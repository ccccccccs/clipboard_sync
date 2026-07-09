#!/usr/bin/env python3
"""Clipboard Sync Server — monitors Windows clipboard, serves via LAN HTTP."""

import os
import time
import queue
import secrets
import socket
import sqlite3
import threading
from datetime import datetime
import sys
from pathlib import Path

from flask import Flask, render_template, request, jsonify, Response
import pyperclip

import tkinter as tk
# (ttk no longer needed — CTkTextbox replaces Treeview)

# customtkinter GUI (pip install customtkinter)
try:
    import customtkinter as ctk
    _HAS_GUI = True
except ImportError:
    _HAS_GUI = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "clips.db"
TEMPLATE_DIR = BASE_DIR / "templates"
TOKEN_FILE = BASE_DIR / "token.txt"
PORT_FILE = BASE_DIR / "port.txt"
MAX_CLIPS = int(os.environ.get("CLIPBOARD_MAX_CLIPS", 1000))

def _load_port():
    """Load port: env var > port.txt > default 5000."""
    p = os.environ.get("CLIPBOARD_PORT")
    if p:
        return int(p)
    if PORT_FILE.exists():
        return int(PORT_FILE.read_text().strip())
    return 5000

def _load_token():
    """Load token: env var > token.txt > default password. Always persist."""
    token = os.environ.get("CLIPBOARD_PASSWORD") or os.environ.get("CLIPBOARD_TOKEN")
    if token:
        return token
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    default = "admin"
    TOKEN_FILE.write_text(default)
    return default

PORT = _load_port()
TOKEN = _load_token()

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def require_auth():
    t = request.args.get("token") or request.headers.get("X-Token")
    if t != TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    return None

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clips (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            content     TEXT NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def _cleanup_old_clips(conn):
    """Keep only the newest MAX_CLIPS records; delete the rest."""
    # ponytail: single query, no count-then-delete
    conn.execute(
        "DELETE FROM clips WHERE id NOT IN (SELECT id FROM clips ORDER BY id DESC LIMIT ?)",
        (MAX_CLIPS,),
    )

# ---------------------------------------------------------------------------
# SSE broadcast
# ---------------------------------------------------------------------------
_sse_queues: set[queue.Queue] = set()

def sse_broadcast(event: str, data: dict):
    global _sse_queues
    dead = set()
    for q in _sse_queues:
        try:
            q.put_nowait((event, data))
        except queue.Full:
            dead.add(q)
    _sse_queues -= dead

# ---------------------------------------------------------------------------
# Clipboard monitor (background thread)
# ---------------------------------------------------------------------------
_clip_lock = threading.Lock()
_last_clip = ""

def _monitor():
    global _last_clip
    while True:
        try:
            content = pyperclip.paste()
            with _clip_lock:
                if content and content != _last_clip:
                    _last_clip = content
                else:
                    content = None  # skip
            if content:
                conn = get_db()
                conn.execute("INSERT INTO clips (content) VALUES (?)", (content,))
                _cleanup_old_clips(conn)
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM clips WHERE id = last_insert_rowid()"
                ).fetchone()
                conn.close()
                sse_broadcast("new", dict(row))
        except Exception:
            pass
        time.sleep(1)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    token = request.args.get("token") or request.headers.get("X-Token")
    if token != TOKEN:
        return _access_denied()
    return render_template("index.html", token=TOKEN)


def _access_denied():
    return (
        '<html lang="zh"><head><meta charset="UTF-8"><title>访问被拒绝</title></head>'
        '<body style="font-family:sans-serif;max-width:500px;margin:60px auto;padding:20px;color:#333">'
        '<h2 style="color:#e05555">访问被拒绝</h2>'
        '<p>请在浏览器地址栏输入 <code>http://IP地址:5000/<b>密码</b></code> 来访问</p>'
        '<p>例如: <code>http://10.118.81.105:5000/admin</code></p>'
        '<p style="color:#888;font-size:0.85em">也可使用 <code>?token=密码</code> 参数方式</p>'
        '</body></html>'
    ), 401

@app.route("/api/clips")
def list_clips():
    auth = require_auth()
    if auth:
        return auth
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM clips ORDER BY id DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/clips", methods=["POST"])
def create_clip():
    auth = require_auth()
    if auth:
        return auth
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "empty content"}), 400

    conn = get_db()
    conn.execute("INSERT INTO clips (content) VALUES (?)", (content,))
    _cleanup_old_clips(conn)
    conn.commit()
    row = conn.execute(
        "SELECT * FROM clips WHERE id = last_insert_rowid()"
    ).fetchone()
    conn.close()

    # Set local Windows clipboard (hold lock so monitor skips this write)
    with _clip_lock:
        global _last_clip
        _last_clip = content
        try:
            pyperclip.copy(content)
        except Exception:
            pass

    sse_broadcast("new", dict(row))
    return jsonify(dict(row)), 201

