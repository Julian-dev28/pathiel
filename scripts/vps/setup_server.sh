#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server for the desk loops.
# Run as root ON THE SERVER:
#
#   ssh root@SERVER 'bash -s' < scripts/vps/setup_server.sh
#
# Creates the `pathiel` user (your root SSH key carries over), installs
# python, tmux and rsync, installs Claude Code for that user, and closes
# every inbound port except SSH. The Desk page (:8000) is never exposed;
# reach it with `ssh -L 8000:localhost:8000 pathiel@SERVER`.
set -euo pipefail

if [[ "$(id -u)" != "0" ]]; then
  echo "run as root on the server" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip tmux rsync curl ufw ca-certificates >/dev/null

if ! id pathiel >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" pathiel
fi
install -d -m 700 -o pathiel -g pathiel /home/pathiel/.ssh
if [[ -f /root/.ssh/authorized_keys ]]; then
  install -m 600 -o pathiel -g pathiel /root/.ssh/authorized_keys /home/pathiel/.ssh/authorized_keys
fi
install -d -m 700 -o pathiel -g pathiel /home/pathiel/pathiel /home/pathiel/state

# Claude Code, native installer, for the pathiel user (lands in ~/.local/bin).
su - pathiel -c 'command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash'
su - pathiel -c 'grep -q ".local/bin" ~/.bashrc || echo "export PATH=\$HOME/pathiel/.venv/bin:\$HOME/.local/bin:\$PATH" >> ~/.bashrc'

ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null

echo "server ready: user pathiel, Claude Code installed, only SSH open"
echo "next, on the Mac: scripts/vps/push.sh pathiel@SERVER"
