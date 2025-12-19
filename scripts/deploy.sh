#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="${ROOT_DIR}/.env"
REMOTE_PATH=${REMOTE_PATH:-"~/federatedlearning"}
# Utilisé pour les commandes docker compose locales (construction et résolution des chemins).
PROJECT_DIR="$ROOT_DIR"
# Utilisé pour les montages sur les hôtes distants (chemins doivent exister sur le nœud Docker).
HOST_PROJECT_PATH=${HOST_PROJECT_PATH:-$REMOTE_PATH}
export HOST_PROJECT_PATH

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
DEPLOY_LOG_FOLLOW=${DEPLOY_LOG_FOLLOW:-false}
DEPLOY_LOG_TIMEOUT=${DEPLOY_LOG_TIMEOUT:-60}

PROM_TEMPLATE="${ROOT_DIR}/monitoring/prometheus.tmpl.yml"
PROM_RENDERED="${ROOT_DIR}/monitoring/prometheus/prometheus.yml"

PROM_TEMPLATE="$PROM_TEMPLATE" PROM_RENDERED="$PROM_RENDERED" python - <<'PY'
from pathlib import Path
import os

Path(os.environ["PROM_RENDERED"]).parent.mkdir(parents=True, exist_ok=True)

template = Path(os.environ["PROM_TEMPLATE"]).read_text()
values = {
    "PROXY_IP": os.getenv("PROXY_IP", "127.0.0.1"),
    "DGX_IP": os.getenv("DGX_IP", "127.0.0.1"),
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

print_container_logs() {
  local context=$1
  shift
  local services=("$@")

  if [ ${#services[@]} -eq 0 ]; then
    return
  fi

  echo "[deploy] Journaux des services en échec (${services[*]}) sur ${context}"
  docker --context "$context" compose --project-directory "$PROJECT_DIR" logs --no-color --tail=200 "${services[@]}" || true

  if [[ "$DEPLOY_LOG_FOLLOW" == "true" ]]; then
    echo "[deploy] Suivi en temps réel (limité à ${DEPLOY_LOG_TIMEOUT}s) des services ${services[*]} sur ${context}"
    if ! timeout "$DEPLOY_LOG_TIMEOUT" docker --context "$context" compose --project-directory "$PROJECT_DIR" logs --no-color -f "${services[@]}"; then
      echo "[deploy] Suivi des logs interrompu (timeout ${DEPLOY_LOG_TIMEOUT}s)" >&2
    fi
  fi
}

check_services_health() {
  local context=$1
  local exited_services
  local unhealthy_services

  exited_services=$(docker --context "$context" compose --project-directory "$PROJECT_DIR" ps --services --filter "status=exited" --filter "status=dead") || true
  unhealthy_services=$(docker --context "$context" compose --project-directory "$PROJECT_DIR" ps --services --filter "status=unhealthy") || true

  if [[ -n "$exited_services" ]]; then
    echo "[deploy] ⚠️ Services en échec détectés sur ${context}: ${exited_services//$'\n'/, }" >&2
    print_container_logs "$context" $exited_services
  fi

  if [[ -n "$unhealthy_services" ]]; then
    echo "[deploy] ⚠️ Services en état unhealthy détectés sur ${context}: ${unhealthy_services//$'\n'/, }" >&2
    print_container_logs "$context" $unhealthy_services
  fi
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

resolve_remote_path() {
  local target=$1
  local path=$2

  ssh -o BatchMode=yes "$target" "mkdir -p ${path} && cd ${path} && pwd"
}

# Les services monitorant les métriques tournent sur le DGX : on utilise son chemin absolu
# pour monter les dossiers de configuration dans Prometheus/Grafana.
HOST_PROJECT_PATH=$(resolve_remote_path dgx "$REMOTE_PATH")
export HOST_PROJECT_PATH

echo "[deploy] Démarrage du hub sur le proxy (${PROXY_IP})"
docker --context proxy-node compose --profile hub --project-directory "$PROJECT_DIR" up -d --build
check_services_health proxy-node

echo "[deploy] Démarrage du client + monitoring sur le DGX (${DGX_IP})"
docker --context dgx-node compose --profile client --profile monitor --project-directory "$PROJECT_DIR" up -d --build
check_services_health dgx-node

echo "✅ Déploiement terminé"
echo "🔗 Hub Fleet API: http://${PROXY_IP}:${HUB_PORT}"
echo "📊 Grafana: http://${DGX_IP}:${GRAFANA_PORT} (admin/admin)"
echo "📈 Prometheus: http://${DGX_IP}:${PROMETHEUS_PORT}"
