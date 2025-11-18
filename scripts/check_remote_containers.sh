#!/usr/bin/env bash
set -euo pipefail

PROXY_HOST=${PROXY_HOST:-PROXY}
DGX_HOST=${DGX_HOST:-DGX}
ORCH_CONTAINER=${ORCH_CONTAINER:-fl-orchestrator}
CLIENT_CONTAINER=${CLIENT_CONTAINER:-fl-client-dgx}

check_container() {
  local host=$1
  local container=$2
  local label=$3

  if ssh "$host" "docker ps --format '{{.Names}}' --filter name=^${container}$ | grep -q '^${container}$'"; then
    status=$(ssh "$host" "docker inspect -f '{{.State.Status}}' ${container}" 2>/dev/null || echo "inconnu")
    echo "[$label] ${container} en cours d'exécution (état: ${status})"
  else
    echo "[$label] ${container} n'est pas démarré" >&2
    return 1
  fi
}

exit_code=0
check_container "$PROXY_HOST" "$ORCH_CONTAINER" "orchestrateur" || exit_code=$?
check_container "$DGX_HOST" "$CLIENT_CONTAINER" "client" || exit_code=$?

if [[ $exit_code -eq 0 ]]; then
  echo "[test] Les deux conteneurs sont présents."
fi

exit $exit_code
