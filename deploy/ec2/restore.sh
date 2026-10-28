#!/bin/sh
# Restore a backup made by backup.sh: sudo ./restore.sh backups/<file>.dump
# REPLACES the database: it is dropped and re-created empty, so no table newer than the dump
# survives (a pre-M18 dump leaves no M18 table behind). The API is stopped while restoring.
# Grants afterwards (docs/m18/release.md): a dump at revision 0007 or later gets the explicit M18
# grants script, never a blanket grant (that would expose raw return files to the API role); an
# older dump gets the pre-M18 grants it always had.
set -eu
cd "${M26A_DIR:-/opt/m26a}"
[ -f "$1" ] || { echo "no such backup: $1" >&2; exit 1; }
psql() { docker compose exec -T db psql -v ON_ERROR_STOP=1 -U search_admin "$@"; }
docker compose stop app
psql -d postgres -c "DROP DATABASE ecommerce_search WITH (FORCE)"
psql -d postgres -c "CREATE DATABASE ecommerce_search"
docker compose exec -T db pg_restore -U search_admin -d ecommerce_search --no-owner --exit-on-error < "$1"
rev=$(psql -d ecommerce_search -Atc "select version_num from alembic_version")
case "$rev" in
  0001|0002|0003|0004|0005|0006)
    psql -d ecommerce_search \
      -c "GRANT CONNECT ON DATABASE ecommerce_search TO search_app" \
      -c "GRANT USAGE ON SCHEMA public TO search_app" \
      -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO search_app"
    ;;
  *)
    docker compose --profile m18 run --rm grants
    ;;
esac
docker compose start app
echo "restore: done from $1 (revision $rev)"
