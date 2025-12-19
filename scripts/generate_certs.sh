#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
CERT_DIR="${ROOT_DIR}/certs"
SERVER_SAN=${SERVER_SAN:-"IP:127.0.0.1"}
CLIENT_SAN=${CLIENT_SAN:-"DNS:client.local"}

mkdir -p "${CERT_DIR}/orchestrator" "${CERT_DIR}/client"

CA_KEY="${CERT_DIR}/ca.key"
CA_CRT="${CERT_DIR}/ca.crt"
SERVER_KEY="${CERT_DIR}/orchestrator/server.key"
SERVER_CSR="${CERT_DIR}/orchestrator/server.csr"
SERVER_CRT="${CERT_DIR}/orchestrator/server.crt"
CLIENT_KEY="${CERT_DIR}/client/client.key"
CLIENT_CSR="${CERT_DIR}/client/client.csr"
CLIENT_CRT="${CERT_DIR}/client/client.crt"

printf "\n[+] Génération de l'autorité de certification...\n"
openssl req -x509 -new -nodes -newkey rsa:4096 -sha256 -days 365 \
  -keyout "$CA_KEY" -out "$CA_CRT" -subj "/CN=flower-ca"

printf "\n[+] Génération du certificat serveur (SuperLink)...\n"
cat >"${CERT_DIR}/orchestrator/ext.cnf" <<EOT
subjectAltName=${SERVER_SAN}
extendedKeyUsage=serverAuth
EOT
openssl req -new -newkey rsa:4096 -nodes -keyout "$SERVER_KEY" -out "$SERVER_CSR" -subj "/CN=flower-superlink"
openssl x509 -req -in "$SERVER_CSR" -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
  -out "$SERVER_CRT" -days 365 -sha256 -extfile "${CERT_DIR}/orchestrator/ext.cnf"

printf "\n[+] Génération du certificat client (SuperNode)...\n"
cat >"${CERT_DIR}/client/ext.cnf" <<EOT
subjectAltName=${CLIENT_SAN}
extendedKeyUsage=clientAuth
EOT
openssl req -new -newkey rsa:4096 -nodes -keyout "$CLIENT_KEY" -out "$CLIENT_CSR" -subj "/CN=flower-client"
openssl x509 -req -in "$CLIENT_CSR" -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
  -out "$CLIENT_CRT" -days 365 -sha256 -extfile "${CERT_DIR}/client/ext.cnf"

chmod 644 "$CA_CRT" "$SERVER_CRT" "$CLIENT_CRT"
chmod 600 "$CA_KEY" "$SERVER_KEY" "$CLIENT_KEY"

printf "\n[✓] Certificats générés dans ${CERT_DIR}.\n"
