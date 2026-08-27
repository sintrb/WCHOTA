#!/usr/bin/env python3
"""Convert an Intel HEX image to an address-preserving OTA BIN image.

Unlike ``objcopy -O binary``, this converter emits the bytes from the
requested start address, preserving leading and internal address holes.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def parse_hex(path: Path) -> dict[int, int]:
    memory: dict[int, int] = {}
    upper = 0

    for line_number, raw_line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ValueError(f"line {line_number}: missing ':'")
        try:
            record = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise ValueError(f"line {line_number}: invalid hexadecimal data") from exc
        if len(record) < 5:
            raise ValueError(f"line {line_number}: record is too short")

        count, address, record_type = record[0], int.from_bytes(record[1:3], "big"), record[3]
        if len(record) != count + 5:
            raise ValueError(f"line {line_number}: byte count does not match record length")
        if sum(record) & 0xFF:
            raise ValueError(f"line {line_number}: checksum mismatch")

        data = record[4 : 4 + count]
        if record_type == 0x00:  # Data record
            base = upper + address
            for offset, value in enumerate(data):
                absolute = base + offset
                if absolute in memory and memory[absolute] != value:
                    raise ValueError(f"line {line_number}: conflicting data at 0x{absolute:X}")
                memory[absolute] = value
        elif record_type == 0x01:  # End-of-file
            break
        elif record_type == 0x02:  # Extended segment address
            if count != 2:
                raise ValueError(f"line {line_number}: invalid segment address record")
            upper = int.from_bytes(data, "big") << 4
        elif record_type == 0x04:  # Extended linear address
            if count != 2:
                raise ValueError(f"line {line_number}: invalid linear address record")
            upper = int.from_bytes(data, "big") << 16
        elif record_type in (0x03, 0x05):  # Start-address records, not data
            continue
        else:
            raise ValueError(f"line {line_number}: unsupported record type 0x{record_type:02X}")

    if not memory:
        raise ValueError("HEX file contains no data records")
    return memory


def convert(input_path: Path, output_path: Path, start: int, fill: int) -> None:
    if start < 0:
        raise ValueError("start address must be non-negative")
    memory = parse_hex(input_path)
    end = max(memory) + 1
    if start > end:
        raise ValueError(f"start address 0x{start:X} is after image end 0x{end:X}")

    image = bytearray([fill]) * (end - start)
    for address, value in memory.items():
        if address < start:
            raise ValueError(
                f"image contains data at 0x{address:X}, below start address 0x{start:X}"
            )
        image[address - start] = value

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="input Intel HEX file")
    parser.add_argument("output", type=Path, help="output binary file")
    parser.add_argument(
        "--start-address",
        type=lambda value: int(value, 0),
        default=0,
        help="first address emitted (default: 0, preserving leading holes)",
    )
    parser.add_argument(
        "--fill",
        type=lambda value: int(value, 0),
        default=0,
        help="fill byte for address holes (default: 0)",
    )
    args = parser.parse_args()
    if not 0 <= args.fill <= 0xFF:
        parser.error("--fill must be between 0 and 255")
    try:
        convert(args.input, args.output, args.start_address, args.fill)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
