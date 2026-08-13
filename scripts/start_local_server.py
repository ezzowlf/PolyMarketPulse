"""Canonical Windows-friendly local server launcher.

Normalizes only the child process environment, because Windows treats
environment keys case-insensitively while some PowerShell hosts can expose
both ``Path`` and ``PATH``.  It never changes user or machine environment
variables.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def normalized_child_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Return one deterministic spelling for each case-insensitive key."""
    normalized: dict[str, tuple[str, str]] = {}
    for key, value in (source or dict(os.environ)).items():
        folded = key.casefold()
        # Prefer PATH's conventional spelling when both variants exist.
        if folded not in normalized or key == "PATH":
            normalized[folded] = ("PATH" if folded == "path" else key, value)
    return {key: value for key, value in normalized.values()}


def port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex((host, port)) == 0


def wait_for_health(url: str, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Start PolymarketPulse locally and wait for /health.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--health-timeout", type=float, default=15.0)
    args = parser.parse_args()
    host = "127.0.0.1"
    if port_is_open(host, args.port):
        print(f"Port {args.port} is already in use; refusing to start a second server.", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parent.parent
    environment = normalized_child_environment()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(root / "src") if not existing_pythonpath else f"{root / 'src'}{os.pathsep}{existing_pythonpath}"
    )
    command = [sys.executable, "-m", "polymarketpulse.cli", "serve", "--port", str(args.port)]
    # Detach inherited console streams: otherwise a background uvicorn
    # process can keep a PowerShell/CI caller waiting after this launcher
    # has completed its health check.
    process = subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    health_url = f"http://{host}:{args.port}/health"
    if wait_for_health(health_url, args.health_timeout):
        print(f"PolymarketPulse is ready at {health_url} (pid {process.pid}).")
        return 0
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
    print("Server did not become healthy before timeout.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
