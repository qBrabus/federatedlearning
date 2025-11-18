# Plateforme Flower fédérée (orchestrateur + client DGX)

Ce dépôt fournit une démonstration complète d'apprentissage fédéré basée sur **Flower** avec un orchestrateur (hub) et un client de calcul DGX. La stack cible :

- **Orchestrateur** sur `PROXY-DATA (10.200.241.101)` dans `~/federated`.
- **Client DGX** sur `dgxh200 (10.200.50.45)` dans `/raid/workspace/qladane/federated`.
- **Transport** : gRPC sur 443 avec **mTLS** (les certificats du hub sont réutilisés par les clients).
- **Conteneurs** Docker isolés (aucune installation système requise côté hôtes, hormis Docker et Git déjà présents).

Le dépôt inclut :

- Un serveur Flower minimal (`orchestrator/app/server.py`) et un client PyTorch (`client/app/client.py`).
- Des scripts shell pour builder/lancer les conteneurs et générer des certificats auto-signés.
- Un script Python de déploiement **multi-hôtes** prévu pour être exécuté depuis un poste Windows, qui automatise le clone, la construction, l'exécution, les tests de connectivité gRPC/mTLS, puis l'arrêt propre des conteneurs.

## Contenu et architecture

```
./
├─ orchestrator/          # Image du hub Flower (serveur gRPC, TLS/mTLS)
│  ├─ app/server.py       # Démarrage du serveur Flower et chargement des certificats
│  └─ .env.example        # Variables d'environnement orchestrateur (ports, seuils clients...)
├─ client/                # Image du client DGX (PyTorch + Flower)
│  ├─ app/client.py       # Client Flower avec données synthétiques (Smoke test GPU/CUDA 12.4)
│  └─ .env.example        # Variables d'environnement client (adresse serveur, TLS...)
├─ scripts/
│  ├─ deploy_proxy_dgx.py # Déploiement Linux↔Linux (existant, via SSH)
│  ├─ deploy_windows_e2e.py # **Nouveau** déploiement/validation multi-hôtes depuis Windows
│  ├─ cleanup_proxy_dgx.py# Nettoyage des conteneurs/images/dépôts
│  └─ generate_self_signed_certs.sh # Génération de certificats auto-signés (TLS/mTLS)
├─ build_docker_FL.sh     # Build des images orchestrateur/client
└─ run_docker_FL.sh       # Lancement des conteneurs orchestrateur/client
```

### Flux réseau & certificats

1. L'orchestrateur écoute en gRPC (par défaut 8080 ou 443) et charge les certificats CA/serveur.
2. Les clients montent le même CA, et facultativement leurs certificats/clefs pour mTLS.
3. Les certificats peuvent être fournis par l'infra ou générés automatiquement (self-signed) via `scripts/generate_self_signed_certs.sh`. Le client reprend les certificats générés côté hub.

### Code applicatif

- **Orchestrateur** (`orchestrator/app/server.py`)
  - Lit les variables d'environnement (`FLOWER_SERVER_ADDRESS`, `FLOWER_SERVER_PORT`, `NUM_ROUNDS`, `MIN_FIT_CLIENTS`, `MIN_AVAILABLE_CLIENTS`, etc.).
  - Charge les certificats si `CA_CERT_PATH`, `SERVER_CERT_PATH` et `SERVER_KEY_PATH` sont définis, puis démarre `fl.server.start_server` avec la stratégie `FedAvg`.
- **Client** (`client/app/client.py`)
  - Paramétrable via l'environnement (`SERVER_ADDRESS`, `CLIENT_ID`, `N_LOCAL_EPOCHS`, `BATCH_SIZE`, `LEARNING_RATE`, `USE_TLS`, chemins des certificats).
  - Crée un MLP simple, génère des données synthétiques (aucune dépendance externe), et se connecte au hub via `fl.client.start_client`. Le code gère automatiquement le cas mTLS (nouvelle/ancienne signature Flower).

## Prérequis

- **Docker** et **Git** déjà installés sur les hôtes `PROXY-DATA` et `dgxh200`. Aucune installation système additionnelle n'est requise ; tout se fait dans les conteneurs.
- Accès SSH fonctionnel depuis la machine de pilotage (Windows) vers `PROXY-DATA` et `dgxh200` (entrées SSH ou clé dans `~/.ssh`).
- Python 3.10+ sur la machine Windows (pour exécuter le script de déploiement).

## Déploiement automatisé depuis Windows (recommandé)

Le nouveau script `scripts/deploy_windows_e2e.py` orchestre l'ensemble du cycle sur les deux hôtes (clone → build → run → tests → arrêt) avec journalisation complète.

### Étapes réalisées

