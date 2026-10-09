#!/usr/bin/env python3
"""Manage only this project's local processes, never kill a port owner implicitly."""

import argparse, json, os, signal, socket, subprocess, sys, time, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".runtime"
STATE.mkdir(exist_ok=True)
FILE = STATE / "services.json"
SPECS = {
    "api": (
        8186,
        [
            str(ROOT / "backend/.venv/bin/python"),
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            str(ROOT / "backend"),
            "--host",
            "127.0.0.1",
            "--port",
            "8186",
            "--no-access-log",
        ],
    ),
    "web": (
        5186,
        [
            shutil.which("node") or "node",
            str(ROOT / "frontend/node_modules/vite/bin/vite.js"),
            "--host",
            "127.0.0.1",
            "--port",
            "5186",
            "--strictPort",
        ],
    ),
    "mobile": (
        5187,
        [
            shutil.which("node") or "node",
            str(ROOT / "mobile/node_modules/vite/bin/vite.js"),
            "--host",
            "127.0.0.1",
            "--port",
            "5187",
            "--strictPort",
        ],
    ),
}


def occupied(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def ours(pid):
    p = subprocess.run(
        ["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True
    )
    return p.returncode == 0 and str(ROOT) in p.stdout


def start():
    saved = json.loads(FILE.read_text()) if FILE.exists() else {}
    for name, (port, cmd) in SPECS.items():
        if occupied(port):
            if name in saved and ours(saved[name]):
                continue
            raise SystemExit(
                f"Port {port} is occupied by a process not managed by this script. No process was stopped."
            )
        cwd = ROOT / (
            "backend" if name == "api" else "frontend" if name == "web" else "mobile"
        )
        with (STATE / (name + ".log")).open("a") as log:
            process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        saved[name] = process.pid
        FILE.write_text(json.dumps(saved, indent=2))
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if all(occupied(port) for port, _ in SPECS.values()):
            break
        time.sleep(0.25)
    for name, (port, _) in SPECS.items():
        if not occupied(port):
            raise SystemExit(f"{name} failed to start. See {STATE / (name + '.log')}")
    print(
        "管理端 http://127.0.0.1:5186/\n手机端 http://127.0.0.1:5187/mobile/\nAPI 文档 http://127.0.0.1:8186/docs"
    )


def stop():
    saved = json.loads(FILE.read_text()) if FILE.exists() else {}
    for name, pid in saved.items():
        if ours(pid):
            os.killpg(pid, signal.SIGTERM)
            print("Stopping", name)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and any(ours(pid) for pid in saved.values()):
        time.sleep(0.1)
    remaining = {name: pid for name, pid in saved.items() if ours(pid)}
    if remaining:
        FILE.write_text(json.dumps(remaining, indent=2))
        raise SystemExit("Services are still stopping; their process records were preserved. Retry stop shortly.")
    FILE.unlink(missing_ok=True)


def status():
    for name, (port, _) in SPECS.items():
        print(name, port, "listening" if occupied(port) else "stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status"])
    args = parser.parse_args()
    {"start": start, "stop": stop, "status": status}[args.action]()
