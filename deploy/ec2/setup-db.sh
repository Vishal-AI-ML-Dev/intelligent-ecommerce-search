#!/bin/sh
# One-off on an EMPTY database: migrations, synthetic seed, embeddings, checks, roles, grants.
# Refuses a database that already has a schema; an existing database is released with
# m18-release.sh (docs/m18/release.md), never by re-running setup.
set -eu
cd "${M26A_DIR:-/opt/m26a}"
docker compose up -d --wait db
tables=$(docker compose exec -T db psql -U search_admin -d ecommerce_search -Atc \
  "select count(*) from pg_tables where schemaname = 'public'")
[ "$tables" = "0" ] || { echo "setup-db: the database is not empty ($tables tables); not run" >&2; exit 1; }
docker compose --profile setup run --rm setup
