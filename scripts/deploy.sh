#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="${ROOT_DIR}/.env"
REMOTE_PATH=${REMOTE_PATH:-"~/federatedlearning"}
PROJECT_DIR="$ROOT_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[deploy] Fichier .env introuvable." >&2
  exit 1
fi

# Charger la configuration
set -a
source "$ENV_FILE"
set +a

# Vérification des variables critiques
: "${PROXY_IP:?[deploy] PROXY_IP doit être défini (alias SSH ou IP)}"
: "${HUB_INTERNAL_IP:?[deploy] HUB_INTERNAL_IP doit être défini}"
: "${HUB_PUBLIC_IP:?[deploy] HUB_PUBLIC_IP doit être défini}"
: "${CLIENT_SITES:?[deploy] CLIENT_SITES doit être défini}"

declare -A SITE_HOST_PATHS
declare -A CONTEXT_HOST_PATHS
declare -A CONTEXT_SITE_NAMES
PROMETHEUS_CONTEXTS=()

create_context() {
  local name=$1
  local target=$2
  local endpoint="ssh://${target}"
  if docker context ls --format '{{.Name}}' | grep -q "^${name}$"; then
    docker context update "$name" --docker "host=${endpoint}" >/dev/null 2>&1 || true
  else
    docker context create "$name" --docker "host=${endpoint}"
  fi
}

ensure_ssh_access() {
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$1" "echo ok" >/dev/null 2>&1 || (echo "[deploy] SSH failed to $1. Vérifiez votre ~/.ssh/config" && exit 1)
}

sync_repo() {
  echo "[deploy] Synchronisation vers $1..."
  rsync -e "ssh -o BatchMode=yes" -avz --delete --exclude '.git/' --exclude 'certs/' --exclude 'data/' "${ROOT_DIR}/" "$1:$2/"
}

generate_prometheus_config() {
  # IMPORTANCE : On passe HUB_INTERNAL_IP à PROXY_IP pour que Prometheus utilise l'IP
  CLIENT_SITES="$CLIENT_SITES" GPU_SITES="${GPU_SITES_LIST_STR:-}" PROXY_IP="$HUB_INTERNAL_IP" python3 - <<'PY'
import os
from pathlib import Path
import yaml

sites_env = os.getenv("CLIENT_SITES", "")
gpu_sites_env = os.getenv("GPU_SITES", "")
proxy_ip = os.getenv("PROXY_IP", "127.0.0.1")

sites = [entry.split(":", 1) for entry in sites_env.split(",") if ":" in entry]
gpu_sites = [entry.split(":", 1) for entry in gpu_sites_env.split(",") if ":" in entry]

config = {
    "global": {"scrape_interval": "5s"},
    "scrape_configs": [
        {"job_name": "cadvisor-hub", "static_configs": [{"targets": [f"{proxy_ip}:8081"]}]},
        {"job_name": "cadvisor-client", "static_configs": [{"targets": [f"{ip}:8080" for _, ip in sites]}]},
    ],
}
if gpu_sites:
    config["scrape_configs"].append({
        "job_name": "dcgm-exporters", 
        "static_configs": [{"targets": [f"{ip}:9400" for _, ip in gpu_sites]}]
    })

prometheus_dir = Path("monitoring/prometheus")
prometheus_dir.mkdir(parents=True, exist_ok=True)
output_file = prometheus_dir / "prometheus.yml"
output_file.write_text(yaml.dump(config, sort_keys=False))
PY
}

