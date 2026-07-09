# Clipboard Sync Server

A lightweight **LAN clipboard sync server** for Windows. Monitors the local clipboard in real-time and provides a web UI for viewing, searching, copying, and pasting clipboard content across devices on the same network.

> **Windows 局域网剪贴板同步服务器** — 实时监控本机剪贴板，在局域网内通过 Web 页面查看、搜索、复制和推送剪贴板内容。

---

## Features / 功能特性

- **Real-time clipboard monitoring** — Automatically captures clipboard changes on the server machine
- **Web UI** — Modern dark-themed interface with search, expand, copy, and delete
- **Desktop GUI** — Built-in customtkinter control panel showing server status and URL
- **SSE push** — Server-Sent Events for instant updates to all connected web clients
- **Push to clipboard** — Send text from any device's browser to the server's clipboard
- **Auth protection** — Token/password based access control
- **Portable exe** — Can be compiled into a standalone Windows executable via PyInstaller

---

## Quick Start / 快速开始

### Prerequisites / 前置条件

- Python 3.10+
- Windows (clipboard monitoring uses `pyperclip` which works best on Windows)

### Install / 安装

```bash
pip install -r requirements.txt
```

### Run / 运行

```bash
python server.py
```

The server starts at `http://0.0.0.0:5000`. Open the displayed URL in your browser.

---

## Configuration / 配置

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `CLIPBOARD_PORT` | `5000` | Server port |
| `CLIPBOARD_MAX_CLIPS` | `1000` | Maximum stored clips |
| `CLIPBOARD_PASSWORD` / `CLIPBOARD_TOKEN` | `admin` | Access password |

If no environment variables are set, the default password is `admin`, stored in `token.txt`.

---

## API

All endpoints require authentication via `?token=<password>` or `X-Token` header.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/clips` | List recent clips (max 200) |
| `POST` | `/api/clips` | Push text to server clipboard |
| `DELETE` | `/api/clips/<id>` | Delete a clip |
| `GET` | `/api/stream` | SSE event stream for real-time updates |

### POST example

```bash
curl -X POST "http://192.168.1.100:5000/api/clips?token=admin" \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello from browser!"}'
```

---

## Build Windows Executable / 打包为 Windows 可执行文件

### One-step build

```bash
# Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install pyinstaller pillow

# Generate icon
python make_icon.py

# Build (onedir mode — fast startup)
pyinstaller --onedir --windowed --icon=clipboard.ico ^
  --add-data "templates;templates" ^
  --hidden-import customtkinter ^
  --exclude-module matplotlib numpy pandas PIL scipy ^
  --clean --noconfirm --name "ClipboardSync" server.py
```

Output: `dist/ClipboardSync/ClipboardSync.exe`

> Use `--onefile` instead of `--onedir` for a single exe (slower startup due to self-extraction).

---

## Project Structure / 项目结构

```
clipboard-server/
├── server.py            # Main entry point (CLI + GUI)
├── templates/
│   └── index.html       # Web UI template
├── requirements.txt     # Python dependencies
├── make_icon.py         # Icon generator (requires Pillow)
├── clipboard.ico        # Pre-built application icon
├── clips.db             # SQLite database (created at runtime)
├── token.txt            # Auth token (created at runtime)
├── .gitignore
├── LICENSE
└── README.md
```

---

## License / 许可

[MIT](LICENSE)
