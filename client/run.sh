#!/usr/bin/env bash
set -euo pipefail

APPIO_API_ADDRESS=${APPIO_API_ADDRESS:-"supernode:9094"}

flower-superexec \
  --plugin-type clientapp \
  --appio-api-address "${APPIO_API_ADDRESS}" \
  --insecure
