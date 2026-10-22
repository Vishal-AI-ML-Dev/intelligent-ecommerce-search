#!/bin/sh
# Restore a backup made by backup.sh: sudo ./restore.sh backups/<file>.dump
# REPLACES the current database contents. The API is stopped while restoring.
set -eu
cd /opt/m26a
[ -f "$1" ] || { echo "no such backup: $1" >&2; exit 1; }
docker compose stop app
docker compose exec -T db pg_restore -U search_admin -d ecommerce_search --clean --if-exists --no-owner < "$1"
docker compose exec -T db psql -U search_admin -d ecommerce_search -c \
  "GRANT SELECT ON ALL TABLES IN SCHEMA public TO search_app"
docker compose start app
echo "restore: done from $1"
