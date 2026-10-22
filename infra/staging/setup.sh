# M26A one-off staging setup, run inside the application image by the `setup` ECS task as the
# RDS master user. Writes to the empty staging database: migrations, the committed synthetic
# seed, embeddings; then the read-only checks and the SELECT-only role the Search API uses.
set -eu
alembic upgrade head
python -m ecommerce_search.catalog ingest --database "$POSTGRES_DB"
python -m ecommerce_search.search embed --database "$POSTGRES_DB"
python -m ecommerce_search.search status --database "$POSTGRES_DB" --require-current
python -m ecommerce_search.search embed-status --database "$POSTGRES_DB" --require-current
python - <<'PY'
import os

import psycopg
from psycopg import sql

name = os.environ["APP_DB_USER"]
role = sql.Identifier(name)
db = os.environ["POSTGRES_DB"]
with psycopg.connect(
    host=os.environ["POSTGRES_HOST"],
    port=int(os.environ["POSTGRES_PORT"]),
    dbname=db,
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    # RDS (managed stack) requires TLS; the single-EC2 Compose network sets SETUP_DB_SSLMODE=disable.
    sslmode=os.environ.get("SETUP_DB_SSLMODE", "require"),
    autocommit=True,
) as conn:
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
    verb = sql.SQL("ALTER ROLE" if exists else "CREATE ROLE")
    password = sql.Literal(os.environ["APP_DB_PASSWORD"])
    conn.execute(sql.SQL("{} {} LOGIN PASSWORD {}").format(verb, role, password))
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db), role))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(role))
print("setup: app role ready (SELECT only)")
PY
