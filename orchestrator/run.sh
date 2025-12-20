#!/usr/bin/env bash
set -euo pipefail

APPIO_API_ADDRESS=${APPIO_API_ADDRESS:-"superlink:9091"}

echo "[serverapp] Connexion à ${APPIO_API_ADDRESS}"

flower-superexec \
  --plugin-type serverapp \
  --appio-api-address "${APPIO_API_ADDRESS}" \
  --insecure
