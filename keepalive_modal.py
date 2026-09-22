"""Modal-scheduled keepalive: recreate SSH Sandbox every ~20h for TARGET_DAYS.

Deploy once (PC on):
  modal deploy keepalive_modal.py

After deploy, keepalive runs in Modal cloud — your PC can be off.
Connection info is written to Volume /mnt/data/connection.txt inside the sandbox
and also to Modal Dict "codex-on-modal-conn".
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import modal

APP_NAME = "debian12-ssh-server"
VOLUME_NAME = "debian512"
SANDBOX_NAME = "debian12-4cpu-16gb-ssh"
TARGET_DAYS = 7
RECREATE_EVERY_HOURS = 20

app = modal.App("codex-on-modal-keepalive")
vol = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
conn = modal.Dict.from_name("codex-on-modal-conn", create_if_missing=True)

# Password from Modal Secret "ssh-root-password" key ROOT_PASSWORD
# Create once:  modal secret create ssh-root-password ROOT_PASSWORD=...
secret = modal.Secret.from_name("ssh-root-password")

image_base = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("modal>=1.5.0")
)


def build_sandbox_image() -> modal.Image:
    return (
        modal.Image.from_registry("debian:12")
        .apt_install(
            "openssh-server",
            "procps",
            "iproute2",
            "nano",
            "curl",
            "ca-certificates",
        )
        .run_commands("mkdir -p /var/run/sshd /run/sshd")
    )


def startup_script(password: str) -> str:
    return f"""
set -e
mkdir -p /var/run/sshd /run/sshd /root/.ssh /mnt/data
echo 'root:{password}' | chpasswd
if grep -q '^PermitRootLogin' /etc/ssh/sshd_config; then
  sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
else
  echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config
fi
if grep -q '^PasswordAuthentication' /etc/ssh/sshd_config; then
  sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
else
  echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config
fi
ssh-keygen -A
exec /usr/sbin/sshd -D -e
"""


def create_ssh_sandbox(password: str) -> modal.Sandbox:
    sb_app = modal.App.lookup(APP_NAME, create_if_missing=True)
    try:
        old = modal.Sandbox.from_name(APP_NAME, SANDBOX_NAME)
        try:
            old.terminate()
        except Exception:
            pass
        time.sleep(2)
    except Exception:
        pass

    return modal.Sandbox.create(
        "/bin/bash",
        "-lc",
        startup_script(password),
        app=sb_app,
        name=SANDBOX_NAME,
        image=build_sandbox_image(),
        cpu=4,
        memory=16384,
        volumes={"/mnt/data": vol},
        timeout=60 * 60 * 24,
        idle_timeout=60 * 60 * 6,
        unencrypted_ports=[22],
        pty=False,
    )


def wait_tunnel(sandbox: modal.Sandbox):
    for _ in range(40):
        tunnels = sandbox.tunnels()
        if 22 in tunnels:
            return tunnels[22].tcp_socket
        time.sleep(2)
    raise RuntimeError("tunnel not ready")


@app.function(
    image=image_base,
    secrets=[secret],
    timeout=30 * 60,
    schedule=modal.Period(hours=RECREATE_EVERY_HOURS),
)
def keepalive():
    password = os.environ["ROOT_PASSWORD"]
    started = conn.get("campaign_started_at")
    now = datetime.now(timezone.utc)
    if not started:
        conn["campaign_started_at"] = now.isoformat()
        started = now.isoformat()
    started_dt = datetime.fromisoformat(started)
    if (now - started_dt).total_seconds() > TARGET_DAYS * 86400:
        print("campaign done")
        return {"status": "campaign_finished"}

    sb = create_ssh_sandbox(password)
    host, port = wait_tunnel(sb)
    info = {
        "SANDBOX_ID": sb.object_id,
        "HOST": host,
        "PORT": port,
        "SSH": f"ssh -p {port} root@{host}",
        "UPDATED": now.isoformat(),
    }
    conn["latest"] = info
    # also write into volume via exec
    text = "\n".join(f"{k}={v}" for k, v in info.items()) + "\n"
    p = sb.exec("bash", "-lc", f"cat > /mnt/data/connection.txt << 'E'\n{text}E\n")
    p.wait()
    sb.detach()
    print(info)
    return info


@app.local_entrypoint()
def show():
    print(conn.get("latest") or "no connection yet — wait for first keepalive or run: modal run keepalive_modal.py::keepalive")
