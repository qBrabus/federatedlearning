# Plateforme d'apprentissage fédéré Flower (orchestrateur + client DGX)

Ce dépôt regroupe une démonstration complète d'apprentissage fédéré avec **Flower 1.23**. Il fournit deux images Docker (hub/orchestrateur et client DGX) et une boîte à outils d'automatisation pour déployer, tester et nettoyer un pipeline gRPC sécurisé par TLS/mTLS.

## Contenu du dépôt

```
./
├─ orchestrator/                 # Image Flower SuperLink (hub)
│  ├─ Dockerfile                 # Python 3.11 slim, exécute flower-superlink
│  ├─ requirements.txt           # Dépendances (flwr)
│  └─ app/server.py              # Construction dynamique des arguments TLS et ports
├─ client/                       # Image client DGX (GPU requis)
│  ├─ Dockerfile                 # Basée sur pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime
│  ├─ requirements.txt           # flwr + torchmetrics
│  └─ app/client.py              # Client Flower NumPyClient avec MLP et données synthétiques
├─ scripts/
│  ├─ generate_self_signed_certs.sh # Génération CA + certificats serveur/client pour TLS/mTLS
│  ├─ deploy_windows_e2e.py      # Déploiement/tests orchestrateur + DGX via SSH (Windows/Linux)
│  └─ cleanup_proxy_dgx.py       # Arrêt des conteneurs, suppression images et dépôt sur les hôtes
├─ build_docker_FL.sh            # Construction des images (orchestrateur, client ou les deux)
└─ run_docker_FL.sh              # Lancement des conteneurs avec options TLS/mTLS et détaché
```

## Architecture applicative

