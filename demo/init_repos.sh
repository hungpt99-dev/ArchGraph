#!/bin/sh
# Initialise the demo repositories as real git repositories.
#
# The demo source is tracked as plain files in this repository (no nested git
# repositories, no submodules). The scanner needs an actual commit history, so the
# seed step runs this script first: each folder becomes a git repository with one
# commit if it does not already have one.
set -eu

ROOT="${1:-${DEMO_REPO_ROOT:-/demo/repos}}"

for dir in "$ROOT"/*/; do
  [ -d "$dir" ] || continue
  if [ -d "$dir/.git" ]; then
    continue
  fi
  echo "initialising demo repository: $dir"
  git -C "$dir" init -q -b main
  git -C "$dir" add -A
  git -C "$dir" -c user.email=demo@archgraph.dev -c user.name="ArchGraph Demo" \
      commit -q -m "Initial commit: $(basename "$dir")"
done

echo "demo repositories ready in $ROOT"
