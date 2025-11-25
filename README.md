# Plateforme d'apprentissage fédéré Flower (SuperLink/SuperNode)

Ce dépôt fournit une démonstration complète d'apprentissage fédéré avec **Flower 1.23** en s'appuyant sur l'architecture moderne **SuperLink / SuperNode**. Deux images Docker sont proposées (orchestrateur et client DGX) ainsi qu'une boîte à outils pour construire, déployer et diagnostiquer un pipeline gRPC sécurisé (TLS/mTLS) sans API dépréciée.

## Contenu du dépôt

```
./
├─ orchestrator/                 # Image Flower orchestrateur (SuperLink + ServerApp)
│  ├─ Dockerfile                 # Python 3.11 slim, démarre run.sh
│  ├─ requirements.txt           # Dépendances (flwr)
│  ├─ app/server.py              # ServerApp FedAvg
│  └─ run.sh                     # Lance flower-superlink puis flower-server-app
├─ client/                       # Image client DGX (GPU requis)
│  ├─ Dockerfile                 # Basée sur pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime
│  ├─ requirements.txt           # flwr + torchmetrics
│  ├─ app/client.py              # ClientApp NumPyClient avec MLP et données synthétiques
│  └─ run.sh                     # Lance flower-supernode pointé vers le SuperLink
├─ scripts/
│  ├─ generate_self_signed_certs.sh # Génération CA + certificats serveur/client pour TLS/mTLS
│  ├─ deploy_windows_e2e.py      # Déploiement/tests orchestrateur + DGX via SSH (Windows/Linux)
│  └─ cleanup_proxy_dgx.py       # Arrêt des conteneurs, suppression images et dépôt sur les hôtes
├─ build_docker_FL.sh            # Construction des images (orchestrateur, client ou les deux)
└─ run_docker_FL.sh              # Lancement des conteneurs avec options TLS/mTLS et détaché
```

## Architecture applicative (sans API dépréciée)

- **Orchestrateur** : `orchestrator/run.sh` démarre un **SuperLink** (port Fleet API par défaut `8080`, Exec API `9091`) puis le **ServerApp** défini dans `orchestrator/app/server.py` via `flower-superexec` (API moderne recommandée). La stratégie `FedAvg` impose `min_fit_clients=min_available_clients=min_evaluate_clients=1` et le nombre de rounds peut être piloté via `run_config["num-server-rounds"]`. Le TLS/mTLS est **obligatoire** côté SuperLink (certificats exigés, aucun mode `--insecure`), tandis que la connexion `flower-superexec` reste interne (loopback) sans paramètres TLS additionnels.【F:orchestrator/run.sh†L1-L48】【F:orchestrator/app/server.py†L1-L23】
- **Client DGX** : `client/run.sh` invoque `flower-supernode` vers le SuperLink (adresse `SERVER_ADDRESS`, ex. `10.200.241.101:8443`). Le **ClientApp** construit dans `client/app/client.py` expose un `NumPyClient` PyTorch minimal (MLP 28x28, données synthétiques) avec hyperparamètres issus de l'environnement (`N_LOCAL_EPOCHS`, `BATCH_SIZE`, `LEARNING_RATE`). Le mode `--insecure` est interdit ; TLS est imposé via `--root-certificates` avec la CA fournie (`USE_TLS=true` par défaut).【F:client/run.sh†L1-L31】【F:client/app/client.py†L1-L81】

Cette approche élimine les avertissements `start_server()/start_client()` dépréciés et améliore la robustesse réseau (retries intégrés côté SuperNode).

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

1. **Orchestrateur** (SuperLink sur 8080 → port hôte via `HOST_PORT_OVERRIDE`, Exec API 9091) :
   ```bash
   HOST_PORT_OVERRIDE=8443 ./run_docker_FL.sh orchestrator --self-signed --detach
   ```

2. **Client DGX** (se connecte au port SuperLink exposé sur le proxy, TLS activé si `--self-signed`) :
   ```bash
   SERVER_ADDRESS=10.200.241.101:8443 USE_TLS=true ./run_docker_FL.sh client --self-signed --detach
   ```

Chaque lancement crée les répertoires `certs/` (si `--self-signed`) et `data/` côté client, monte les certificats, applique les variables d'environnement et démarre les conteneurs `fl-orchestrator` et `fl-client-dgx`. L'option `--detach` garde les conteneurs actifs après le script ; définissez `KEEP_CONTAINER_LOGS=true` pour conserver les conteneurs une fois arrêtés.【F:run_docker_FL.sh†L1-L112】【F:run_docker_FL.sh†L113-L201】

