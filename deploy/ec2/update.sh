#!/bin/sh
# Deploy a new image uploaded as /opt/m26a/<tar.gz>: sudo ./update.sh <file.tar.gz> <image-tag>
# Backs up first; rollback = run again with the previous tag (images are kept until pruned).
set -eu
cd /opt/m26a
./backup.sh
gunzip -c "$1" | docker load
sed -i "s|^APP_IMAGE=.*|APP_IMAGE=$2|" .env
docker compose up -d app
echo "update: app now runs $2"
