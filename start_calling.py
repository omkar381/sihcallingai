#!/usr/bin/env python
"""
One-command launcher for the voice agent.

Starts ngrok, writes the new public URL into .env, starts the API server, and
waits until the webhook is reachable from the internet.

Why this exists: a free ngrok URL changes every restart. When it does, .env
still points at the dead tunnel and calling fails silently - Twilio simply
cannot reach the webhook. That was the single cause of every "calling stopped
working" incident during development, so the fix is automated here rather
than left as a step to remember.

    python start_calling.py            # start everything
    python start_calling.py --stop     # stop everything
    python start_calling.py --status   # report without changing anything

Once it prints READY, place a call with:

    python call.py +918618075133
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
LOG_DIR = ROOT / "output"

PORT = 8001
NGROK_API = "http://127.0.0.1:4040/api/tunnels"

# Where ngrok tends to live on a Windows machine when it was unzipped by hand
# rather than installed.
NGROK_HINTS = [
    "ngrok",
    str(Path.home() / "ngrok.exe"),
    str(Path.home() / "Downloads" / "ngrok.exe"),
    str(Path.home() / "Downloads" / "ngrok-v3-stable-windows-amd64" / "ngrok.exe"),
    r"C:\ngrok\ngrok.exe",
    str(ROOT / "ngrok.exe"),
]

IS_WINDOWS = platform.system() == "Windows"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    print(f"  {msg}", flush=True)


# ---------------------------------------------------------------------------
# ngrok
# ---------------------------------------------------------------------------

def find_ngrok() -> Optional[str]:
    on_path = shutil.which("ngrok")
    if on_path:
        return on_path
    for hint in NGROK_HINTS:
        if hint != "ngrok" and Path(hint).exists():
            return hint
    return None


def tunnel_url() -> Optional[str]:
    """The current public https tunnel, or None when ngrok is not running."""
    try:
        with urllib.request.urlopen(NGROK_API, timeout=4) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None

    for tunnel in data.get("tunnels", []):
        url = tunnel.get("public_url", "")
        if url.startswith("https://"):
            return url
    return None


def start_ngrok() -> Optional[str]:
    existing = tunnel_url()
    if existing:
        step(f"ngrok already running: {existing}")
        return existing

    binary = find_ngrok()
    if not binary:
        say()
        say("  ngrok was not found. Install it, or put ngrok.exe next to this script.")
        say("  Download: https://ngrok.com/download")
        return None

    step(f"starting ngrok ({binary})")
    LOG_DIR.mkdir(exist_ok=True)
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}
    subprocess.Popen(
        [binary, "http", str(PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **flags,
    )

    for _ in range(25):
        time.sleep(2)
        url = tunnel_url()
        if url:
            return url

    say("  ngrok started but never reported a tunnel.")
    say("  If this is a fresh install, add your authtoken first:")
    say("      ngrok config add-authtoken <YOUR_TOKEN>")
    return None


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------

def read_base_url() -> str:
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("BASE_URL="):
            return line.split("=", 1)[1].strip()
    return ""


def write_base_url(url: str) -> bool:
    """Point BASE_URL at the live tunnel. Returns True when it changed."""
    if not ENV_FILE.exists():
        say(f"  .env not found at {ENV_FILE}")
        return False

    text = ENV_FILE.read_text(encoding="utf-8")
    current = read_base_url()
    if current == url:
        step(f"BASE_URL already correct: {url}")
        return False

    if re.search(r"^BASE_URL=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^BASE_URL=.*$", f"BASE_URL={url}", text, flags=re.MULTILINE)
    else:
        text = text.rstrip() + f"\nBASE_URL={url}\n"

    ENV_FILE.write_text(text, encoding="utf-8")
    step(f"BASE_URL updated -> {url}")
    return True


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

def server_healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def stop_server() -> None:
    if IS_WINDOWS:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
             "Where-Object { $_.CommandLine -like '*uvicorn*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
            capture_output=True,
        )
    else:
        subprocess.run(["pkill", "-f", "uvicorn app.main:app"], capture_output=True)


def start_server(restart: bool) -> bool:
    if server_healthy():
        if not restart:
            step("server already healthy")
            return True
        step("restarting server to pick up the new BASE_URL")
        stop_server()
        time.sleep(3)

    step("starting API server")
    LOG_DIR.mkdir(exist_ok=True)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    out = open(LOG_DIR / "server.log", "a", encoding="utf-8")
    err = open(LOG_DIR / "server.err", "a", encoding="utf-8")

    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", str(PORT)],
        cwd=str(ROOT), stdout=out, stderr=err, env=env, **flags,
    )

    # Startup pre-warms caches and initialises tables, so allow a generous window.
    for _ in range(40):
        time.sleep(3)
        if server_healthy():
            return True

    say(f"  server did not become healthy. Check {LOG_DIR / 'server.err'}")
    return False


def webhook_reachable(base_url: str) -> bool:
    """Confirm Twilio could actually reach the voice webhook from outside."""
    req = urllib.request.Request(
        f"{base_url}/twilio/voice",
        data=b"CallSid=CAlaunchcheck&From=%2B10000000000",
        headers={"ngrok-skip-browser-warning": "1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return "<Response>" in resp.read().decode()
    except Exception as exc:
        say(f"  webhook check failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_status() -> int:
    say("Status")
    url = tunnel_url()
    say(f"  ngrok tunnel : {url or 'not running'}")
    say(f"  BASE_URL     : {read_base_url() or 'not set'}")
    say(f"  server       : {'healthy' if server_healthy() else 'not responding'}")

    if url and read_base_url() != url:
        say()
        say("  BASE_URL does not match the live tunnel. Run: python start_calling.py")
        return 1
    return 0


def cmd_stop() -> int:
    say("Stopping")
    stop_server()
    step("server stopped")
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/IM", "ngrok.exe", "/F"], capture_output=True)
    else:
        subprocess.run(["pkill", "ngrok"], capture_output=True)
    step("ngrok stopped")
    return 0


def cmd_start() -> int:
    say("Starting AI Krishi voice agent")
    say()

    url = start_ngrok()
    if not url:
        return 1

    changed = write_base_url(url)

    if not start_server(restart=changed):
        return 1

    step("checking the webhook from the public internet")
    if not webhook_reachable(url):
        say()
        say("  The tunnel is up but Twilio cannot get valid TwiML back.")
        say(f"  Check {LOG_DIR / 'server.err'}")
        return 1

    say()
    say("READY")
    say(f"  Public URL : {url}")
    say(f"  Console    : {url}/console")
    say(f"  API docs   : {url}/docs")
    say()
    say("  Place a call:")
    say("      python call.py +918618075133")
    say()
    say("  Twilio webhook (if you set it in the console):")
    say(f"      {url}/twilio/voice")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the voice agent stack.")
    parser.add_argument("--stop", action="store_true", help="stop ngrok and the server")
    parser.add_argument("--status", action="store_true", help="report state, change nothing")
    args = parser.parse_args()

    if args.stop:
        return cmd_stop()
    if args.status:
        return cmd_status()
    return cmd_start()


if __name__ == "__main__":
    raise SystemExit(main())
