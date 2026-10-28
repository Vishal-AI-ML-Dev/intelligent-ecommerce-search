#!/bin/sh
# Deploy a new image uploaded as /opt/m26a/<tar.gz>: sudo ./update.sh <file.tar.gz> <image-tag>
# Backs up first; rollback = run again with the previous tag (images are kept until pruned).
# It never migrates and never applies grants: for the M18 release use m18-release.sh, not this.
set -eu
cd "${M26A_DIR:-/opt/m26a}"
./backup.sh
gunzip -c "$1" | docker load
sed -i "s|^APP_IMAGE=.*|APP_IMAGE=$2|" .env
docker compose up -d app
echo "update: app now runs $2"
