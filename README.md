# Déploiement Flower pour apprentissage fédéré

Ce dépôt fournit des assets Docker prêts à l'emploi, des variables d'environnement et des scripts pour assembler rapidement un environnement d'apprentissage fédéré basé sur **Flower**. Les adresses IP/FQDN définitifs sont volontairement laissés à votre convenance : renseignez vos propres valeurs dans les fichiers `.env` avant d'exécuter les scripts.

## Table des matières
1. [Aperçu rapide](#aperçu-rapide)
2. [Prérequis techniques](#prérequis-techniques)
3. [Contenu du dépôt](#contenu-du-dépôt)
4. [Configuration des environnements](#configuration-des-environnements)
5. [Construction des images Docker](#construction-des-images-docker)
6. [Exécution des conteneurs](#exécution-des-conteneurs)
7. [Déploiement automatisé PROXY + DGX](#déploiement-automatisé-proxy--dgx)
8. [Tests locaux et recettes](#tests-locaux-et-recettes)
9. [TLS/mTLS](#tlsmtls)
10. [Personnalisation du code client](#personnalisation-du-code-client)
11. [Dépannage](#dépannage)

## Aperçu rapide
- **Orchestrateur (Hub)** : serveur Flower CPU qui agrège les poids envoyés par les clients.
- **Client DGX** : client Flower basé sur PyTorch avec CUDA **12.4**, prêt pour une station DGX/NVIDIA.
- **Workload de démonstration** : modèle PyTorch simple entraîné sur des données synthétiques pour valider la chaîne de fédération. Remplacez‑le par votre code métier lorsque l'infrastructure est validée.

## Prérequis techniques
- Docker 24+ sur les hôtes orchestrateur et client.
- NVIDIA Container Toolkit installé sur le DGX (accès GPU requis pour le client).
- Accès réseau sortant du client vers l'orchestrateur sur le port que vous aurez configuré (ex : 443 ou 8080).
- (Optionnel mais recommandé) Certificats TLS/mTLS disponibles côté hôte et montés dans `certs/orchestrator/` et `certs/client/`. Un mode auto-signé est inclus pour les phases de test.

## Contenu du dépôt
- `orchestrator/`
  - `Dockerfile` : image Python légère pour le serveur Flower.
  - `app/server.py` : serveur Flower configurable (commenté) avec FedAvg.
  - `.env.example` : variables d'environnement à copier/adapter.
- `client/`
  - `Dockerfile` : image PyTorch CUDA 12.4 pour le DGX.
  - `app/client.py` : client Flower PyTorch (commenté) avec données synthétiques.
  - `.env.example` : variables d'environnement à copier/adapter.
- `build_docker_FL.sh` : script pour construire une ou deux images (option `--self-signed` pour générer des certs de test).
- `run_docker_FL.sh` : script pour lancer un orchestrateur ou un client (option `--self-signed` également).
- `scripts/test_e2e_local.sh` : test de fumée automatisé (orchestrateur + client locaux via Docker, peut générer des certs de test).
- `scripts/generate_self_signed_certs.sh` : utilitaire pour produire un bundle CA + serveur + client auto-signés.
- `certs/` (à créer) : emplacement attendu pour vos certificats.
- `data/` (créé au run) : répertoire monté dans le conteneur client.

## Configuration des environnements
Copiez les modèles et renseignez vos adresses IP/ports/certificats :
```bash
cp orchestrator/.env.example orchestrator/.env
cp client/.env.example client/.env
```

### Orchestrateur (`orchestrator/.env`)
- `FLOWER_SERVER_ADDRESS` : adresse d'écoute (ex. `0.0.0.0`). Utilisez `0.0.0.0` si les clients se connectent depuis un autre hôte ; `127.0.0.1` limite l'écoute à la boucle locale à l'intérieur du conteneur et empêche les connexions externes.
- `FLOWER_SERVER_PORT` : port d'écoute (ex. `8080` ou `443`).
- `GRPC_MAX_MESSAGE_LENGTH` : taille max des messages gRPC (par défaut 512 MiB).
- `NUM_ROUNDS` : nombre de rounds de fédération.
- `MIN_FIT_CLIENTS`, `MIN_AVAILABLE_CLIENTS` : seuils d'attente des clients.
- `CA_CERT_PATH`, `SERVER_CERT_PATH`, `SERVER_KEY_PATH` : chemins montés dans `/certs` (optionnel, requiert 3 fichiers pour activer TLS/mTLS).

### Client (`client/.env`)
- `SERVER_ADDRESS` : endpoint de l'orchestrateur (ex. `192.0.2.10:443` ou `127.0.0.1:8080`).
- `CLIENT_ID` : identifiant visible dans les logs Flower.
- `N_LOCAL_EPOCHS`, `BATCH_SIZE`, `LEARNING_RATE` : hyperparamètres d'entraînement local.
- `USE_TLS` : `true/false` pour activer TLS.
- `CA_CERT_PATH`, `CLIENT_CERT_PATH`, `CLIENT_KEY_PATH` : chemins montés dans `/certs` (optionnel, 3 fichiers requis pour mTLS).

## Construction des images Docker
Depuis la racine du dépôt :
```bash
./build_docker_FL.sh orchestrator   # construit uniquement l'orchestrateur
./build_docker_FL.sh client         # construit uniquement le client DGX
./build_docker_FL.sh all            # construit les deux images
# Ajouter --self-signed génère automatiquement des certificats de test dans certs/
./build_docker_FL.sh all --self-signed
```
Le client est basé sur PyTorch CUDA 12.4 (runtime) afin d'être compatible avec les GPU NVIDIA récents.

## Exécution des conteneurs
### Orchestrateur
```bash
./run_docker_FL.sh orchestrator               # sans TLS
./run_docker_FL.sh orchestrator --self-signed # génère des certs de test et active TLS côté conteneur
```
- Monte `certs/orchestrator` dans le conteneur (créé si absent).
- Expose le port 8080 du conteneur vers 8080 de l'hôte par défaut (modifiez dans `.env`).

### Client DGX
```bash
./run_docker_FL.sh client                     # sans TLS
./run_docker_FL.sh client --self-signed       # génère des certs de test (mêmes CA) pour activer TLS/mTLS
```
- Monte `certs/client` et `data/` dans le conteneur (créés si absents).
- Nécessite `--gpus all` (NVIDIA Container Toolkit).

## Déploiement automatisé PROXY + DGX (Python)
Le script `scripts/deploy_proxy_dgx.py` s'exécute depuis **votre poste** et enchaîne clone/pull, copie des `.env`, build + run, puis synchronisation des certificats entre le proxy (orchestrateur) et le DGX (client) en s'appuyant sur votre configuration SSH locale.

Pré-requis :
- Un fichier `~/.ssh/config` avec deux hôtes nommés `PROXY` et `DGX` (clés privées, ports et utilisateurs configurés).
- Accès aux chemins distants par défaut `/home/qladane/federated` (proxy) et `/raid/workspace/qladane/federated` (DGX) ou surcharge via options CLI.
- Docker et (pour le client) NVIDIA Container Toolkit déjà installés sur les machines distantes.

Exécution :
```bash
# Depuis votre machine, à la racine du dépôt cloné localement
./scripts/deploy_proxy_dgx.py \
  --proxy-host PROXY --dgx-host DGX \
  --proxy-base /home/qladane/federated --dgx-base /raid/workspace/qladane/federated
```

Ce que fait le script :
1. Clone ou met à jour `https://github.com/qBrabus/federatedlearning` sur le proxy et le DGX.
2. Copie `orchestrator/.env.example` → `orchestrator/.env` sur le proxy et `client/.env.example` → `client/.env` sur le DGX.
3. Construit et lance l'orchestrateur sur le proxy en mode auto-signé.
4. Copie le dossier `certs/` généré sur le proxy vers le DGX pour partager **la même autorité de certification** (CA) entre orchestrateur et clients.
5. Construit et lance le client sur le DGX en réutilisant ces certificats.

Conseil : si l'orchestrateur écoute sur une IP/FQDN autre que `localhost`, définissez `CERT_SERVER_SAN` dans votre environnement avant d'appeler le script pour que le certificat serveur contienne le SAN adéquat.

Nettoyage :
```bash
./scripts/cleanup_proxy_dgx.py \
  --proxy-host PROXY --dgx-host DGX \
  --proxy-base /home/qladane/federated --dgx-base /raid/workspace/qladane/federated
```
Ce script arrête/supprime les conteneurs `fl-orchestrator` et `fl-client-dgx`, supprime les images associées et efface le dépôt cloné sur chaque machine distante.


## Tests locaux et recettes
### 1. Test de fumée orchestrateur + client (local Docker)
Utilisez le script automatisé qui démarre un orchestrateur détaché puis un client :
```bash
./scripts/test_e2e_local.sh
# ou avec certificats auto-signés + TLS (pensez à activer USE_TLS=true dans client/.env)
SELF_SIGNED=true ./scripts/test_e2e_local.sh
```
- Pré-requis : avoir construit les deux images et renseigné `orchestrator/.env` + `client/.env` avec des valeurs locales (ex. `127.0.0.1:8080`).
- Le script lance l'orchestrateur en arrière-plan, exécute un client, attend la fin du round puis arrête le conteneur serveur.

### 2. Test manuel pas-à-pas
1. Démarrer l'orchestrateur dans un terminal :
   ```bash
   ./run_docker_FL.sh orchestrator
   ```
2. Dans un autre terminal, démarrer le client :
   ```bash
   ./run_docker_FL.sh client
   ```
3. Surveillez les logs : le client doit se connecter, envoyer un fit et recevoir les poids agrégés.

### 3. Validation TLS/mTLS
- Placez vos certificats dans `certs/orchestrator` et `certs/client` et vérifiez que les chemins référencés dans les `.env` correspondent.
- Le script `scripts/test_e2e_local.sh` activera mTLS automatiquement si les trois fichiers sont présents côté orchestrateur et côté client.

## TLS/mTLS
- TLS est activé côté orchestrateur si **et seulement si** les variables `CA_CERT_PATH`, `SERVER_CERT_PATH`, `SERVER_KEY_PATH` pointent vers des fichiers valides montés dans `/certs`.
- mTLS côté client requiert `USE_TLS=true` et les trois fichiers `CA_CERT_PATH`, `CLIENT_CERT_PATH`, `CLIENT_KEY_PATH`.
- Les fichiers peuvent être montés en lecture seule depuis l'hôte (recommandé).
- Si vous n'avez pas encore vos certificats officiels, utilisez `--self-signed` ou `SELF_SIGNED=true` sur les scripts fournis : un bundle CA + serveur + client sera généré automatiquement dans `certs/` avec les chemins déjà alignés sur les `.env`.
- Pour des connexions inter-machines, copiez le contenu de `certs/` généré sur l'orchestrateur vers chaque client (au minimum `certs/ca.crt` et `certs/client/*`) afin que tous partagent la même autorité de certification. Ne regénérez pas un nouveau CA sur chaque machine, sinon la vérification TLS échouera.
- Si l'orchestrateur écoute sur une IP ou un FQDN différent de `localhost`, exportez la variable `CERT_SERVER_SAN` avant de lancer `--self-signed` (ex. `CERT_SERVER_SAN="DNS:localhost,IP:127.0.0.1,IP:10.200.241.101"`) pour que le certificat serveur contienne le SAN adapté.

## Personnalisation du code client
- Le modèle de démonstration se trouve dans `client/app/client.py` (`SimpleNet`). Remplacez-le par votre modèle PyTorch et vos loaders.
- Les hyperparamètres sont injectés via les variables d'environnement pour éviter les modifications de code.
- Ajoutez vos métriques de validation dans `evaluate` (retourne un dictionnaire libre).
- Les accès data doivent être montés dans `/data` ou gérés via vos connecteurs (NFS, API, base, etc.).

## Dépannage
- **Le client ne se connecte pas** : vérifiez `SERVER_ADDRESS` et la résolution DNS/port ; en TLS, contrôlez la validité des certificats.
- **Pas de GPU détecté** : exécutez `nvidia-smi` sur l'hôte et assurez-vous que le runtime Docker est configuré pour NVIDIA.
- **Timeouts gRPC** : augmentez `GRPC_MAX_MESSAGE_LENGTH` et vérifiez la latence réseau.
- **Certificats non trouvés** : assurez-vous que `certs/orchestrator` et `certs/client` existent et contiennent les bons chemins référencés dans `.env`.

