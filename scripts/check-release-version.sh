#!/usr/bin/env bash
# Checks that VERSION is the next patch after the latest released tag.
# The next minor or major version passes only when the second argument is
# "true". Skipped numbers always fail. See docs/decisions/0002-patch-releases-by-default.md.
#
# Usage: check-release-version.sh VERSION [MINOR_OR_MAJOR] < tag-refs
# Reads existing tags on stdin, one per line (refs/tags/v1.2.3 or v1.2.3).
set -euo pipefail

version="$1"
bigger="${2:-false}"

[[ "$version" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] || { echo "::error::Version must look like 0.3.1"; exit 1; }
major=${BASH_REMATCH[1]} minor=${BASH_REMATCH[2]} patch=${BASH_REMATCH[3]}

# Latest earlier release. The version itself is left out so a failed start can be rerun.
latest=$(sed -E 's#^refs/tags/##; s#^v##' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | grep -vx "$version" | sort -V | tail -n1 || true)
if [ -z "$latest" ]; then
  echo "No earlier release; ${version} is fine."
  exit 0
fi
IFS=. read -r lmajor lminor lpatch <<< "$latest"
next_patch="${lmajor}.${lminor}.$((lpatch + 1))"

if [ "$version" = "$next_patch" ]; then
  echo "${version} is the next patch after ${latest}."
  exit 0
fi

if [ "$(printf '%s\n%s\n' "$latest" "$version" | sort -V | tail -n1)" != "$version" ]; then
  echo "::error::${version} is not newer than the latest release, ${latest}."
  exit 1
fi

if [ "$bigger" != "true" ]; then
  echo "::error::The next patch after ${latest} is ${next_patch}. For ${version}, tick \"Minor or major release\" (see docs/decisions/0002-patch-releases-by-default.md)."
  exit 1
fi

if [ "$version" != "${lmajor}.$((lminor + 1)).0" ] && [ "$version" != "$((lmajor + 1)).0.0" ]; then
  echo "::error::After ${latest} the next minor is ${lmajor}.$((lminor + 1)).0 and the next major is $((lmajor + 1)).0.0, not ${version}."
  exit 1
fi
echo "${version} is a minor or major release after ${latest}, as requested."
