# WCH BLE OTA

通过 Bluetooth Low Energy 对 WCH CH57x、CH58x 等芯片进行 OTA 固件升级，同时提供：

- 浏览器 Web Bluetooth 页面：`WCHWebOTA.html`
- Python 命令行工具：`wchota.py`
- Intel HEX 转地址保持型 BIN 工具：`hex_to_bin.py`
- 通信协议说明：`升级协议.md`

当前协议已在 CH583 设备上验证。

## 工作原理

OTA 使用以下 GATT 接口：

```text
Service:        0000FEE0-0000-1000-8000-00805F9B34FB
Characteristic: 0000FEE1-0000-1000-8000-00805F9B34FB
```

升级流程为：查询设备信息、擦除 Flash、分包写入、分包校验、发送完成命令。

默认固件前 `0x1000` 字节作为引导区保留，实际传输内容从 `0x1000` 开始。BIN 和 HEX 均支持。

## 环境要求

### Web 版本

- 支持 Web Bluetooth 的浏览器，推荐最新版 Chrome 或 Edge
- 页面必须运行在安全上下文：HTTPS 或本机 `localhost`
- macOS、Windows、Linux 的 Web Bluetooth 支持情况取决于浏览器和系统蓝牙权限

### Python 版本

- Python 3.10+
- `bleak`

安装依赖：

```bash
python -m pip install bleak
```

## Web 使用

直接在 Chrome/Edge 中打开 `WCHWebOTA.html`，选择 `.bin` 或 `.hex` 文件，然后：

1. 点击“连接设备”并选择目标设备
2. 查看设备 ImageInfo
3. 点击“开始升级”

也可以通过 URL 自动下载固件：

```text
WCHWebOTA.html?file=https://example.com/firmware/BackupUpgrade_OTA.hex
```

页面会根据文件名后缀自动识别 HEX 或 BIN。远程服务器必须允许跨域访问，例如 Nginx：

```nginx
location /firmware/ {
    add_header Access-Control-Allow-Origin "*" always;
    add_header Access-Control-Allow-Methods "GET, HEAD, OPTIONS" always;
}
```

如果页面使用 HTTPS，固件 URL 也会自动切换为 HTTPS，避免 Mixed Content 拦截。

## Python 使用

### 扫描设备

```bash
python wchota.py scan
```

设备会按 RSSI 信号强度从高到低列出：

```text
Scanning BLE devices (8s)...
1. Simple Peripheral (..., RSSI=-56 dBm)
2. <unnamed> (..., RSSI=-72 dBm)
```

### 交互选择设备并升级

不指定 `--device` 时，程序会显示设备列表并要求输入编号。输入 `q` 或 `0` 可取消升级：

```bash
python wchota.py firmware.hex
```

### 自动匹配设备

设备名称或地址匹配：

```bash
python wchota.py firmware.hex --device "Simple Peripheral"
```

名称前缀过滤：

```bash
python wchota.py firmware.hex --name-prefix Simple
```

组合使用：

```bash
python wchota.py firmware.hex \
    --name-prefix Simple \
    --device Peripheral
```

### 扫描时长和连接超时

```bash
python wchota.py scan --scan-timeout 5
python wchota.py firmware.bin --scan-timeout 5 --timeout 30
```

- `--scan-timeout`：设备扫描最长时间，默认 8 秒
- `--timeout`：BLE 连接超时，默认 20 秒
- 指定 `--device` 时，发现匹配设备后会立即结束扫描

升级成功后会显示总耗时：

```text
OTA complete, elapsed: 42.7s
```

## HEX 转换

Python 工具可以单独将 Intel HEX 转换成 BIN：

```bash
python hex_to_bin.py input.hex output.bin
```

指定起始地址和空洞填充值：

```bash
python hex_to_bin.py input.hex output.bin \
    --start-address 0x1000 \
    --fill 0x00
```

直接使用 `wchota.py` 时不需要手动转换：

```bash
python wchota.py input.hex --device "Simple Peripheral"
```

## 进度显示

Python 工具默认显示三个阶段：

```text
[1/3] ERASE...
[1/3] ERASE OK!
[2/3] FLASH 153280/153280 (100%)
[3/3] VERIFY 153280/153280 (100%)
```

FLASH 和 VERIFY 使用回车符在同一行刷新。将输出重定向到文件时，文件中会保留所有中间进度文本，属于正常现象。

## 协议和限制

详细帧格式、字段定义、时序和响应规则见 [升级协议.md](升级协议.md)。当前实现的主要限制：

- 固件默认从 `0x1000` 开始传输
- 单个应用层数据包最多携带 240 字节固件数据
- 设备必须提供 `FEE0/FEE1`
- 擦除和最终校验结果通过主动读取获取，协议没有 Notify 和逐包 ACK
- 发送完成命令后设备通常会复位并断开 BLE，这是正常行为

## 故障排查

### 找不到设备

```bash
python wchota.py scan --scan-timeout 15
```

确认系统蓝牙已开启、设备正在广播，并尝试使用蓝牙地址作为 `--device` 参数。

### Web 页面提示 `Failed to fetch`

通常是远程服务器没有配置 CORS，或 HTTPS 页面请求了 HTTP 固件地址。检查服务器响应头和浏览器地址栏协议。

### 重定向输出文件很大

进度条使用 `\r` 原地刷新；终端会覆盖同一行，重定向到文件时每次刷新都会被保存。可以使用以下命令转换查看：

```bash
tr '\r' '\n' < a.txt > a-lines.txt
```

## License

本项目采用 [MIT License](LICENSE) 开源。你可以自由使用、修改、复制、发布和商用，但需要在副本中保留版权声明和许可证文本。
