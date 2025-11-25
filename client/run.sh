#!/bin/bash
set -e

# Extraction de l'adresse IP et du port depuis SERVER_ADDRESS (ex: 10.200.x.x:8443)
SUPERLINK_ADDRESS="${SERVER_ADDRESS:-127.0.0.1:8080}"

TLS_FLAGS=""
if [[ "${USE_TLS,,}" == "true" ]]; then
    # Flower 1.23 SuperNode utilise --root-certificates pour valider le serveur
    if [[ -f "$CA_CERT_PATH" ]]; then
        echo "[client] TLS activé (CA: $CA_CERT_PATH)."
        TLS_FLAGS="--root-certificates $CA_CERT_PATH"
        # Si vous utilisez le mTLS strict (client auth), il faudrait ajouter --auth-...
        # Pour l'instant, on fixe la connexion TLS serveur-authentifiée (standard)
    else
        echo "[client] TLS demandé mais CA introuvable."
        exit 1
    fi
else
    echo "[client] TLS désactivé (--insecure)."
    TLS_FLAGS="--insecure"
fi

echo "[client] Démarrage du SuperNode vers $SUPERLINK_ADDRESS..."

# Le supernode se connecte au SuperLink et exécute la ClientApp
flower-supernode \
    --superlink "$SUPERLINK_ADDRESS" \
    $TLS_FLAGS
