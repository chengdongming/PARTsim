#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
verified_paths="$stage/verified_package_paths.json"
python3 "$AUTODL_DIR/verify_outputs.py" \
  --output-root "$PARTSIM_OUTPUT_ROOT" --profile "$PARTSIM_RUN_MODE" \
  --package-manifest "$verified_paths"
package_dir="${PARTSIM_PACKAGE_DIR:-${PARTSIM_OUTPUT_ROOT%/}_packages}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$package_dir/partsim_v9_3_results_${stamp}.tar.gz"
archive_manifest="$archive.manifest.json"
commit_sha="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
python3 "$AUTODL_DIR/package_inventory.py" \
  --manifest "$verified_paths" --source-root "$PARTSIM_OUTPUT_ROOT" \
  --archive "$archive" --archive-manifest "$archive_manifest" \
  --archive-sha "$archive.sha256" \
  --commit-sha "$commit_sha"
printf 'Packaged verified results: %s %s %s\n' \
  "$archive" "$archive_manifest" "$archive.sha256"