- **Orchestrateur** (`orchestrator/app/server.py`) : construit l'appel `flower-superlink` en fonction des variables d'environnement (adresse d'écoute, port gRPC, port ServerApp optionnel). Si `USE_TLS` est vrai et que les fichiers de certificats sont fournis, il ajoute les options `--ssl-certfile`, `--ssl-keyfile` et `--ssl-ca-certfile`; sinon il passe en mode `--insecure`.【F:orchestrator/app/server.py†L13-L43】
- **Client DGX** (`client/app/client.py`) : implémentation `fl.client.NumPyClient` minimaliste avec un MLP sur données synthétiques. La configuration (adresse serveur, hyperparamètres, TLS/mTLS) est chargée depuis l'environnement ; le client détecte automatiquement CUDA, gère la compatibilité des signatures Flower pour les certificats (arguments `client_certificates` ou `certificate_chain`/`private_key`) et démarre via `fl.client.start_client`.【F:client/app/client.py†L16-L129】【F:client/app/client.py†L146-L213】

## Pré-requis

- **Docker** installé sur les hôtes orchestrateur et client.
- **GPU NVIDIA + drivers + CUDA 12.4** sur le nœud client (image `pytorch` runtime).
- **OpenSSL** sur la machine qui génère les certificats (installé par défaut dans les images de build/run).
- **SSH** et **SCP** fonctionnels depuis la machine de pilotage si vous utilisez l'automatisation multi-hôtes.

## Construction des images

Depuis la racine du dépôt :

```bash
./build_docker_FL.sh orchestrator   # Image fl-orchestrator:latest
./build_docker_FL.sh client         # Image fl-client-dgx:latest (GPU)
./build_docker_FL.sh all --self-signed  # Construit les deux et génère des certificats de test
```

L'option `--self-signed` appelle `scripts/generate_self_signed_certs.sh` pour créer un CA local et des certificats serveur/client prêts à être montés dans les conteneurs.【F:build_docker_FL.sh†L32-L55】

## Génération des certificats (TLS/mTLS)

Le script `scripts/generate_self_signed_certs.sh` crée par défaut :

- `certs/ca.crt` / `certs/ca.key` (autorité de certification)
- `certs/orchestrator/server.crt` / `server.key`
- `certs/client/client.crt` / `client.key`

Il ajoute automatiquement les adresses IP locales au SAN du certificat serveur pour éviter les erreurs « peer name ... is not in peer certificate ». Vous pouvez personnaliser les répertoires et les SAN :

```bash
./scripts/generate_self_signed_certs.sh \
  --orch-dir ./certs/orchestrator \
  --client-dir ./certs/client \
  --server-san "DNS:fl-orchestrator.local,IP:10.0.0.10" \
  --client-san "DNS:fl-client.local"
```

Les certificats sont posés en lecture seule (chmod 644) et copiés dans chaque dossier pour correspondre aux chemins `.env` par défaut.【F:scripts/generate_self_signed_certs.sh†L8-L109】【F:scripts/generate_self_signed_certs.sh†L128-L146】

## Lancement manuel (Linux ↔ Linux)

1. **Orchestrateur** (ports 8080/9091 par défaut, `HOST_PORT_OVERRIDE` permet d'exposer un autre port) :
   ```bash
   HOST_PORT_OVERRIDE=443 ./run_docker_FL.sh orchestrator --self-signed --detach
   ```

2. **Client DGX** (exige un GPU et le driver NVIDIA) :
   ```bash
   SERVER_ADDRESS=10.200.241.101:9091 USE_TLS=true ./run_docker_FL.sh client --self-signed --detach
   ```

Chaque lancement crée les répertoires `certs/` (si `--self-signed`) et `data/` côté client, monte les certificats, applique les variables d'environnement et démarre les conteneurs `fl-orchestrator` et `fl-client-dgx`. L'option `--detach` garde les conteneurs actifs après le script; définissez `KEEP_CONTAINER_LOGS=true` pour conserver les conteneurs une fois arrêtés.【F:run_docker_FL.sh†L14-L111】【F:run_docker_FL.sh†L112-L201】

## Variables d'environnement principales

### Orchestrateur (`orchestrator/.env` chargé par `run_docker_FL.sh`)

- `FLOWER_SERVER_ADDRESS` (défaut `0.0.0.0`)
- `FLOWER_SERVER_PORT` (défaut `8080`, exemple `443` en production)
- `FLOWER_SERVERAPP_PORT` (API ServerApp optionnelle, défaut `9091`)
- `GRPC_MAX_MESSAGE_LENGTH` (défaut 512 Mo)
- `USE_TLS` (`true`/`false`)
- `CA_CERT_PATH`, `SERVER_CERT_PATH`, `SERVER_KEY_PATH` (chemins montés dans le conteneur)
- `HOST_PORT_OVERRIDE`, `HOST_SERVERAPP_PORT_OVERRIDE` (mapping des ports hôtes)

### Client (`client/.env` chargé par `run_docker_FL.sh`)

- `SERVER_ADDRESS` (ex. `10.200.241.101:9091`, vise le port ServerApp exposé)
- `CLIENT_ID` (identifiant Flower, défaut `dgx-client`)
- `N_LOCAL_EPOCHS`, `BATCH_SIZE`, `LEARNING_RATE` (hyperparamètres locaux)
- `USE_TLS` (`true`/`false`)
- `CA_CERT_PATH`, `CLIENT_CERT_PATH`, `CLIENT_KEY_PATH`
- `DATA_DIR` (montage des données locales si nécessaire)

Si un fichier `.env` est absent, le script `run_docker_FL.sh` tente de basculer vers le `.env.example` correspondant. L'option `--self-signed` force `USE_TLS=true` côté client et ajoute le SAN adéquat pour l'adresse du serveur.【F:run_docker_FL.sh†L35-L111】【F:run_docker_FL.sh†L142-L188】

## Automatisation multi-hôtes (Windows ou Linux)

Le script `scripts/deploy_windows_e2e.py` orchestre l'ensemble depuis une machine de pilotage :

1. Vérifie ou installe **Docker** et **Git** sur chaque hôte cible (via `curl https://get.docker.com | sh` si nécessaire).【F:scripts/deploy_windows_e2e.py†L6-L56】
2. Clone ou met à jour ce dépôt sur les hôtes (chemins par défaut `~/federated` pour l'orchestrateur, `/raid/workspace/qladane/federated` pour le DGX).【F:scripts/deploy_windows_e2e.py†L20-L33】
3. Prépare les `.env` à partir des exemples, applique une configuration minimale de test (1 client suffisant, port gRPC configurable, TLS/mTLS activé).
   - Le port serveur demandé (par défaut 443) est préféré ; s'il est occupé, le script tente automatiquement 8443 puis bascule sur un port non privilégié disponible pour éviter les blocages ACL sur des ports bas.【F:scripts/deploy_windows_e2e.py†L686-L748】【F:scripts/deploy_windows_e2e.py†L1264-L1336】
4. Construit et lance les conteneurs via `build_docker_FL.sh` et `run_docker_FL.sh`, avec certificats auto-signés optionnels.
5. Exécute des tests : connectivité SSH, disponibilité Docker, état des conteneurs, handshake gRPC/mTLS depuis le conteneur client, extraction rapide des logs Flower.
6. Arrête proprement les conteneurs et rappelle les commandes pour relancer manuellement.
7. Journalise en console et dans un fichier horodaté (`deploy_YYYYMMDD_HHMMSS.log`).

Commande type depuis PowerShell/Git Bash/Linux :

```bash
python scripts/deploy_windows_e2e.py \
  --proxy-host PROXY \
  --dgx-host DGX \
  --proxy-base "~/federated" \
  --dgx-base "/raid/workspace/qladane/federated" \
  --repo-url "https://github.com/qBrabus/federatedlearning" \
  --server-port 443 \
  --self-signed
```

> Astuce : définissez la variable d'environnement `pwdsession` si vous devez fournir un mot de passe SSH non interactif ; le script utilise `sshpass` s'il est présent ou bascule sur Paramiko côté Windows.【F:scripts/deploy_windows_e2e.py†L36-L90】【F:scripts/deploy_windows_e2e.py†L92-L151】

## Nettoyage des hôtes

Pour supprimer conteneurs, images et dépôt Git sur les deux machines :

```bash
python scripts/cleanup_proxy_dgx.py \
  --proxy-host PROXY --dgx-host DGX \
  --proxy-base "~/federated" --dgx-base "/raid/workspace/qladane/federated"
```

Le script arrête et supprime les conteneurs `fl-orchestrator` et `fl-client-dgx`, supprime toutes les images locales préfixées `fl-` puis efface le dossier du dépôt sur chaque hôte.【F:scripts/cleanup_proxy_dgx.py†L13-L64】

## Relance manuelle rapide après déploiement automatisé

Si vous avez utilisé `deploy_windows_e2e.py`, les conteneurs sont arrêtés en fin de test. Pour relancer sans tout reconstruire :

```bash
cd ~/federated/federatedlearning && ./run_docker_FL.sh orchestrator --self-signed --detach
cd /raid/workspace/qladane/federated/federatedlearning && SERVER_ADDRESS=10.200.241.101:443 ./run_docker_FL.sh client --self-signed --detach
```

## Conseils TLS/mTLS

- Ajoutez l'adresse ou le FQDN du hub dans `CERT_SERVER_SAN` avant de générer les certificats pour éviter les erreurs de nom d'hôte (ex. `CERT_SERVER_SAN="DNS:proxy.local,IP:10.200.241.101"`).【F:run_docker_FL.sh†L140-L180】
- Pour activer mTLS, fournissez également `CLIENT_CERT_PATH` et `CLIENT_KEY_PATH` côté client ; le code gère automatiquement les deux signatures d'API Flower (`client_certificates` ou `certificate_chain/private_key`).【F:client/app/client.py†L164-L206】
- En environnement de test, l'option `--self-signed` s'occupe de générer et monter les certificats côté orchestrateur et client.

## Licence

Projet fourni comme exemple de pipeline Flower sécurisé. Adaptez les scripts et paramètres à vos contraintes internes avant toute utilisation en production.