@app.route("/api/clips/<int:clip_id>", methods=["DELETE"])
def delete_clip(clip_id):
    auth = require_auth()
    if auth:
        return auth
    conn = get_db()
    conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    conn.commit()
    conn.close()
    return "", 204

@app.route("/api/stream")
def stream():
    auth = require_auth()
    if auth:
        return auth
    q: queue.Queue = queue.Queue(maxsize=50)
    _sse_queues.add(q)

    def generate():
        try:
            while True:
                try:
                    event, data = q.get(timeout=30)
                    import json
                    yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            _sse_queues.discard(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})

# Catch-all: path-based token access — http://ip:port/<password>
@app.route("/<path:path>")
def path_token(path):
    if path == TOKEN:
        return render_template("index.html", token=TOKEN)
    return _access_denied()

# ---------------------------------------------------------------------------
# GUI window (customtkinter)
# ---------------------------------------------------------------------------
def _run_gui():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("Clipboard Sync Server")
    root.geometry("560x500")
    root.minsize(420, 320)
    root.attributes("-topmost", True)

    main = ctk.CTkFrame(root)
    main.pack(fill="both", expand=True, padx=12, pady=12)

    # URL header: clickable link + copy button
    header = ctk.CTkFrame(main, fg_color="transparent")
    header.pack(fill="x", pady=(10, 2))
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    _lan_url = f"http://{local_ip}:{PORT}/{TOKEN}"

    url_box = ctk.CTkFrame(header, fg_color="#2a2a2a", corner_radius=8)
    url_box.pack(side="left")
    url_inner = ctk.CTkLabel(url_box, text="\U0001f517  " + _lan_url,
                              font=ctk.CTkFont(size=12), text_color="#dddddd",
                              cursor="hand2")
    url_inner.pack(padx=10, pady=5)

    def _copy_url():
        root.clipboard_clear()
        root.clipboard_append(_lan_url)
        url_inner.configure(text="\u2714  \u5df2\u590d\u5236")
        root.after(1500, lambda: url_inner.configure(text="\U0001f517  " + _lan_url))

    url_box.bind("<Button-1>", lambda e: _copy_url())
    url_inner.bind("<Button-1>", lambda e: _copy_url())

    settings_btn = ctk.CTkButton(header, text="\u2699 \u8bbe\u7f6e", width=56, height=24,
                                  font=ctk.CTkFont(size=10),
                                  fg_color="transparent", text_color="#888888",
                                  hover_color="#2a2a2a", corner_radius=4)
    settings_btn.pack(side="right")

    # Collapsible settings panel
    settings_frame = ctk.CTkFrame(main, fg_color="#141414", corner_radius=6)
    _settings_open = False

    def _toggle_settings():
        nonlocal _settings_open
        if _settings_open:
            settings_frame.pack_forget()
            _settings_open = False
        else:
            settings_frame.pack(fill="x", pady=(4, 6), after=header)
            _settings_open = True

    settings_btn.configure(command=_toggle_settings)

    pw_inner = ctk.CTkFrame(settings_frame, fg_color="transparent")
    pw_inner.pack(fill="x", padx=10, pady=(8, 4))
    ctk.CTkLabel(pw_inner, text="\u8bbf\u95ee\u5bc6\u7801:", font=ctk.CTkFont(size=11)).pack(side="left")
    pw_var = ctk.StringVar(value=TOKEN)
    ctk.CTkEntry(pw_inner, textvariable=pw_var, width=140, font=ctk.CTkFont(size=11)).pack(side="left", padx=(6, 6))

    def _save_password():
        new_pw = pw_var.get().strip()
        if not new_pw or len(new_pw) < 2:
            return
        global TOKEN
        TOKEN = new_pw
        TOKEN_FILE.write_text(new_pw)

    ctk.CTkButton(pw_inner, text="\u4fee\u6539", width=50, height=24, font=ctk.CTkFont(size=10),
                  command=_save_password).pack(side="left")

    top_inner = ctk.CTkFrame(settings_frame, fg_color="transparent")
    top_inner.pack(fill="x", padx=10, pady=(0, 8))
    top_var = ctk.BooleanVar(value=True)
    ctk.CTkCheckBox(top_inner, text="\u7a97\u53e3\u7f6e\u9876", variable=top_var,
                     command=lambda: root.attributes("-topmost", top_var.get()),
                     font=ctk.CTkFont(size=11)).pack(side="left")

    # Port setting (requires restart)
    port_inner = ctk.CTkFrame(settings_frame, fg_color="transparent")
    port_inner.pack(fill="x", padx=10, pady=(0, 4))
    ctk.CTkLabel(port_inner, text="\u7aef\u53e3:", font=ctk.CTkFont(size=11)).pack(side="left")
    port_var = ctk.StringVar(value=str(PORT))
    ctk.CTkEntry(port_inner, textvariable=port_var, width=60, font=ctk.CTkFont(size=11)).pack(side="left", padx=(6, 6))

    def _save_port():
        nonlocal _lan_url
        try:
            new_port = int(port_var.get().strip())
            if new_port < 1 or new_port > 65535:
                return
            global PORT
            PORT = new_port
            PORT_FILE.write_text(str(new_port))
            _lan_url = f"http://{local_ip}:{PORT}/{TOKEN}"
            url_inner.configure(text="\U0001f517  " + _lan_url)
        except ValueError:
            pass

    ctk.CTkButton(port_inner, text="\u4fee\u6539", width=50, height=24, font=ctk.CTkFont(size=10),
                  command=_save_port).pack(side="left")
    ctk.CTkLabel(port_inner, text="(\u9700\u91cd\u542f\u751f\u6548)", font=ctk.CTkFont(size=10),
                 text_color="#888888").pack(side="left", padx=(6, 0))

    # Divider
    div = ctk.CTkFrame(main, height=2, fg_color="#404040")
    div.pack(fill="x", pady=10)

    # Search bar
    search_frame = ctk.CTkFrame(main, fg_color="transparent")
    search_frame.pack(fill="x", pady=(0, 8))
    ctk.CTkLabel(search_frame, text="\u641c\u7d22:", font=ctk.CTkFont(size=11),
                 text_color="#888888").pack(side="left", padx=(0, 6))
    search_var = ctk.StringVar()
    search_entry = ctk.CTkEntry(search_frame, textvariable=search_var,
                                 placeholder_text="\u8f93\u5165\u5173\u952e\u5b57\u67e5\u627e",
                                 font=ctk.CTkFont(size=11))
    search_entry.pack(side="left", fill="x", expand=True)
    search_var.trace_add("write", lambda *_: _schedule_refresh(300))

    # --- Clip list: single CTkTextbox (selectable text, Ctrl+A, double-click popup) ---
    clip_text = ctk.CTkTextbox(main, font=ctk.CTkFont(size=12), wrap="word",
                               fg_color="#0a0a0a", corner_radius=6)
    clip_text.pack(fill="both", expand=True)
    clip_text.configure(state="disabled")

    # Status bar
    status_frame = ctk.CTkFrame(main, fg_color="transparent")
    status_frame.pack(fill="x", pady=(8, 0))
    status_label = ctk.CTkLabel(status_frame, text="\u670d\u52a1\u8fd0\u884c\u4e2d", text_color="#4caf50",
                                 font=ctk.CTkFont(size=11))
    status_label.pack(side="left")
    count_label = ctk.CTkLabel(status_frame, text="", text_color="#888888",
                                font=ctk.CTkFont(size=11))
    count_label.pack(side="right")

    _clip_data = []
    _last_hash = ""
    _pending_refresh = None

    def _schedule_refresh(delay=1000):
        nonlocal _pending_refresh
        if _pending_refresh:
            root.after_cancel(_pending_refresh)
        _pending_refresh = root.after(delay, _refresh)

    def _popup_detail(clip):
        """Double-click popup: full read-only content view."""
        popup = ctk.CTkToplevel(root)
        popup.title(clip["ts"])
        popup.geometry("500x380")
        popup.minsize(300, 200)
        popup.attributes("-topmost", True)
        popup.transient(root)
        popup.after(50, popup.focus_force)

        txt = ctk.CTkTextbox(popup, font=ctk.CTkFont(size=13), wrap="word",
                             fg_color="#0a0a0a", corner_radius=6)
        txt.insert("1.0", clip["content"])
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True, padx=12, pady=12)


    _click1 = None

    def _on_click(event):
        nonlocal _click1
        _click1 = (event.x, event.y)

    def _on_dbl_click(event):
        nonlocal _click1
        # ponytail: ignore double-clicks where the two clicks are far apart (accidental)
        if _click1 and (abs(event.x - _click1[0]) > 10 or abs(event.y - _click1[1]) > 10):
            _click1 = None
            return
        _click1 = None
        idx = clip_text.index(f"@{event.x},{event.y}")
        tags = clip_text.tag_names(idx)
        for tag in tags:
            if tag.startswith("r_"):
                i = int(tag.split("_")[1])
                if 0 <= i < len(_clip_data):
                    clip = _clip_data[i]
                    if len(clip["content"]) <= 200:
                        clip_text.tag_add("sel", "1.0", "end")
                        clip_text.focus_set()
                    else:
                        _popup_detail(clip)
                break

    def _on_right_click(event):
        idx = clip_text.index(f"@{event.x},{event.y}")
        tags = clip_text.tag_names(idx)
        for tag in tags:
            if tag.startswith("r_"):
                i = int(tag.split("_")[1])
                if 0 <= i < len(_clip_data):
                    _right_target = _clip_data[i]
                    menu = tk.Menu(clip_text, tearoff=0, bd=0,
                                   bg="#1a1a1a", fg="#cccccc",
                                   activebackground="#2a4a6a", activeforeground="#ffffff",
                                   font=("Segoe UI", 10))
                    menu.add_command(label="\u590d\u5236",
                                     command=lambda c=_right_target: (
                                         root.clipboard_clear(), root.clipboard_append(c["content"])))
                    menu.add_command(label="\u5220\u9664",
                                     command=lambda c=_right_target: _do_delete_menu(c))
                    menu.post(event.x_root, event.y_root)
                break

    def _do_delete_menu(clip):
        try:
            conn = get_db()
            conn.execute("DELETE FROM clips WHERE id = ?", (clip["id"],))
            conn.commit()
            conn.close()
            _schedule_refresh(200)
        except Exception:
            pass

    clip_text.bind("<Button-1>", _on_click)
    clip_text.bind("<Double-1>", _on_dbl_click)
    clip_text.bind("<Button-3>", _on_right_click)

    def _rebuild_text():
        clip_text.configure(state="normal")
        clip_text.delete("1.0", "end")
        # Unbind old tags
        for i in range(len(_clip_data) + 20):
            for t in (f"r_{i}",):
                try: clip_text.tag_unbind(t, "<Double-1>")
                except: pass

        keyword = search_var.get().strip().lower()
        sep_line = "\u2500" * 36 + "\n"
        for i, c in enumerate(_clip_data):
            if keyword and keyword not in c["content"].lower():
                continue
            preview = c["content"][:200].replace("\n", " ")
            truncated = len(c["content"]) > 200
            tag = f"r_{i}"
            line = f"[{c['ts']}]  {preview}"
            if truncated:
                line += " \u2026"
            line += "\n"
            clip_text.insert("end", line, (tag,))
            clip_text.insert("end", sep_line, ("sep",))

        clip_text.tag_config("sep", foreground="#222222")
        clip_text.configure(state="disabled")

    def _refresh():
        nonlocal _last_hash
        try:
            conn = get_db()
            count = conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
            rows = conn.execute(
                "SELECT id, content, created_at FROM clips ORDER BY id DESC LIMIT 20"
            ).fetchall()
            conn.close()
            count_label.configure(text=f"\u5171 {count} \u6761\u8bb0\u5f55")

            keyword = search_var.get().strip().lower()
            new_hash = f"{keyword}|" + "|".join(r["content"][:30] for r in rows)
            if new_hash == _last_hash:
                _schedule_refresh()
                return
            _last_hash = new_hash

            _clip_data.clear()
            for r in rows:
                _clip_data.append({
                    "id": r["id"],
                    "content": r["content"],
                    "ts": r["created_at"] or "",
                })
            _rebuild_text()
        except Exception:
            pass
        _schedule_refresh()

    _schedule_refresh(500)
    root.mainloop()

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
def _print_banner():
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print(f"""
{'=' * 54}
  Clipboard Sync Server
  Token: {TOKEN}
  Local:   http://127.0.0.1:{PORT}/{TOKEN}
  Network: http://{local_ip}:{PORT}/{TOKEN}
{'=' * 54}
""")


if __name__ == "__main__":
    init_db()
    threading.Thread(target=_monitor, daemon=True).start()
    _print_banner()

    use_gui = _HAS_GUI and "--no-gui" not in sys.argv
    if use_gui:
        threading.Thread(
            target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True),
            daemon=True,
        ).start()
        try:
            _run_gui()
        except tk.TclError:
            print("[warn] GUI failed (no display?), running headless...")
            app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
    else:
        app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
