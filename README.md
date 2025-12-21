# Plateforme Flower orchestrée (SuperLink/SuperNode) avec monitoring GPU

Cette plateforme fournit une pile d'apprentissage fédéré basée sur **Flower 1.25** empaquetée avec Docker Compose.
Elle cible un déploiement multi-hôtes (proxy/hub + nœud GPU) piloté depuis un poste d'administration Ubuntu et inclut
une supervision complète Prometheus/Grafana (CPU/RAM/GPU, réseau, état du SuperLink). Les scripts fournis automatisent
la synchronisation du dépôt, la génération des certificats TLS et le démarrage des services via Docker Contexts.

## Sommaire
- [Architecture et composants](#architecture-et-composants)
- [Arborescence du dépôt](#arborescence-du-dépôt)
- [Prérequis](#prérequis)
- [Configuration](#configuration)
- [Déploiement automatisé multi-hôtes](#déploiement-automatisé-multi-hôtes)
- [Exécution locale (monohôte) pour test](#exécution-locale-monohôte-pour-test)
- [Services Docker Compose](#services-docker-compose)
- [Applications Flower](#applications-flower)
- [Ajouter un nouveau site client](#ajouter-un-nouveau-site-client)
- [Entraîner un modèle réel : workflow](#entraîner-un-modèle-réel--workflow)
- [Monitoring](#monitoring)
- [Logs, diagnostics et nettoyage](#logs-diagnostics-et-nettoyage)
- [FAQ rapide](#faq-rapide)

## Architecture et composants
```
Admin Ubuntu                      Proxy / Hub                         DGX / SuperNode + GPU
------------------------------    --------------------------------    ------------------------------------
- Docker CLI + contexts           - superlink (Fleet API 8080)        - supernode (connecté au hub)
- scripts/deploy.sh               - serverapp (FedAvg)                - clientapp (PyTorch + Flower client)
- scripts/generate_certs.sh       - cadvisor-hub (metrics)            - dcgm-exporter (metrics GPU)
                                  - ports hub : 8080/9091/9093        - cadvisor (metrics conteneurs)
                                                                      - prometheus + grafana
```
- **Profiles Compose** : `hub` (proxy), `client` (DGX), `monitor` (DGX). Ils peuvent être lancés ensemble ou séparément.
- **Réseau** : le SuperNode se connecte au SuperLink via `${PROXY_IP}:${HUB_PORT}` ; les AppIo internes utilisent les ports
  9091 (hub) et 9094 (client) exposés uniquement sur le réseau docker.

## Arborescence du dépôt
```
.
├── compose.yaml                   # Orchestration hub/client/monitoring via profils
├── .env.example                   # Variables partagées (copier en .env)
├── scripts/
│   ├── deploy.sh                  # Déploiement automatisé via Docker Context + rsync
│   ├── remove.sh                  # Supprime les éléments déployé
│   └── generate_certs.sh          # Génération CA + certificats TLS serveur/client
├── orchestrator/                  # ServerApp Flower (hub)
│   ├── Dockerfile
│   ├── app/server.py              # FedAvg, rounds pilotables via NUM_ROUNDS/run_config
│   └── run.sh                     # Lance flower-superexec vers le SuperLink
├── client/                        # ClientApp Flower (PyTorch)
│   ├── Dockerfile
│   ├── app/client.py              # NumPyClient avec données synthétiques, hyperparams dynamiques
│   └── run.sh                     # Lance flower-superexec vers le SuperNode local
└── monitoring/
    ├── prometheus.tmpl.yml        # Template Prometheus (rendu avec IP proxy/DGX)
    ├── prometheus/prometheus.yml  # Fichier généré/utilisé par le conteneur
    └── grafana-provisioning/      # Datasource + dashboard « Flower Federated Overview »
```

## Prérequis
- Poste d'**administration Ubuntu** avec Docker et le plugin Docker Compose.
- Accès **SSH sans mot de passe** vers :
  - `proxy-data` (héberge le SuperLink/hub) → IP `${PROXY_IP}`
  - `dgx` (héberge le SuperNode + client + monitoring) → IP `${DGX_IP}`
- GPU NVIDIA et runtime CUDA actifs sur le DGX (images `pytorch/pytorch` + `dcgm-exporter`).
- Fichier `~/.ssh/config` configuré pour les hôtes `proxy-data` et `dgx` (utilisateur, clé, etc.).
- (Optionnel) Accès sudo sur les hôtes pour installer `rsync` si absent.

## Configuration
1. Copiez le modèle :
   ```bash
   cp .env.example .env
   ```
2. Ajustez les variables clés :
   - **Réseau** : `PROXY_IP`, `DGX_IP`, `HUB_PORT`
   - **Flower** : `FLWR_VERSION` (1.25.0), `NUM_ROUNDS` (rounds serveur)
   - **Hyperparamètres client** : `BATCH_SIZE`, `LEARNING_RATE` (utilisés par `client/app/client.py`)
   - **Monitoring** : `GRAFANA_PORT`, `PROMETHEUS_PORT`
   - **Déploiement** : `HOST_PROJECT_PATH` si le chemin du dépôt côté hôte diffère (`bind mounts` Prometheus/Grafana)

## Déploiement automatisé multi-hôtes
Exécuté depuis le poste admin (répertoire racine du dépôt).

1) Rendez les scripts exécutables si besoin :
```bash
chmod +x scripts/deploy.sh scripts/generate_certs.sh
```

2) (Optionnel) Générer les certificats TLS (SuperLink/ SuperNode) :
```bash
./scripts/generate_certs.sh \
  SERVER_SAN="IP:${PROXY_IP},DNS:proxy" \
  CLIENT_SAN="IP:${DGX_IP},DNS:dgx"
```
Les certificats sont placés dans `certs/` (non versionné).

3) Lancez le déploiement complet :
```bash
./scripts/deploy.sh
```
Le script :
- charge `.env` et **rend** `monitoring/prometheus/prometheus.yml` depuis `monitoring/prometheus.tmpl.yml` avec les IP réelles ;
- vérifie/installe `rsync` sur chaque hôte ;
- crée les **Docker Contexts** `proxy-node` et `dgx-node` (SSH) si absents ;
- synchronise le dépôt sur chaque hôte (`rsync --delete`) ;
- démarre les profils Compose : hub sur le proxy, client + monitoring sur le DGX ;
- affiche les URLs utiles à la fin du déploiement.

4) Points d'accès après déploiement :
- Fleet API hub : `http://${PROXY_IP}:${HUB_PORT}`
- Grafana : `http://${DGX_IP}:${GRAFANA_PORT}` (admin/admin par défaut)
- Prometheus : `http://${DGX_IP}:${PROMETHEUS_PORT}`

## Exécution locale (monohôte) pour test
Pour expérimenter sans SSH (tout sur la même machine) :
```bash
docker compose --profile hub --profile client --profile monitor up -d --build
```
- Le fichier `monitoring/prometheus/prometheus.yml` versionné pointe par défaut sur `cadvisor:8080` et `${PROXY_IP}:8081`.
  Pour surveiller le hub localement, assurez-vous que `PROXY_IP` vaut `127.0.0.1` ou regénérez le fichier via `scripts/deploy.sh`.
- Les conteneurs utilisent les images locales construites (`orchestrator`, `client`) ; GPU requis pour `clientapp`.

Arrêt local :
```bash
docker compose --profile hub --profile client --profile monitor down
```

## Services Docker Compose
Principaux services définis dans `compose.yaml` :
- **superlink** (`hub`) : image `flwr/superlink:${FLWR_VERSION}` ; expose 8080 (Fleet API), 9091 (ServerAppIo) et 9093 (Control).
- **serverapp** (`hub`) : build `./orchestrator` ; exécute le ServerApp Flower FedAvg ; lit `NUM_ROUNDS` ou `run_config`.
- **supernode** (`client`) : image `flwr/supernode:${FLWR_VERSION}` ; se connecte au SuperLink via `${PROXY_IP}:${HUB_PORT}`.
- **clientapp** (`client`) : build `./client` ; PyTorch + Flower NumPyClient ; hyperparamètres via env ou `run_config`.
- **dcgm-exporter** (`monitor`) : expose les métriques GPU (9400).
- **cadvisor / cadvisor-hub** (`monitor`) : métriques CPU/Mem/Réseau des conteneurs côté DGX et proxy.
- **prometheus** (`monitor`) : charge `monitoring/prometheus/prometheus.yml` (montage en lecture seule).
- **grafana** (`monitor`) : provisionne datasource Prometheus et dashboard par défaut, persistance via volume `grafana-storage`.

## Applications Flower
### Orchestrateur (ServerApp)
- **Code** : `orchestrator/app/server.py`
- **Stratégie** : `FedAvg` avec `min_*_clients=1`.
- **Rounds** : `NUM_ROUNDS` (variable d'env) sinon `run_config['num-server-rounds']` (défaut 5).
- **Entrée** : démarré via `orchestrator/run.sh` avec `flower-superexec --plugin-type serverapp --appio-api-address superlink:9091`.

### Client PyTorch (ClientApp)
- **Code** : `client/app/client.py`
- **Modèle** : réseau simple (Flatten + Linear/ReLU + Linear) sur données synthétiques générées à la volée.
- **Hyperparamètres** :
  - `n-local-epochs` (run_config) ou env `N_LOCAL_EPOCHS` (défaut 1)
  - `batch-size` / env `BATCH_SIZE` (défaut 64)
  - `learning-rate` / env `LEARNING_RATE` (défaut 0.01)
- **Device** : sélection automatique `cuda` si disponible, sinon CPU.
- **Entrée** : démarré via `client/run.sh` avec `flower-superexec --plugin-type clientapp --appio-api-address supernode:9094`.

## Ajouter un nouveau site client
Dans l'architecture Flower 1.x (SuperLink/SuperNode), chaque nouveau site est un SuperNode. Deux approches sont possibles :

### Option manuelle (nouvelle machine)
- Ajouter un nouvel hôte (ex. `site-B`) accessible en SSH et déclaré dans `~/.ssh/config`.
- Exécuter `scripts/deploy.sh` en ciblant cet hôte (variable de contexte) ou adapter le script pour boucler sur une liste d'IP.

### Option automatique (modification du script)
Pour gérer plusieurs clients automatiquement, exposez une liste d'IP dans `.env` puis bouclez dans `scripts/deploy.sh` :

```bash
# Exemple de logique à ajouter dans deploy.sh
IFS=',' read -ra ADDR <<< "$CLIENT_IPS"
for IP in "${ADDR[@]}"; do
  create_context "node-${IP}" "${IP}"
  sync_repo "${IP}"
  # Lancer uniquement le profil client sur ces nœuds
  docker --context "node-${IP}" compose --profile client up -d --build
done
```

### Sur la même machine (simulation)
Pour tester plusieurs clients sur un seul hôte, utilisez `docker compose --scale` ou dupliquez les blocs `supernode` et `clientapp` dans `compose.yaml` en adaptant les noms/ports de conteneurs pour éviter les collisions.

## Entraîner un modèle réel : workflow
Le dépôt s'appuie sur des données synthétiques pour la démonstration. Pour un projet réel :

1. **Préparer les données locales** : placer les jeux de données sur chaque machine client (ex. `./data`) et monter ce dossier dans `compose.yaml` pour le service `clientapp`.
2. **Adapter le client** (`client/app/client.py`) :
   - Remplacer `SimpleNet` par le modèle cible (ResNet, BERT, etc.).
   - Remplacer `generate_synthetic_data` par un chargement des données locales (CSV, images, etc.).
3. **Adapter l'orchestrateur** (`orchestrator/app/server.py`) :
   - Choisir une stratégie adaptée (`FedProx`, `FedOpt`, etc.).
   - Ajuster les paramètres de participation (`min_fit_clients`, etc.).
4. **Lancer l'entraînement** : avec Flower 1.x, serveur et clients tournent en tâche de fond (via `flower-superexec`). Une commande
   `flwr run` ou la connexion du nombre requis de clients déclenche l'entraînement selon la configuration définie dans `server.py`.

## Monitoring
- **Prometheus**
  - Template : `monitoring/prometheus.tmpl.yml` (IP paramétrables via `.env`).
  - Fichier monté : `monitoring/prometheus/prometheus.yml` (généré par `deploy.sh`).
  - Scrape : `cadvisor` (client), `cadvisor-hub` (proxy), `dcgm-exporter` (GPU DGX).
- **Grafana**
  - Datasource provisionnée : `Prometheus` (`monitoring/grafana-provisioning/datasources/datasource.yml`).
  - Dashboard : `Flower Federated Overview` (`monitoring/grafana-provisioning/dashboards/json/flower-overview.json`).
  - Affiche CPU/RAM des stacks hub/client, trafic réseau, et utilisation GPU (DCGM).

## Logs, diagnostics et nettoyage
- **Vérifier les conteneurs après déploiement** :
  ```bash
  docker --context proxy-node ps --filter "name=fl-"
  docker --context dgx-node ps --filter "name=fl-"
  ```
- **Logs** :
  - Hub : `docker --context proxy-node logs -f fl-serverapp`
  - Client : `docker --context dgx-node logs -f fl-clientapp`
- **Diagnostic santé (deploy.sh)** : le script remonte les services `exited` ou `unhealthy` et affiche leurs logs (optionnellement en suivi temps réel via `DEPLOY_LOG_FOLLOW=true`).
- **Rapports Flower** (proxy) :
  ```bash
  docker --context proxy-node exec fl-serverapp flwr list
  ```
- **Nettoyage** :
  ```bash
  docker compose --profile hub down                 # Proxy
  docker compose --profile client --profile monitor down   # DGX
  ```

## FAQ rapide
- **Comment changer le nombre de rounds ?** Définir `NUM_ROUNDS` dans `.env` ou passer `run_config['num-server-rounds']` via la Fleet API.
- **Comment ajuster le modèle ?** Modifier `client/app/client.py` (classe `SimpleNet` ou la génération de données) puis reconstruire `clientapp`.
- **GPU non disponible ?** Le client bascule sur CPU automatiquement, mais le conteneur nécessite toujours l'image CUDA ; pour un test 100% CPU,
  adapter l'image de base dans `client/Dockerfile`.
- **Quelles versions sont utilisées ?** Flower `1.25.0`, PyTorch `2.4.1-cuda12.4-cudnn9-runtime` côté client.
