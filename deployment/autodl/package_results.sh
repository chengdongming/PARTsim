#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
"$AUTODL_DIR/verify_results.sh"
package_dir="${PARTSIM_PACKAGE_DIR:-${PARTSIM_OUTPUT_ROOT%/}_packages}"
mkdir -p "$package_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$package_dir/partsim_v9_3_results_${stamp}.tar.gz"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/package"
git -C "$PROJECT_ROOT" rev-parse HEAD > "$stage/package/commit_sha.txt"
cp -a "$AUTODL_DIR" "$stage/package/deployment_autodl"
cp -a "$PARTSIM_OUTPUT_ROOT" "$stage/package/results"
tar -czf "$archive" -C "$stage" package
sha256sum "$archive" > "$archive.sha256"
printf 'Packaged verified results: %s\n' "$archive"
