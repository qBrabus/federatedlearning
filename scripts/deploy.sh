#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="${ROOT_DIR}/.env"
REMOTE_PATH=${REMOTE_PATH:-"~/federatedlearning"}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[deploy] Fichier .env introuvable. Copiez .env.example puis personnalisez-le." >&2
  exit 1
fi

# Charger la configuration
set -a
source "$ENV_FILE"
set +a

: "${PROXY_IP:?[deploy] PROXY_IP doit être défini dans .env}"
: "${DGX_IP:?[deploy] DGX_IP doit être défini dans .env}"
HUB_PORT=${HUB_PORT:-8443}
GRAFANA_PORT=${GRAFANA_PORT:-3000}
PROMETHEUS_PORT=${PROMETHEUS_PORT:-9090}

PROM_TEMPLATE="${ROOT_DIR}/monitoring/prometheus.tmpl.yml"
PROM_RENDERED="${ROOT_DIR}/monitoring/prometheus.generated.yml"

PROM_TEMPLATE="$PROM_TEMPLATE" PROM_RENDERED="$PROM_RENDERED" python - <<'PY'
from pathlib import Path
import os

template = Path(os.environ["PROM_TEMPLATE"]).read_text()
values = {
    "PROXY_IP": os.getenv("PROXY_IP", "127.0.0.1"),
}
rendered = template
for key, val in values.items():
    rendered = rendered.replace(f"${{{key}}}", val)
Path(os.environ["PROM_RENDERED"]).write_text(rendered)
print(f"[deploy] Fichier Prometheus rendu vers {os.environ['PROM_RENDERED']}")
PY

create_context() {
  local name=$1
  local target=$2
  if ! docker context ls --format '{{.Name}}' | grep -q "^${name}$"; then
    echo "[deploy] Création du contexte docker ${name} -> ${target}"
    docker context create "$name" --docker "host=ssh://${target}"
  else
    echo "[deploy] Contexte docker ${name} déjà présent"
  fi
}

create_context proxy-node "proxy-data"
create_context dgx-node "dgx"

sync_repo() {
  local target=$1
  echo "[deploy] Synchronisation du dépôt vers ${target}:${REMOTE_PATH}"
  rsync -avz --delete --exclude '.git/' --exclude 'certs/' --exclude 'data/' "${ROOT_DIR}/" "${target}:${REMOTE_PATH}/"
}

ensure_rsync() {
  local target=$1

  if ssh -o BatchMode=yes "$target" "command -v rsync >/dev/null 2>&1"; then
    return
  fi

  echo "[deploy] rsync absent sur ${target}, tentative d'installation automatique (sudo requis)" >&2
  if ssh -o BatchMode=yes "$target" "sudo apt-get update -y && sudo apt-get install -y rsync"; then
    return
  fi

  echo "[deploy] Échec de l'installation de rsync sur ${target}. Installez-le manuellement puis relancez le déploiement." >&2
  exit 1
}

ensure_rsync proxy-data
ensure_rsync dgx

sync_repo proxy-data
sync_repo dgx

echo "[deploy] Démarrage du hub sur le proxy (${PROXY_IP})"
docker --context proxy-node compose --profile hub --project-directory "$REMOTE_PATH" up -d --build

echo "[deploy] Démarrage du client + monitoring sur le DGX (${DGX_IP})"
docker --context dgx-node compose --profile client --profile monitor --project-directory "$REMOTE_PATH" up -d --build

echo "✅ Déploiement terminé"
echo "🔗 Hub Fleet API: http://${PROXY_IP}:${HUB_PORT}"
echo "📊 Grafana: http://${DGX_IP}:${GRAFANA_PORT} (admin/admin)"
echo "📈 Prometheus: http://${DGX_IP}:${PROMETHEUS_PORT}"
