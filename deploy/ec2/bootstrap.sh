#!/bin/sh
# Run once on a fresh Ubuntu 24.04 instance: sudo sh bootstrap.sh <duckdns-hostname> <image-tag>
# Installs Docker from Ubuntu's archive, adds a 2 GB swap file, enables the host firewall and
# writes /opt/m26a/.env with freshly generated passwords (root-only, never printed).
set -eu
SITE_HOST="$1"
APP_IMAGE="$2"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2
systemctl enable --now docker
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443 && ufw --force enable
mkdir -p /opt/m26a/backups
chmod 700 /opt/m26a/backups
cd /opt/m26a
if [ ! -f .env ]; then
  umask 077
  {
    echo "SITE_HOST=$SITE_HOST"
    echo "APP_IMAGE=$APP_IMAGE"
    echo "POSTGRES_ADMIN_PASSWORD=$(openssl rand -hex 24)"
    echo "APP_DB_PASSWORD=$(openssl rand -hex 24)"
  } > .env
fi
chmod 600 .env
echo "bootstrap: done (.env written, values not shown)"
