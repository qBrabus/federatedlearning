# Plateforme Flower fédérée (orchestrateur + client DGX)

Ce dépôt fournit une démonstration complète d'apprentissage fédéré basée sur **Flower** avec un orchestrateur (hub) et un client de calcul DGX. Il inclut un script de déploiement de bout en bout qui prépare les hôtes, construit les images Docker, exécute des tests gRPC/mTLS, puis arrête proprement les conteneurs en fournissant les commandes de relance.

## Objectifs
- Fournir une topologie de référence : orchestrateur sur `PROXY-DATA (10.200.241.101)` dans `~/federated` et client DGX sur `dgxh200 (10.200.50.45)` dans `/raid/workspace/qladane/federated`.
- Sécuriser les échanges en **gRPC mTLS** sur le port 443 (le client consomme les certificats générés par le hub).
- Livrer une automatisation complète : clone Git, installation Docker/Git si manquants, build, run, tests, arrêt, journalisation console + fichier.
- Documenter l'architecture du code et de l'infra pour faciliter les adaptations.

## Topologie & transport
- **Orchestrateur** : `PROXY-DATA (10.200.241.101)` — répertoire `~/federated`.
- **Client DGX** : `dgxh200 (10.200.50.45)` — répertoire `/raid/workspace/qladane/federated`.
- **Ports** : gRPC exposé sur **443** (configurable).
- **Sécurité** : mTLS ; le client récupère le CA + cert/clé générés côté hub ou fournis par l'infra.

## Architecture des sources
```
./
├─ orchestrator/               # Image du hub Flower (SuperLink gRPC, TLS/mTLS)
│  ├─ app/server.py            # Construction + exécution de la CLI `flower-superlink`
│  └─ .env.example             # Variables d'environnement orchestrateur (ports, seuils clients...)
├─ client/                     # Image du client DGX (PyTorch + Flower)
│  ├─ app/client.py            # Client Flower générant des données synthétiques
│  └─ .env.example             # Variables d'environnement client (adresse serveur, TLS...)
├─ scripts/
│  ├─ cleanup_proxy_dgx.py     # Nettoyage des conteneurs/images/dépôts sur les deux hôtes
│  ├─ deploy_windows_e2e.py    # Déploiement/validation multi-hôtes (Windows ou Linux)
│  └─ generate_self_signed_certs.sh # Génération de certificats auto-signés (TLS/mTLS)
├─ build_docker_FL.sh          # Build des images orchestrateur/client
└─ run_docker_FL.sh            # Lancement des conteneurs orchestrateur/client
```

### Code applicatif
- **Orchestrateur** (`orchestrator/app/server.py`)
  - Lit les variables d'environnement (`FLOWER_SERVER_ADDRESS`, `FLOWER_SERVER_PORT`, `USE_TLS`, chemins des certificats).
  - Lance l'orchestrateur via la CLI **`flower-superlink`** (recommandée par Flower) en TLS/mTLS si les certificats sont fournis, sinon en mode `--insecure`.
- **Client** (`client/app/client.py`)
  - Paramétrable via l'environnement (`SERVER_ADDRESS`, `CLIENT_ID`, `N_LOCAL_EPOCHS`, `BATCH_SIZE`, `LEARNING_RATE`, `USE_TLS`, chemins des certificats).
  - Crée un MLP simple, génère des données synthétiques et se connecte au hub via `fl.client.start_client`. Gère automatiquement le cas TLS/mTLS.

## Pré-requis machine de pilotage
- **SSH** et **SCP** fonctionnels (PowerShell, Git Bash ou Linux).
- **Python 3.10+** pour exécuter `scripts/deploy_windows_e2e.py`.
- Accès aux hôtes via la configuration SSH ci-dessous.

### Configuration SSH recommandée
Ajouter à `~/.ssh/config` (Windows ou Linux) :
```
Host DGX
    HostName 10.200.50.45
    User quentin
    Port 22
    IdentityFile C:\Users\Maquette\.ssh\id_ed25519
    IdentitiesOnly yes

Host PROXY
    HostName 10.200.241.101
    User qladane
    Port 22
    IdentityFile C:\Users\Maquette\.ssh\id_ed25519
    IdentitiesOnly yes
```

