#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [orchestrator|client|all] [--self-signed]" >&2
  echo "Exemples:" >&2
  echo "  $0 orchestrator         # construit uniquement l'image serveur" >&2
  echo "  $0 client               # construit uniquement l'image DGX" >&2
  echo "  $0 all                  # construit les deux images" >&2
  echo "  $0 all --self-signed    # génère aussi des certificats auto-signés" >&2
  exit 1
}

SELF_SIGNED=${SELF_SIGNED:-false}
TARGET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    orchestrator|client|all)
      TARGET="$1"
      shift
      ;;
    --self-signed)
      SELF_SIGNED=true
      shift
      ;;
    *)
      usage
      ;;
  esac
done

[[ -z "$TARGET" ]] && usage

case "$TARGET" in
  orchestrator)
    echo "[build] construction de fl-orchestrator:latest" >&2
    docker build -t fl-orchestrator:latest ./orchestrator
    ;;
  client)
    echo "[build] construction de fl-client-dgx:latest (CUDA 12.4)" >&2
    docker build -t fl-client-dgx:latest ./client
    ;;
  all)
    echo "[build] construction orchestrateur + client" >&2
    docker build -t fl-orchestrator:latest ./orchestrator
    docker build -t fl-client-dgx:latest ./client
    ;;
  *)
    usage
    ;;
esac

if [[ "$SELF_SIGNED" == "true" ]]; then
  echo "[build] génération de certificats auto-signés pour tests..." >&2
  "$(pwd)/scripts/generate_self_signed_certs.sh"
fi
