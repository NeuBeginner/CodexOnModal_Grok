"""Create Debian 12 Modal Sandbox with public SSH tunnel (TCP).

Modal Sandbox max lifetime is 24h — use keepalive.py for ~7 days continuity
via filesystem snapshot + recreate. Data persists on Volume /mnt/data.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import modal

APP_NAME = "debian12-ssh-server"
VOLUME_NAME = "debian512"
SANDBOX_NAME = "debian12-4cpu-16gb-ssh"
CONNECTION_FILE = Path(__file__).resolve().parent / "connection.txt"
PASSWORD = os.environ.get("ROOT_PASSWORD")
if not PASSWORD:
    pw_file = Path(__file__).resolve().parent / "root_password.txt"
    if pw_file.exists():
        PASSWORD = pw_file.read_text(encoding="utf-8").strip()
if not PASSWORD:
    raise SystemExit("Set ROOT_PASSWORD or create root_password.txt")


def build_image() -> modal.Image:
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
mkdir -p /var/run/sshd /run/sshd /root/.ssh
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


def create_sandbox(*, image_id: str | None = None) -> modal.Sandbox:
    app = modal.App.lookup(APP_NAME, create_if_missing=True)
    volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
    image = modal.Image.from_id(image_id) if image_id else build_image()

    try:
        old = modal.Sandbox.from_name(APP_NAME, SANDBOX_NAME)
        old.terminate()
        time.sleep(2)
    except Exception:
        pass

    return modal.Sandbox.create(
        "/bin/bash",
        "-lc",
        startup_script(PASSWORD),
        app=app,
        name=SANDBOX_NAME,
        image=image,
        cpu=4,
        memory=16384,
        volumes={"/mnt/data": volume},
        timeout=60 * 60 * 24,
        idle_timeout=60 * 60 * 6,
        unencrypted_ports=[22],
        pty=False,
    )


def wait_tunnel(sandbox: modal.Sandbox, tries: int = 40) -> tuple[str, int]:
    for _ in range(tries):
        tunnels = sandbox.tunnels()
        if 22 in tunnels:
            host, port = tunnels[22].tcp_socket
            return host, port
        time.sleep(2)
    raise RuntimeError(f"SSH tunnel not ready: {sandbox.tunnels()}")


def write_connection(sandbox_id: str, host: str, port: int) -> None:
    CONNECTION_FILE.write_text(
        "\n".join(
            [
                f"SANDBOX_ID={sandbox_id}",
                f"HOST={host}",
                f"PORT={port}",
                f"SSH=ssh -p {port} root@{{host}}".replace("{host}", host),
                f"UPDATED={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> None:
    sb = create_sandbox()
    host, port = wait_tunnel(sb)
    write_connection(sb.object_id, host, port)
    print("Sandbox:", sb.object_id)
    print(f"ssh -p {port} root@{host}")
    print("Wrote", CONNECTION_FILE)
    sb.detach()


if __name__ == "__main__":
    main()
