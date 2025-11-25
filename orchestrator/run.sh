#!/bin/bash
set -e

USE_TLS=${USE_TLS:-false}

# Configuration TLS pour le SuperLink (Fleet API = lien vers les clients)
#
# Remarque importante : flower-superexec (ServerApp) ne supporte pas TLS à la
# date actuelle. Le SuperLink sert à la fois la Fleet API (clients) ET le
# ServerAppIo, donc activer TLS côté SuperLink obligerait aussi le ServerApp à
# parler TLS… ce qu'il ne sait pas faire. Pour éviter les erreurs de handshake
# observées précédemment, on force donc SuperLink à rester en clair tant que
# superexec n'offre pas la prise en charge TLS.
TLS_FLAGS="--insecure"
if [[ "${USE_TLS,,}" == "true" ]]; then
    if [[ -f "$CA_CERT_PATH" && -f "$SERVER_CERT_PATH" && -f "$SERVER_KEY_PATH" ]]; then
        echo "[orchestrator] TLS demandé, mais flower-superexec ne supporte pas TLS sur la liaison ServerAppIo."
        echo "[orchestrator] SuperLink démarrera donc en clair (--insecure) pour rester compatible."
    else
        echo "[orchestrator] TLS demandé mais certificats introuvables, fallback en --insecure."
    fi
else
    echo "[orchestrator] TLS désactivé (connexion interne ServerAppIo en clair)."
fi

echo "[orchestrator] La liaison loopback SuperLink ↔ ServerAppIo reste en clair (TLS non supporté côté superexec)."

# flower-superexec impose actuellement --insecure (TLS non supporté)
SUPEREXEC_FLAGS="--insecure"

# 1. Démarrer le SuperLink (Routeur) en arrière-plan
# Il écoute sur 0.0.0.0:8080 pour les clients (Fleet) et 9091 pour le ServerApp (ServerAppIo).
# Le Control API est exposé sur un port séparé pour éviter les conflits (ex-Exec API).
echo "[orchestrator] Démarrage du SuperLink..."
flower-superlink \
    --fleet-api-address "0.0.0.0:${FLOWER_SERVER_PORT:-8080}" \
    --serverappio-api-address "0.0.0.0:${FLOWER_SERVERAPP_PORT:-9091}" \
    --control-api-address "0.0.0.0:${FLOWER_CONTROL_API_PORT:-9093}" \
    $TLS_FLAGS &

SUPERLINK_PID=$!

# Attendre que le SuperLink soit prêt (simple sleep pour ce script, ou check netcat)
sleep 5

# 2. Démarrer le ServerApp (Logique FedAvg) qui se connecte au SuperLink localement.
#    La connexion AppIo est interne (loopback), Flower 1.23 n'accepte pas de paramètres TLS ici.
echo "[orchestrator] Démarrage du ServerApp..."
flower-superexec \
    --plugin-type serverapp \
    --appio-api-address "127.0.0.1:${FLOWER_SERVERAPP_PORT:-9091}" \
    $SUPEREXEC_FLAGS &

# Attendre les processus
wait $SUPERLINK_PID
