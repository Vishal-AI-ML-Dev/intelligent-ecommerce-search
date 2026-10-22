#!/bin/sh
# One-off on an EMPTY database: migrations, synthetic seed, embeddings, checks, API role.
set -eu
cd /opt/m26a
docker compose up -d db
docker compose --profile setup run --rm setup
