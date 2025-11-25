#!/bin/bash
set -e

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
    echo "[orchestrator] TLS désactivé (--insecure)."
    TLS_FLAGS="--insecure"
fi

# 1. Démarrer le SuperLink (Routeur) en arrière-plan
# Il écoute sur 0.0.0.0:8080 pour les clients (Fleet) et 9091 pour le ServerApp (Exec)
echo "[orchestrator] Démarrage du SuperLink..."
flower-superlink \
    --fleet-api-address "0.0.0.0:${FLOWER_SERVER_PORT:-8080}" \
    --exec-api-address "0.0.0.0:${FLOWER_SERVERAPP_PORT:-9091}" \
    $TLS_FLAGS &

SUPERLINK_PID=$!

# Attendre que le SuperLink soit prêt (simple sleep pour ce script, ou check netcat)
sleep 5

# 2. Démarrer le ServerApp (Logique FedAvg) qui se connecte au SuperLink localement
echo "[orchestrator] Démarrage du ServerApp..."
flower-server-app \
    --superlink "127.0.0.1:${FLOWER_SERVERAPP_PORT:-9091}" \
    --app app.server:app \
    --insecure &

# Attendre les processus
wait $SUPERLINK_PID
