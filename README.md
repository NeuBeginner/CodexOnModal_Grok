# CodexOnModal_Grok

Debian 12 Modal Sandbox with public SSH tunnel (no Modal CLI on the client PC).

## Spec

- OS: Debian 12 (`debian:12`)
- CPU: 4 · RAM: 16384 MiB
- Disk: Volume `debian512` → `/mnt/data`
- Access: public TCP tunnel → OpenSSH

## 24h Modal limit → ~7 days

One Sandbox lasts **max 24 hours**. Run `keepalive.py` every ~20 hours for a 7-day campaign. `/mnt/data` persists; optional filesystem snapshot restores the rest.

## Setup

```bash
pip install -U modal
modal setup
echo 'your-strong-password' > root_password.txt   # gitignored
python server_ssh.py
cat connection.txt
```

## Keepalive

```bash
python keepalive.py
```

Windows Task Scheduler every 20 hours:
`python C:\path\to\CodexOnModal_Grok\keepalive.py`

## Security

Public Internet SSH — use a strong password or keys; do not commit `root_password.txt`.
