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
declare -A SITE_HOST_PATHS
declare -A CONTEXT_HOST_PATHS
declare -A CONTEXT_SITE_NAMES
PROMETHEUS_CONTEXTS=()

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[deploy] Fichier .env introuvable. Copiez .env.example puis personnalisez-le." >&2
  exit 1
fi

# Charger la configuration
set -a
source "$ENV_FILE"
set +a

: "${PROXY_IP:?[deploy] PROXY_IP doit être défini dans .env}"
: "${CLIENT_SITES:?[deploy] CLIENT_SITES doit être défini dans .env}"
HUB_PORT=${HUB_PORT:-8443}
GRAFANA_PORT=${GRAFANA_PORT:-3000}
PROMETHEUS_PORT=${PROMETHEUS_PORT:-9090}
DEPLOY_LOG_FOLLOW=${DEPLOY_LOG_FOLLOW:-false}
DEPLOY_LOG_TIMEOUT=${DEPLOY_LOG_TIMEOUT:-60}

create_context() {
  local name=$1
  local target=$2
  local endpoint="ssh://${target}"

  if docker context ls --format '{{.Name}}' | grep -q "^${name}$"; then
    local current_endpoint
    current_endpoint=$(docker context inspect "$name" --format '{{ .Endpoints.docker.Host }}' 2>/dev/null || true)

    if [[ "$current_endpoint" != "$endpoint" ]]; then
      echo "[deploy] Mise à jour du contexte docker ${name} -> ${target} (ancien endpoint: ${current_endpoint:-inconnu})"
      if ! docker context update "$name" --docker "host=${endpoint}" >/dev/null 2>&1; then
        echo "[deploy] Échec de la mise à jour du contexte ${name}, recréation" >&2
        docker context rm -f "$name" >/dev/null 2>&1 || true
        docker context create "$name" --docker "host=${endpoint}"
      fi
    else
      echo "[deploy] Contexte docker ${name} déjà présent"
    fi
    return
  fi

  echo "[deploy] Création du contexte docker ${name} -> ${target}"
  docker context create "$name" --docker "host=${endpoint}"
}

ensure_ssh_access() {
  local target=$1

  if ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" "echo ok" >/dev/null 2>&1; then
    return
  fi

  echo "[deploy] Impossible d'établir la connexion SSH vers ${target}. Vérifiez la configuration SSH (clé, .ssh/config, reachabilité)." >&2
  exit 1
}

