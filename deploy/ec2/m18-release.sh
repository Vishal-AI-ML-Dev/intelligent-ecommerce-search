#!/bin/sh
# M18 release on the single-EC2 stack (docs/m18/release.md). Run as root in /opt/m26a:
#   sudo ./m18-release.sh <image.tar.gz | -> <new-image-tag> <provenance.json sha256>
# ("-" = the image is already loaded). Needs the release kit (compose.yaml, setup.sh, scripts),
# ./returns_v1/{provenance.json,bundle/returns_v1.json} from the release commit, and in .env:
# RETURNS_VIEWER_TOKEN, RETURNS_IMPORTER_DB_PASSWORD, RETURNS_IMPORTER_LABEL (values never shown).
# This is the only supported M18 sequence: update.sh alone neither migrates nor applies grants.
# Stops at the first failed step; the backup taken in step 2 is the recovery point.
set -eu
cd "${M26A_DIR:-/opt/m26a}"
archive="$1" tag="$2" provenance_sha="$3"
say() { echo "m18-release: $*"; }
die() { echo "m18-release: STOP: $*" >&2; exit 1; }
revision() {
  docker compose exec -T db psql -U search_admin -d ecommerce_search -Atc \
    "select version_num from alembic_version"
}

# 1. Preflight: nothing is written before these pass.
for key in RETURNS_VIEWER_TOKEN RETURNS_IMPORTER_DB_PASSWORD RETURNS_IMPORTER_LABEL; do
  grep -q "^$key=." .env || die "$key is not set in .env"
done
[ -f returns_v1/provenance.json ] && [ -f returns_v1/bundle/returns_v1.json ] \
  || die "returns_v1/provenance.json or returns_v1/bundle/returns_v1.json is missing"
echo "$provenance_sha  returns_v1/provenance.json" | sha256sum -c --status \
  || die "provenance.json does not match the release checksum"
bundle_sha=$(sed -n 's/.*"json": *"\([0-9a-f]\{64\}\)".*/\1/p' returns_v1/provenance.json)
[ -n "$bundle_sha" ] || die "no JSON bundle checksum in provenance.json"
# bundle_sha256 (contracts §4.6) = SHA-256 of "return-data-v1\n" + "<part>\0<file sha256>\n"; the
# JSON bundle is sent as the single part bundle.json.
file_sha=$(sha256sum returns_v1/bundle/returns_v1.json | cut -c1-64)
[ "$(printf 'return-data-v1\nbundle.json\0%s\n' "$file_sha" | sha256sum | cut -c1-64)" = "$bundle_sha" ] \
  || die "returns_v1.json does not match provenance.json"
before=$(revision)
case "$before" in 0006|0007) ;; *) die "database revision is '$before', expected 0006 or 0007" ;; esac
say "preflight passed (revision $before, bundle $bundle_sha)"

# 2. Recovery point, then the new image.
./backup.sh
[ "$archive" = "-" ] || gunzip -c "$archive" | docker load
docker image inspect "$tag" >/dev/null 2>&1 || die "image $tag is not loaded"

# 3. Migrations, roles and grants with the NEW image; the running app is not touched.
start=$(date +%s)
APP_IMAGE="$tag" docker compose --profile setup run --rm setup sh /setup/setup.sh --release
say "migrations, roles and grants took $(( $(date +%s) - start )) s"
[ "$(revision)" = "0007" ] || die "revision is not 0007 after the migration"

# 4. Tools image for later imports and restores (kept even if APP_IMAGE is rolled back).
if grep -q '^M18_TOOLS_IMAGE=' .env; then
  sed -i "s|^M18_TOOLS_IMAGE=.*|M18_TOOLS_IMAGE=$tag|" .env
else
  echo "M18_TOOLS_IMAGE=$tag" >> .env
fi

# 5. Import and verify the release as the scoped importer role (idempotent on re-run).
docker compose --profile m18 run --rm returns-import import /release/returns_v1/bundle/returns_v1.json
docker compose --profile m18 run --rm returns-import verify /release/returns_v1/bundle/returns_v1.json

# 6. Grants gate (re-applied and verified), then the app swap.
docker compose --profile m18 run --rm grants
sed -i "s|^APP_IMAGE=.*|APP_IMAGE=$tag|" .env
docker compose up -d app
say "done: app runs $tag; run the release.md smoke checks next"
