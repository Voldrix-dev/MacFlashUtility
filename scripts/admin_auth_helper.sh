#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--authorize" ]]; then
  echo "Usage: $0 --authorize" >&2
  exit 2
fi

echo "authorized"