distribute_prometheus_config() {
  local config_path="${ROOT_DIR}/monitoring/prometheus/prometheus.yml"
  for SITE_ENTRY in "${SITES[@]}"; do
    local SITE_IP=${SITE_ENTRY#*:}
    local remote=${SITE_HOST_PATHS[$SITE_IP]:-$REMOTE_PATH}
    ssh -o BatchMode=yes "$SITE_IP" "mkdir -p $remote/monitoring/prometheus"
    rsync -avz "$config_path" "$SITE_IP:$remote/monitoring/prometheus/prometheus.yml"
  done
  ssh -o BatchMode=yes "$PROXY_IP" "mkdir -p $PROXY_HOST_PATH/monitoring/prometheus"
  rsync -avz "$config_path" "$PROXY_IP:$PROXY_HOST_PATH/monitoring/prometheus/prometheus.yml"
}

# --- 1. DEPLOIEMENT HUB (PROXY) ---
echo ">>> Déploiement du HUB sur le Proxy ($PROXY_IP)"
ensure_ssh_access "$PROXY_IP"
create_context proxy-node "$PROXY_IP"
PROXY_HOST_PATH=$(ssh -o BatchMode=yes "$PROXY_IP" "mkdir -p $REMOTE_PATH && cd $REMOTE_PATH && pwd")
sync_repo "$PROXY_IP" "$PROXY_HOST_PATH"

export HOST_PROJECT_PATH="$PROXY_HOST_PATH"
docker --context proxy-node compose --profile hub --project-directory "$PROJECT_DIR" up -d --build

# --- 2. PREPARATION MONITEUR ---
IFS=',' read -ra SITES <<< "$CLIENT_SITES"
GPU_SITES_LIST=()
GPU_SITES_LIST_STR=""
generate_prometheus_config # Création initiale

# --- 3. BOUCLE DEPLOIEMENT CLIENTS ---
for SITE_ENTRY in "${SITES[@]}"; do
  SITE_NAME=${SITE_ENTRY%%:*}
  SITE_IP=${SITE_ENTRY#*:}
  CONTEXT_NAME="ctx-${SITE_NAME}"

  echo "-------------------------------------------------------"
  echo ">>> Site : ${SITE_NAME} (${SITE_IP})"
  echo "-------------------------------------------------------"

  ensure_ssh_access "$SITE_IP"
  CUR_PATH=$(ssh -o BatchMode=yes "$SITE_IP" "mkdir -p $REMOTE_PATH && cd $REMOTE_PATH && pwd")
  SITE_HOST_PATHS["$SITE_IP"]="$CUR_PATH"
  
  sync_repo "$SITE_IP" "$CUR_PATH"
  create_context "$CONTEXT_NAME" "$SITE_IP"

  if [[ "$SITE_NAME" == "site1" ]]; then
    export HUB_ADDRESS="$HUB_INTERNAL_IP"
  else
    export HUB_ADDRESS="$HUB_PUBLIC_IP"
  fi

  GPU_PRESENT=$(ssh -o BatchMode=yes "$SITE_IP" "command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null && echo true || echo false")
  COMPOSE_FILES=(-f "$PROJECT_DIR/compose.yaml")
  PROFILES=(--profile client --profile monitor)

  if [[ "$GPU_PRESENT" == "true" ]]; then
    echo "[deploy] GPU détecté sur $SITE_NAME"
    COMPOSE_FILES+=(-f "$PROJECT_DIR/compose.gpu.yaml")
    PROFILES+=(--profile monitor-gpu)
    GPU_SITES_LIST+=("${SITE_NAME}:${SITE_IP}")
  fi

  export SITE_NAME="$SITE_NAME"
  export HOST_PROJECT_PATH="$CUR_PATH"

  docker --context "$CONTEXT_NAME" compose \
    --project-directory "$PROJECT_DIR" \
    "${COMPOSE_FILES[@]}" \
    "${PROFILES[@]}" \
    up -d --build

  PROMETHEUS_CONTEXTS+=("$CONTEXT_NAME")
  CONTEXT_HOST_PATHS["$CONTEXT_NAME"]="$CUR_PATH"
  CONTEXT_SITE_NAMES["$CONTEXT_NAME"]="$SITE_NAME"
done

# --- 4. FINALISATION PROMETHEUS (Scrape all sites) ---
echo ">>> Mise à jour finale et redémarrage de Prometheus..."
GPU_SITES_LIST_STR=$(IFS=','; echo "${GPU_SITES_LIST[*]}")
generate_prometheus_config
distribute_prometheus_config

for CTX in "${PROMETHEUS_CONTEXTS[@]}"; do
  export HOST_PROJECT_PATH="${CONTEXT_HOST_PATHS[$CTX]}"
  export SITE_NAME="${CONTEXT_SITE_NAMES[$CTX]}"
  # On force le redémarrage pour charger la nouvelle config
  docker --context "$CTX" compose --project-directory "$PROJECT_DIR" restart prometheus
done

echo "✅ Déploiement terminé. Tout est UP."
