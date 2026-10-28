#!/bin/sh
# Daily (cron) and before every update: custom-format pg_dump into /opt/m26a/backups, keep 7.
set -eu
cd "${M26A_DIR:-/opt/m26a}"
file="backups/ecommerce_search-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T db pg_dump -U search_admin -d ecommerce_search -Fc > "$file"
chmod 600 "$file"
ls -1t backups/*.dump | tail -n +8 | xargs -r rm --
echo "backup: $file ($(du -h "$file" | cut -f1))"
