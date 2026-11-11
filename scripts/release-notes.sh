#!/usr/bin/env bash
# Print one version's section of CHANGELOG.md (without its heading), for the release notes.
#
#   scripts/release-notes.sh 0.5.0
set -euo pipefail
version="${1:?usage: $0 X.Y.Z}"
cd "$(dirname "$0")/.."
awk -v v="$version" '
  /^## / { inside = (index($0, "## " v " ") == 1); next }
  inside
' CHANGELOG.md | sed -e '/./,$!d' | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}'
