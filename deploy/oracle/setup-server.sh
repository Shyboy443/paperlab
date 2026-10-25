#!/usr/bin/env bash
# One-time setup of a fresh Oracle Cloud Ubuntu 24.04 VM for PaperLab. Run it ON THE SERVER as the default user:
#   bash setup-server.sh <public-ip>
# It installs Docker from Ubuntu's own packages, opens ports 80/443 in the VM firewall, and prepares /opt/paperlab.
set -euo pipefail

IP="${1:?usage: bash setup-server.sh <public-ip>}"

echo "== packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 iptables-persistent unattended-upgrades
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

echo "== firewall: Oracle's Ubuntu image rejects everything but SSH; allow web traffic (80 for the certificate, 443)"
if ! sudo iptables -C INPUT -p tcp -m multiport --dports 80,443 -m state --state NEW -j ACCEPT 2>/dev/null; then
  sudo iptables -I INPUT -p tcp -m multiport --dports 80,443 -m state --state NEW -j ACCEPT
fi
sudo netfilter-persistent save

echo "== clock in UTC (the desks use UTC candles)"
sudo timedatectl set-timezone UTC

echo "== /opt/paperlab"
sudo mkdir -p /opt/paperlab/app /opt/paperlab/data
sudo chown -R "$USER":"$USER" /opt/paperlab
SITE="$(echo "$IP" | tr '.' '-').sslip.io"
if [ ! -f /opt/paperlab/.env ]; then
  echo "SITE_ADDRESS=$SITE" > /opt/paperlab/.env
fi

echo
echo "Done. Log out and back in once (so 'docker' works without sudo), then run push.ps1 on your PC."
echo "The site will be: https://$(cut -d= -f2 /opt/paperlab/.env)"