sync_repo() {
  local target=$1
  local destination=${2:-$REMOTE_PATH}
  echo "[deploy] Synchronisation du dépôt vers ${target}:${destination}"
  if ! rsync -e "ssh -o BatchMode=yes -o ConnectTimeout=10" --rsync-path="bash -c 'rsync \"\$@\"' --" -avz --delete --exclude '.git/' --exclude 'certs/' --exclude 'data/' "${ROOT_DIR}/" "${target}:${destination}/"; then
    echo "[deploy] La synchronisation rsync vers ${target} a échoué. Vérifiez la connectivité réseau et la configuration SSH." >&2
    exit 1
  fi
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

detect_remote_gpu() {
  local target=$1

  if ssh -o BatchMode=yes "$target" "command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1" >/dev/null 2>&1; then
    echo "true"
  else
    echo "false"
  fi
}

resolve_remote_path() {
  local target=$1
  local path=$2

  ssh -o BatchMode=yes "$target" "mkdir -p ${path} && cd ${path} && pwd"
}

generate_prometheus_config() {
  CLIENT_SITES="$CLIENT_SITES" GPU_SITES="$GPU_SITES" PROXY_IP="$PROXY_IP" python3 - <<'PY'
import os
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError as exc:  # pragma: no cover - runtime safeguard
    raise SystemExit("[deploy] PyYAML est requis pour générer la configuration Prometheus") from exc

sites_env = os.getenv("CLIENT_SITES", "")
gpu_sites_env = os.getenv("GPU_SITES", "")
proxy_ip = os.getenv("PROXY_IP", "127.0.0.1")

sites = [entry.split(":", 1) for entry in sites_env.split(",") if ":" in entry]
gpu_sites = [entry.split(":", 1) for entry in gpu_sites_env.split(",") if ":" in entry]

config = {
    "global": {"scrape_interval": "5s"},
    "scrape_configs": [
        {
            "job_name": "cadvisor-hub",
            "static_configs": [{"targets": [f"{proxy_ip}:8081"]}],
        },
        {
            "job_name": "cadvisor-clients",
            "static_configs": [{"targets": [f"{ip}:8080" for _, ip in sites]}],
        },
    ],
}

dcgm_targets = [f"{ip}:9400" for _, ip in gpu_sites]
if dcgm_targets:
    config["scrape_configs"].append(
        {
            "job_name": "dcgm-exporters",
            "static_configs": [{"targets": dcgm_targets}],
        }
    )

prometheus_dir = Path("monitoring/prometheus")
prometheus_dir.mkdir(parents=True, exist_ok=True)
output_file = prometheus_dir / "prometheus.yml"
output_file.write_text(yaml.dump(config, sort_keys=False))
print(f"[deploy] Prometheus config updated with all sites: {output_file}")
PY
}

distribute_prometheus_config() {
  local config_path="${ROOT_DIR}/monitoring/prometheus/prometheus.yml"
  local targets=()

  targets+=("$PROXY_IP")

  for SITE_ENTRY in "${SITES[@]}"; do
    local SITE_IP=${SITE_ENTRY#*:}
    targets+=("$SITE_IP")
  done

  for TARGET in "${targets[@]}"; do
    local remote_path
    if [[ "$TARGET" == "$PROXY_IP" ]]; then
      remote_path="$PROXY_HOST_PATH"
    else
      remote_path="${SITE_HOST_PATHS[$TARGET]:-$REMOTE_PATH}"
    fi

    echo "[deploy] Distribution du fichier Prometheus vers ${TARGET}:${remote_path}"
    ssh -o BatchMode=yes "$TARGET" "mkdir -p ${remote_path}/monitoring/prometheus"
    rsync -e "ssh -o BatchMode=yes -o ConnectTimeout=10" --rsync-path="bash -c 'rsync \"\$@\"' --" -avz "$config_path" "${TARGET}:${remote_path}/monitoring/prometheus/prometheus.yml"
  done
}

restart_prometheus_services() {
  local contexts=("$@")
  if [[ ${#contexts[@]} -eq 0 ]]; then
    return
  fi

  for CTX in "${contexts[@]}"; do
    echo "[deploy] Redémarrage de Prometheus sur ${CTX}"
    local context_host_path=${CONTEXT_HOST_PATHS[$CTX]:-$HOST_PROJECT_PATH}
    local context_site_name=${CONTEXT_SITE_NAMES[$CTX]:-}
    HOST_PROJECT_PATH="$context_host_path" SITE_NAME="$context_site_name" \
      docker --context "$CTX" compose --project-directory "$PROJECT_DIR" up -d prometheus
    check_services_health "$CTX"
  done
}

ensure_ssh_access "$PROXY_IP"
create_context proxy-node "$PROXY_IP"

ensure_rsync "$PROXY_IP"
PROXY_HOST_PATH=$(resolve_remote_path "$PROXY_IP" "$REMOTE_PATH")
sync_repo "$PROXY_IP" "$PROXY_HOST_PATH"

echo "[deploy] Démarrage du hub sur le proxy (${PROXY_IP})"
HOST_PROJECT_PATH="$PROXY_HOST_PATH" docker --context proxy-node compose --profile hub --project-directory "$PROJECT_DIR" up -d --build
check_services_health proxy-node

IFS=',' read -ra SITES <<< "$CLIENT_SITES"
if [[ ${#SITES[@]} -eq 0 ]]; then
  echo "[deploy] Aucun site client fourni dans CLIENT_SITES" >&2
  exit 1
fi

PRIMARY_SITE_IP=""
GPU_SITES=()

for SITE_ENTRY in "${SITES[@]}"; do
  SITE_NAME=${SITE_ENTRY%%:*}
  SITE_IP=${SITE_ENTRY#*:}

  if [[ -z "$SITE_NAME" || -z "$SITE_IP" || "$SITE_NAME" == "$SITE_IP" ]]; then
    echo "[deploy] Entrée CLIENT_SITES invalide: ${SITE_ENTRY}" >&2
    continue
  fi

  if [[ -z "$PRIMARY_SITE_IP" ]]; then
    PRIMARY_SITE_IP="$SITE_IP"
  fi

  CONTEXT_NAME="ctx-${SITE_NAME}"

  echo "-------------------------------------------------------"
  echo "[deploy] Déploiement du site : ${SITE_NAME} (${SITE_IP})"
  echo "-------------------------------------------------------"

  ensure_ssh_access "$SITE_IP"
  ensure_rsync "$SITE_IP"
  CURRENT_HOST_PATH=$(resolve_remote_path "$SITE_IP" "$REMOTE_PATH")
  SITE_HOST_PATHS["$SITE_IP"]="$CURRENT_HOST_PATH"
  sync_repo "$SITE_IP" "$CURRENT_HOST_PATH"
  create_context "$CONTEXT_NAME" "$SITE_IP"

  GPU_PRESENT=$(detect_remote_gpu "$SITE_IP")

  COMPOSE_FILES=(-f "$PROJECT_DIR/compose.yaml")
  PROFILE_ARGS=(--profile client --profile monitor)

  if [[ "$GPU_PRESENT" == "true" ]]; then
    echo "[deploy] GPU détecté sur ${SITE_NAME} : activation des réservations GPU et des métriques DCGM"
    COMPOSE_FILES+=(-f "$PROJECT_DIR/compose.gpu.yaml")
    PROFILE_ARGS+=(--profile monitor-gpu)
    GPU_SITES+=("${SITE_NAME}:${SITE_IP}")
  else
    echo "[deploy] Aucun GPU détecté sur ${SITE_NAME} : déploiement en mode CPU, le GPU sera pris en compte automatiquement si ajouté ultérieurement"
  fi

  HOST_PROJECT_PATH="$CURRENT_HOST_PATH" SITE_NAME="$SITE_NAME" \
    docker --context "$CONTEXT_NAME" compose \
      --project-directory "$PROJECT_DIR" \
      "${COMPOSE_FILES[@]}" \
      "${PROFILE_ARGS[@]}" \
      up -d --build

  PROMETHEUS_CONTEXTS+=("$CONTEXT_NAME")
  CONTEXT_HOST_PATHS["$CONTEXT_NAME"]="$CURRENT_HOST_PATH"
  CONTEXT_SITE_NAMES["$CONTEXT_NAME"]="$SITE_NAME"

  check_services_health "$CONTEXT_NAME"

done

GPU_SITES=$(IFS=','; echo "${GPU_SITES[*]}")
generate_prometheus_config
distribute_prometheus_config
restart_prometheus_services "${PROMETHEUS_CONTEXTS[@]}"

echo "✅ Déploiement terminé"
echo "🔗 Hub Fleet API: http://${PROXY_IP}:${HUB_PORT}"
if [[ -n "$PRIMARY_SITE_IP" ]]; then
  echo "📊 Grafana: http://${PRIMARY_SITE_IP}:${GRAFANA_PORT} (admin/admin)"
  echo "📈 Prometheus: http://${PRIMARY_SITE_IP}:${PROMETHEUS_PORT}"
fi