Pour éviter de ressaisir le mot de passe (`Melbadina18@!`), générez une clé sans passphrase puis copiez-la une seule fois sur chaque hôte (vous entrerez le mot de passe lors de cette copie, plus besoin ensuite) :
```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
ssh-copy-id -i ~/.ssh/id_ed25519.pub PROXY   # saisissez Melbadina18@!
ssh-copy-id -i ~/.ssh/id_ed25519.pub DGX     # saisissez Melbadina18@!
```

## Déploiement automatisé (Windows ou Linux)
Le script `scripts/deploy_windows_e2e.py` pilote l'orchestrateur et le client DGX. Il :
1. Vérifie/installe **Git** et **Docker** sur chaque hôte (via `curl https://get.docker.com | sh` si Docker est absent) et démarre le service Docker.
2. Clone ou met à jour ce dépôt dans les répertoires cibles (`~/federated` et `/raid/workspace/qladane/federated`).
3. Prépare les `.env` (copie des exemples si absents) et force des valeurs de test minimales (1 client, port gRPC choisi, TLS/mTLS activé avec certificats partagés).
4. Construit puis lance les conteneurs via `build_docker_FL.sh` et `run_docker_FL.sh` (option `--self-signed` si demandée pour générer/synchroniser les certificats).
5. Exécute les tests : version Docker, conteneurs en cours, handshake gRPC/mTLS depuis le conteneur client, extraction des logs Flower.
6. Arrête proprement les conteneurs après validation et imprime les commandes pour relancer manuellement.
7. Journalise tout dans un fichier horodaté et en console.

### Commande type
Depuis votre poste (PowerShell/Git Bash/Linux) :
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
Options clés :
- `--proxy-host` / `--dgx-host` : alias/entrées SSH.
- `--proxy-base` / `--dgx-base` : dossiers cibles.
- `--repo-url` : URL Git (par défaut ce dépôt).
- `--server-port` : port gRPC exposé côté orchestrateur (443 recommandé).
- `--self-signed` : génère un CA local + certs hub/client et les synchronise vers le DGX.
- `--log-file` : chemin du log (défaut : `./deploy_YYYYMMDD_HHMMSS.log`).

### Résultats des tests et arrêt
Le script affiche et écrit dans le log :
- Connexion SSH + détection/installation de Docker et Git.
- État des conteneurs (`fl-orchestrator`, `fl-client-dgx`).
- Résultat du handshake gRPC/mTLS (commande `grpc.channel_ready_future`).
- Extraits des logs Flower (serveur + client).

En fin d'exécution, les conteneurs sont arrêtés et un rappel indique comment relancer :
```
Orchestrateur : cd ~/federated/federatedlearning && ./run_docker_FL.sh orchestrator --self-signed --detach
Client DGX   : cd /raid/workspace/qladane/federated/federatedlearning \
               && SERVER_ADDRESS=10.200.241.101:443 ./run_docker_FL.sh client --self-signed --detach
```

## Déploiement manuel (Linux ↔ Linux)
1. **Build**
   ```bash
   ./build_docker_FL.sh all --self-signed
   ```
2. **Lancer l'orchestrateur**
   ```bash
   HOST_PORT_OVERRIDE=443 ./run_docker_FL.sh orchestrator --self-signed --detach
   ```
3. **Lancer le client DGX**
   ```bash
   SERVER_ADDRESS=10.200.241.101:443 USE_TLS=true ./run_docker_FL.sh client --self-signed --detach
   ```
4. **Nettoyer**
   ```bash
   python scripts/cleanup_proxy_dgx.py --proxy-host PROXY --dgx-host DGX \
     --proxy-base "~/federated" --dgx-base "/raid/workspace/qladane/federated"
   ```

## Variables d'environnement principales
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

## FAQ rapide
- **Le script installe-t-il des paquets ?** Oui, il installe/active Docker et Git si absents sur les hôtes distants via le script officiel Docker (`get.docker.com`).
- **Comment éviter la saisie du mot de passe ?** Utilisez `ssh-copy-id` avec le mot de passe `Melbadina18@!` une seule fois (voir section SSH) pour autoriser la clé ed25519 sans prompt.
- **Et si le port 443 est pris ?** Ajustez `FLOWER_SERVER_PORT` et `HOST_PORT_OVERRIDE` côté orchestrateur, puis `SERVER_ADDRESS` côté client.
- **Où trouver les logs ?** Fichier `deploy_YYYYMMDD_HHMMSS.log` (ou celui passé via `--log-file`), plus la console.

## Licence
Ce projet est fourni à titre d'exemple pour orchestrer Flower avec TLS/mTLS. Adaptez les scripts selon vos besoins internes.