## Variables d'environnement principales

### Orchestrateur (`orchestrator/.env` chargé par `run_docker_FL.sh`)

- `FLOWER_SERVER_PORT` (port Fleet API SuperLink, défaut `8080`)
- `FLOWER_SERVERAPP_PORT` (port Exec API pour ServerApp, défaut `9091`)
- `HOST_PORT_OVERRIDE`, `HOST_SERVERAPP_PORT_OVERRIDE` (mapping des ports hôtes ; ex. `8443` pour exposer le SuperLink)
- `USE_TLS` (`true`/`false`)
- `CA_CERT_PATH`, `SERVER_CERT_PATH`, `SERVER_KEY_PATH` (chemins montés dans le conteneur)

### Client (`client/.env` chargé par `run_docker_FL.sh`)

- `SERVER_ADDRESS` (ex. `10.200.241.101:8443`, cible le port SuperLink exposé)
- `CLIENT_ID` (identifiant Flower, défaut `dgx-client`)
- `N_LOCAL_EPOCHS`, `BATCH_SIZE`, `LEARNING_RATE` (hyperparamètres locaux)
- `USE_TLS` (`true`/`false`)
- `CA_CERT_PATH`, `CLIENT_CERT_PATH`, `CLIENT_KEY_PATH`
- `DATA_DIR` (montage des données locales si nécessaire)

Si un fichier `.env` est absent, le script `run_docker_FL.sh` tente de basculer vers le `.env.example` correspondant. L'option `--self-signed` force `USE_TLS=true` côté client et ajoute le SAN adéquat pour l'adresse du serveur.【F:run_docker_FL.sh†L35-L112】【F:run_docker_FL.sh†L140-L188】

## Automatisation multi-hôtes (Windows ou Linux)

Le script `scripts/deploy_windows_e2e.py` orchestre l'ensemble depuis une machine de pilotage :

1. Vérifie ou installe **Docker** et **Git** sur chaque hôte cible (via `curl https://get.docker.com | sh` si nécessaire).【F:scripts/deploy_windows_e2e.py†L6-L56】
2. Clone ou met à jour ce dépôt sur les hôtes (chemins par défaut `~/federated` pour l'orchestrateur, `/raid/workspace/qladane/federated` pour le DGX).【F:scripts/deploy_windows_e2e.py†L20-L33】
3. Prépare les `.env` à partir des exemples, applique une configuration minimale de test (1 client suffisant, port SuperLink configurable, TLS/mTLS activé). Le port demandé (par défaut 8443) est imposé ; si le port est occupé côté proxy ou filtré depuis le DGX, le script échoue pour garantir la cohérence de la topologie.【F:scripts/deploy_windows_e2e.py†L731-L786】【F:scripts/deploy_windows_e2e.py†L1451-L1494】
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
  --server-port 8443 \
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
cd ~/federated/federatedlearning && HOST_PORT_OVERRIDE=8443 ./run_docker_FL.sh orchestrator --self-signed --detach
cd /raid/workspace/qladane/federated/federatedlearning && SERVER_ADDRESS=10.200.241.101:8443 ./run_docker_FL.sh client --self-signed --detach
```

## Conseils TLS/mTLS

- Ajoutez l'adresse ou le FQDN du hub dans `CERT_SERVER_SAN` avant de générer les certificats pour éviter les erreurs de nom d'hôte (ex. `CERT_SERVER_SAN="DNS:proxy.local,IP:10.200.241.101"`).【F:run_docker_FL.sh†L140-L180】
- Pour activer mTLS, fournissez également `CLIENT_CERT_PATH` et `CLIENT_KEY_PATH` côté client ; le SuperNode vérifiera la CA serveur via `--root-certificates` et pourra être étendu avec les options `--auth-*` si nécessaire. Le mode `--insecure` n'est plus supporté par les scripts de lancement.【F:client/run.sh†L1-L31】
- En environnement de test, l'option `--self-signed` s'occupe de générer et monter les certificats côté orchestrateur et client.

## Licence

Projet fourni comme exemple de pipeline Flower sécurisé. Adaptez les scripts et paramètres à vos contraintes internes avant toute utilisation en production.
