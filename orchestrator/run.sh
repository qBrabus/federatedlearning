#!/bin/bash
set -e

USE_TLS=${USE_TLS:-true}

# Configuration TLS pour le SuperLink (Fleet API = lien vers les clients)
TLS_FLAGS=""
if [[ "${USE_TLS,,}" == "true" ]]; then
    if [[ -f "$CA_CERT_PATH" && -f "$SERVER_CERT_PATH" && -f "$SERVER_KEY_PATH" ]]; then
        echo "[orchestrator] TLS activé avec certificats."
        TLS_FLAGS="--ssl-ca-certfile $CA_CERT_PATH --ssl-certfile $SERVER_CERT_PATH --ssl-keyfile $SERVER_KEY_PATH"
    else
        echo "[orchestrator] ERREUR: Fichiers certificats manquants pour TLS."
        exit 1
    fi
else
    echo "[orchestrator] ERREUR: le mode --insecure est interdit. Activez USE_TLS et fournissez des certificats."
    exit 1
fi

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

# 2. Démarrer le ServerApp (Logique FedAvg) qui se connecte au SuperLink localement
echo "[orchestrator] Démarrage du ServerApp..."
flower-superexec \
    --plugin-type serverapp \
    --serverappio-api-address "127.0.0.1:${FLOWER_SERVERAPP_PORT:-9091}" \
    $TLS_FLAGS &

# Attendre les processus
wait $SUPERLINK_PID
