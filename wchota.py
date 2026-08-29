#!/usr/bin/env python3
"""Upload a WCH BLE OTA image using bleak.

The wire protocol mirrors WCHWebOTA.html: service 0xFEE0, characteristic
0xFEE1, ImageInfo (84 12), erase (81 00 ...), write (80 ...), verify (82 ...),
and finish (83 12 followed by 18 zero bytes). Addresses are encoded as
address / 16, little endian. By default the first 0x1000 bytes of a .bin are
the bootloader area and are not transferred.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from pathlib import Path
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from hex_to_bin import parse_hex

SERVICE_UUID = "0000fee0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000fee1-0000-1000-8000-00805f9b34fb"
START_ADDR = 0x1000
DEFAULT_MTU = 247
DEFAULT_SCAN_TIMEOUT = 8.0


def load_image(path: Path) -> bytes:
    """Load a raw BIN, or convert an Intel HEX image in memory."""
    if path.suffix.lower() not in (".hex", ".ihex"):
        return path.read_bytes()
    memory = parse_hex(path)
    image = bytearray(max(memory) + 1)
    for address, value in memory.items():
        image[address] = value
    return bytes(image)


def chip_name(data: bytes) -> str:
    ids = {(0x73, 0): "CH573", (0x79, 0): "CH579", (0x83, 0): "CH583",
           (8, 2): "CH32V208", (8, 0xF2): "CH32F208"}
    return ids.get((data[7], data[8]), "unknown") if len(data) > 8 else "unknown"


async def discover_devices(timeout: float = DEFAULT_SCAN_TIMEOUT):
    print(f"Scanning BLE devices ({timeout:g}s)...", flush=True)
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    devices = [(device, getattr(advertisement, "rssi", None))
               for device, advertisement in discovered.values()]
    return sorted(devices, key=lambda item: (
        item[1] is None,
        -(item[1] if item[1] is not None else 0),
    ))


async def find_device(selector: str, timeout: float, name_prefix: str | None = None):
    print(f"Scanning BLE devices ({timeout:g}s)...", flush=True)
    selector_l = selector.lower()
    prefix_l = name_prefix.lower() if name_prefix else None
    def matches(device, advertisement):
        name = device.name or advertisement.local_name or ""
        return ((prefix_l is None or name.lower().startswith(prefix_l)) and
                selector_l in f"{name} {device.address or ''}".lower())

    device = await BleakScanner.find_device_by_filter(matches, timeout=timeout)
    print("Matching target device...", flush=True)
    if device is None:
        raise RuntimeError(f"no BLE device matched {selector!r} within {timeout:g} seconds")
    return device


def print_devices(devices):
    for index, (device, rssi) in enumerate(devices, 1):
        signal = f", RSSI={rssi} dBm" if rssi is not None else ""
        print(f"{index}. {device.name or '<unnamed>'} ({device.address}{signal})")


async def select_device(timeout: float, name_prefix: str | None = None):
    devices = await discover_devices(timeout)
    if name_prefix:
        prefix_l = name_prefix.lower()
        devices = [(device, rssi) for device, rssi in devices
                   if (device.name or "").lower().startswith(prefix_l)]
    if not devices:
        raise RuntimeError(f"no BLE devices found within {timeout:g} seconds")
    print_devices(devices)
    while True:
        try:
            value = input("Select device number (q/0 to cancel): ").strip().lower()
            if value in ("q", "0"):
                print("Upgrade cancelled.")
                raise SystemExit(0)
            choice = int(value)
            if 1 <= choice <= len(devices):
                return devices[choice - 1][0]
        except ValueError:
            pass
        print(f"Please enter a number from 1 to {len(devices)}.")


async def scan_command(timeout: float, name_prefix: str | None = None):
    devices = await discover_devices(timeout)
    if name_prefix:
        prefix_l = name_prefix.lower()
        devices = [(device, rssi) for device, rssi in devices
                   if (device.name or "").lower().startswith(prefix_l)]
    if devices:
        print_devices(devices)
    else:
        print("No BLE devices found.")


async def read_after(client: BleakClient, char, delay: float) -> bytes:
    await asyncio.sleep(delay)
    return bytes(await client.read_gatt_char(char))


async def upload(device, image: bytes, start: int, timeout: float) -> None:
    if start < 0 or start % 16:
        raise ValueError("start address must be a non-negative multiple of 16")
    async with BleakClient(device, timeout=timeout) as client:
        char = client.services.get_characteristic(CHAR_UUID)
        if char is None:
            raise RuntimeError("device does not expose FEE1 characteristic")
        props = set(char.properties)
        # Web Bluetooth writeValue uses the acknowledged write when available.
        response = "write" in props
        async def write(payload: bytes, *, command: bool = False):
            await client.write_gatt_char(char, payload, response=(response if not command else response))

        await write(b"\x84\x12", command=True)
        info = await read_after(client, char, 0.5)
        if len(info) < 7:
            raise RuntimeError(f"short ImageInfo response ({len(info)} bytes): {info.hex(' ')}")
        max_size = int.from_bytes(info[1:5], "little")
        block_size = int.from_bytes(info[5:7], "little")
        print(f"ImageInfo: chip={chip_name(info)}, image={'A' if info[0] == 1 else 'B' if info[0] == 2 else '?'}, "
              f"max={max_size}, block={block_size}")
        payload = image[start:]
        if not payload:
            raise ValueError(f"image is shorter than start address 0x{start:X}")
        if max_size and len(payload) > max_size:
            raise ValueError(f"image payload {len(payload)} exceeds device maximum {max_size}")
        block_size = block_size or 256
        blocks = math.ceil(len(payload) / block_size)
        if blocks > 0xFFFF:
            raise ValueError(f"erase requires {blocks} blocks; protocol supports at most 65535")
        erase = bytes((0x81, 0, (start // 16) & 0xFF, (start // 16 >> 8) & 0xFF,
                       blocks & 0xFF, (blocks >> 8) & 0xFF))
        print("[1/3] ERASE...", end="", flush=True)
        await write(erase, command=True)
        # The WCH web implementation waits 9 * 500 ms for flash erase.
        erase_reply = await read_after(client, char, 4.5)
        if not erase_reply or erase_reply[0] != 0:
            print()
            raise RuntimeError(f"erase failed: {erase_reply.hex(' ')}")
        sys.stdout.write("\r[1/3] ERASE OK!\n")
        sys.stdout.flush()
        packet_size = min(DEFAULT_MTU - 3 - 4, 255)
        total = len(payload)
        for opcode in (0x80, 0x82):
            step = "FLASH" if opcode == 0x80 else "VERIFY"
            step_no = 2 if opcode == 0x80 else 3
            sys.stdout.write(f"[{step_no}/3] {step} 0/{total} (0%)")
            sys.stdout.flush()
            for offset in range(0, total, packet_size):
                chunk = payload[offset:offset + packet_size]
                addr = start + offset
                packet = bytes((opcode, len(chunk), (addr // 16) & 0xFF, (addr // 16 >> 8) & 0xFF)) + chunk
                await write(packet)
                sent = offset + len(chunk)
                percent = sent * 100 // total
                sys.stdout.write(f"\r[{step_no}/3] {step} {sent}/{total} ({percent}%)")
                sys.stdout.flush()
            print()
        verify_reply = await read_after(client, char, 2.0)
        if not verify_reply or verify_reply[0] != 0:
            raise RuntimeError(f"verify failed: {verify_reply.hex(' ')}")
        try:
            await write(bytes((0x83, 18)) + bytes(18), command=True)
        except BleakError as exc:
            # WCH bootloaders commonly reset immediately after accepting 0x83.
            if "disconnect" not in str(exc).lower():
                raise


async def run(args: argparse.Namespace) -> None:
    if args.image.suffix.lower() in (".hex", ".ihex"):
        print(f"Converting Intel HEX: {args.image}", flush=True)
    image = load_image(args.image)
    device = (await find_device(args.device, args.scan_timeout, args.name_prefix)
              if args.device else await select_device(args.scan_timeout, args.name_prefix))
    print(f"Connecting to {device.name or '<unnamed>'} ({device.address})")
    started = time.perf_counter()
    await upload(device, image, args.start_address, args.timeout)
    print(f"OTA complete, elapsed: {time.perf_counter() - started:.1f}s")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("image", type=Path, nargs="?", help=".bin image or Intel HEX file (bootloader prefix is preserved)")
    p.add_argument("--device", help="BLE name or address substring")
    p.add_argument("--start-address", type=lambda x: int(x, 0), default=START_ADDR)
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--scan-timeout", type=float, default=DEFAULT_SCAN_TIMEOUT,
                   help="maximum BLE scan time in seconds (default: 8)")
    p.add_argument("--name-prefix", help="only use devices whose name starts with this prefix")
    args = p.parse_args()
    if args.scan_timeout <= 0:
        p.error("--scan-timeout must be greater than zero")
    if len(sys.argv) > 1 and sys.argv[1].lower() == "scan":
        asyncio.run(scan_command(args.scan_timeout, args.name_prefix))
        return 0
    if args.image is None:
        p.error("the following arguments are required: image")
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