1. **Clone/pull** du dépôt sur chaque hôte (dans les chemins cibles : `~/federated` pour l'orchestrateur, `/raid/workspace/qladane/federated` pour le client).
2. **Copie des .env** exemples vers `.env` si absent (possibilité de surcharger via arguments).
3. **Build Docker** de l'orchestrateur et du client avec certificats auto-signés (le client récupère le CA/serveur généré côté hub).
4. **Run Docker** en détaché sur chaque hôte.
5. **Tests automatiques** :
   - Ping SSH vers chaque hôte.
   - Vérification de la version Docker.
   - Vérification que les conteneurs tournent (`docker ps`).
   - Test gRPC/mTLS depuis le conteneur client (`grpc.channel_ready_future` avec CA et éventuellement cert client) vers l'adresse serveur.
   - Lecture rapide des logs Flower pour confirmer un échange de paramètres.
6. **Arrêt propre** des conteneurs (les images restent en cache pour un prochain run).
7. **Résumé final** indiquant comment relancer les conteneurs manuellement.

### Utilisation

Depuis votre machine Windows (PowerShell ou Git Bash) :

```powershell
python scripts/deploy_windows_e2e.py \  
  --proxy-host PROXY-DATA \                    # entrée SSH orchestrateur
  --proxy-base "~/federated" \                 # répertoire clone orchestrateur
  --dgx-host dgxh200 \                          # entrée SSH client DGX
  --dgx-base "/raid/workspace/qladane/federated" \  # répertoire clone client
  --repo-url "https://github.com/qBrabus/federatedlearning" \
  --log-file deploy.log \                       # log détaillé (console + fichier)
  --server-port 443 \                           # port gRPC du hub (443 recommandé)
  --self-signed                                 # génère et synchronise les certs
```

Options principales :

- `--proxy-host` / `--dgx-host` : alias/entrées SSH vers les hôtes.
- `--proxy-base` / `--dgx-base` : dossiers cibles pour cloner le dépôt.
- `--repo-url` : URL Git (par défaut ce dépôt).
- `--log-file` : fichier de log (par défaut `deploy_YYYYMMDD_HHMMSS.log`).
- `--server-port` : port gRPC exposé côté orchestrateur (443 par défaut pour mTLS).
- `--self-signed` : génère un CA local + certs hub/client et les synchronise vers le DGX.

> Le script ne tente **aucune installation système** (pas d'`apt install`). Il s'appuie uniquement sur Docker/Git déjà présents et exécute toutes les étapes dans les conteneurs ou via les scripts fournis.

### Résultat des tests

Le script affiche et journalise :

- La réussite des connexions SSH et de la détection Docker.
- L'état des conteneurs (`fl-orchestrator`, `fl-client-dgx`).
- Le résultat du handshake gRPC/mTLS (`channel_ready_future` depuis le conteneur client).
- Un extrait des logs Flower (client/server) pour confirmer l'échange de paramètres.
- Un résumé final avec instructions pour relancer manuellement :
  - Orchestrateur : `./run_docker_FL.sh orchestrator --self-signed --detach`
  - Client DGX : `./run_docker_FL.sh client --self-signed --detach`

## Déploiement manuel (Linux ↔ Linux)

Pour un contrôle manuel, vous pouvez utiliser les scripts shell.

### 1. Construire les images

```bash
./build_docker_FL.sh all --self-signed
```
- Génère `fl-orchestrator:latest` et `fl-client-dgx:latest`.
- Crée des certificats auto-signés dans `./certs` (le client récupère le CA du hub).

### 2. Lancer l'orchestrateur

```bash
# Adapter HOST_PORT_OVERRIDE si vous souhaitez exposer le hub sur 443
HOST_PORT_OVERRIDE=443 ./run_docker_FL.sh orchestrator --self-signed --detach
```
- Charge `orchestrator/.env` (ou `.env.example` par défaut).
- Monte les certificats dans `/certs` (chemins par défaut déjà configurés).

### 3. Lancer le client DGX

```bash
SERVER_ADDRESS=10.200.241.101:443 ./run_docker_FL.sh client --self-signed --detach
```
- Monte le même CA + cert client dans `/certs`.
- Force TLS/mTLS si les certificats existent.

### 4. Nettoyer

```bash
python scripts/cleanup_proxy_dgx.py --proxy-host PROXY-DATA --dgx-host dgxh200 \
  --proxy-base "~/federated" --dgx-base "/raid/workspace/qladane/federated"
```
- Arrête/supprime les conteneurs/images et efface les clones.

## Variables d'environnement clés

- **Orchestrateur** (`orchestrator/.env`)
  - `FLOWER_SERVER_ADDRESS` (par défaut `0.0.0.0`)
  - `FLOWER_SERVER_PORT` (ex : `443`)
  - `GRPC_MAX_MESSAGE_LENGTH` (par défaut 512 Mo)
  - `NUM_ROUNDS`, `MIN_FIT_CLIENTS`, `MIN_AVAILABLE_CLIENTS`
  - `CA_CERT_PATH`, `SERVER_CERT_PATH`, `SERVER_KEY_PATH`
- **Client** (`client/.env`)
  - `SERVER_ADDRESS` (ex : `10.200.241.101:443`)
  - `CLIENT_ID`, `N_LOCAL_EPOCHS`, `BATCH_SIZE`, `LEARNING_RATE`
  - `USE_TLS` (true/false)
  - `CA_CERT_PATH`, `CLIENT_CERT_PATH`, `CLIENT_KEY_PATH`

## Tests locaux rapides (smoke tests)

Pour vérifier le pipeline sur une seule machine (sans DGX) :

```bash
./build_docker_FL.sh all --self-signed
HOST_PORT_OVERRIDE=8080 ./run_docker_FL.sh orchestrator --self-signed --detach
SERVER_ADDRESS=127.0.0.1:8080 USE_TLS=true ./run_docker_FL.sh client --self-signed --detach
# Inspecter les logs
docker logs -f fl-orchestrator
```

## FAQ rapide

- **Faut-il installer des paquets système ?** Non, les hôtes n'ont besoin que de Docker et Git. Le reste est encapsulé dans les images.
- **Comment personnaliser les certificats ?** Exportez `CERT_SERVER_SAN` et/ou `CERT_CLIENT_SAN` avant d'appeler `generate_self_signed_certs.sh` ou `run_docker_FL.sh --self-signed`.
- **Et si le port 443 est déjà utilisé ?** Ajustez `FLOWER_SERVER_PORT` et `HOST_PORT_OVERRIDE` côté orchestrateur, puis `SERVER_ADDRESS` côté client.

## Licence

Ce projet est fourni à titre d'exemple pour orchestrer Flower avec TLS/mTLS. Ajustez les scripts selon vos besoins internes.
