"""Keep SSH Modal Sandbox available for ~TARGET_DAYS by recreating before 24h timeout.

Schedule every ~20 hours (Windows Task Scheduler). Volume /mnt/data persists.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import modal

import server_ssh as ssh

STATE_FILE = Path(__file__).resolve().parent / "keepalive_state.json"
TARGET_DAYS = int(os.environ.get("TARGET_DAYS", "7"))
RECREATE_AFTER_HOURS = float(os.environ.get("RECREATE_AFTER_HOURS", "20"))


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def ensure_running() -> None:
    state = load_state()
    now = datetime.now(timezone.utc)
    if not state.get("campaign_started_at"):
        state["campaign_started_at"] = now.isoformat()
        state["recreates"] = 0
        save_state(state)

    started_dt = datetime.fromisoformat(state["campaign_started_at"])
    if (now - started_dt).total_seconds() > TARGET_DAYS * 86400:
        print(f"Campaign finished after {TARGET_DAYS} days. Stop scheduling keepalive.")
        return

    need = True
    last = state.get("last_recreate_at")
    if last:
        age_h = (now - datetime.fromisoformat(last)).total_seconds() / 3600
        need = age_h >= RECREATE_AFTER_HOURS
        sid = state.get("sandbox_id")
        if sid and not need:
            try:
                sb = modal.Sandbox.from_id(sid)
                if sb.poll() is not None:
                    need = True
                else:
                    sb.exec("true").wait()
            except Exception:
                need = True

    if not need:
        print("Sandbox still within window; no recreate.")
        print("connection:", ssh.CONNECTION_FILE)
        return

    image_id = None
    old_id = state.get("sandbox_id")
    if old_id:
        try:
            old = modal.Sandbox.from_id(old_id)
            print("Snapshotting filesystem from", old_id)
            snap = old.snapshot_filesystem()
            image_id = snap.object_id
            print("Snapshot image:", image_id)
            try:
                old.terminate()
            except Exception:
                pass
        except Exception as e:
            print("No snapshot (base image):", type(e).__name__, e)

    print("Creating sandbox...")
    sb = ssh.create_sandbox(image_id=image_id)
    host, port = ssh.wait_tunnel(sb)
    ssh.write_connection(sb.object_id, host, port)
    state["sandbox_id"] = sb.object_id
    state["last_recreate_at"] = now.isoformat()
    state["recreates"] = int(state.get("recreates", 0)) + 1
    state["last_host"] = host
    state["last_port"] = port
    save_state(state)
    sb.detach()
    print("OK", sb.object_id, f"ssh -p {port} root@{host}")


if __name__ == "__main__":
    ensure_running()
