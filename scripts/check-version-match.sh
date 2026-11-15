#!/usr/bin/env bash
# Checks that every place that names the release version agrees with VERSION:
# the CHANGELOG section, VERSION in app/server.py, and the README's install
# commands (git clone --branch, the image tag and git checkout).
#
# Usage: check-version-match.sh VERSION   (run from the repository root)
set -uo pipefail

version="$1"
failed=0
fail() { echo "::error::$1"; failed=1; }

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "::error::Version must look like 0.3.1"; exit 1; }

grep -q "^## \[${version}\] - " CHANGELOG.md \
  || fail "CHANGELOG.md has no '## [${version}] - <date>' section."

server=$(sed -nE 's/^VERSION = "([^"]*)".*/\1/p' app/server.py)
[ "$server" = "$version" ] \
  || fail "app/server.py has VERSION = \"${server}\", not \"${version}\"."

# Each install command in the README must name this version.
found=0
while read -r line; do
  [ -z "$line" ] && continue
  found=1
  named=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' <<< "$line")
  [ "$named" = "$version" ] || fail "README.md names ${named} instead of ${version}: ${line}"
done < <(grep -oE '(--branch v|detour-sh-ce:|git checkout v)[0-9]+\.[0-9]+\.[0-9]+' README.md)
[ "$found" = 1 ] || fail "README.md has no install command naming a version."

[ "$failed" = 0 ] && echo "CHANGELOG.md, app/server.py and README.md all name ${version}."
exit "$failed"
