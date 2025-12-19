# Plateforme Flower orchestrée depuis Ubuntu (SuperLink/SuperNode + monitoring)

Ce dépôt fournit une pile d'apprentissage fédéré **Flower 1.25** prête pour la prod : orchestration Docker Compose multi-profils, déploiement en SSH depuis un poste **Ubuntu** (admin), supervision Prometheus/Grafana sur le DGX et génération simplifiée des certificats TLS.

## Structure
```
.
├── compose.yaml                   # Orchestration hub/client/monitoring via profils
├── .env.example                   # Configuration centralisée (copier en .env)
├── scripts/
│   ├── deploy.sh                  # Déploiement automatisé via Docker Context + rsync
│   └── generate_certs.sh          # Génération CA + certificats serveur/client
├── orchestrator/
│   ├── Dockerfile                 # Image ServerApp (Flower 1.25)
│   ├── app/server.py              # FedAvg, rounds pilotables via NUM_ROUNDS
│   └── run.sh                     # Lance flower-superexec vers le SuperLink
├── client/
│   ├── Dockerfile                 # Image ClientApp (PyTorch CUDA)
│   ├── app/client.py              # NumPyClient avec hyperparamètres dynamiques
│   └── run.sh                     # Lance flower-superexec vers le SuperNode
└── monitoring/
    ├── prometheus.tmpl.yml        # Modèle Prometheus (render vers prometheus/prometheus.yml)
    ├── prometheus/                # Répertoire monté dans le conteneur Prometheus
    └── grafana-provisioning/      # Datasource + dashboard de base
```

## Prérequis
- Poste d'admin **Ubuntu** avec Docker + Docker Compose plugin installés.
- Accès SSH sans mot de passe vers :
  - `proxy-data` (SuperLink/hub) → IP `${PROXY_IP}`
  - `dgx` (SuperNode + ClientApp + monitoring) → IP `${DGX_IP}`
- CUDA/GPU sur le DGX (runtime NVIDIA actif pour `clientapp`).
- Fichier `~/.ssh/config` déjà configuré (cf. question utilisateur).

## Configuration (.env)
Copiez `.env.example` en `.env` puis ajustez :
- **Réseau** : `PROXY_IP`, `DGX_IP`, `HUB_PORT`
- **Flower** : `FLWR_VERSION` (1.25.0), `NUM_ROUNDS`
- **Hyperparamètres** : `BATCH_SIZE`, `LEARNING_RATE`
- **Monitoring** : `GRAFANA_PORT`, `PROMETHEUS_PORT`

## Déploiement automatisé depuis l'admin Ubuntu
1) Rendez le script exécutable : `chmod +x scripts/deploy.sh`.
2) (Optionnel) Générer des certificats : `./scripts/generate_certs.sh SERVER_SAN="IP:${PROXY_IP},DNS:proxy" CLIENT_SAN="IP:${DGX_IP}"`.
3) Déployez tout :
```bash
./scripts/deploy.sh
```
Le script :
- charge `.env` et rend `monitoring/prometheus/prometheus.yml` à partir du template avec l'IP du proxy ;
- crée les contextes Docker `proxy-node` et `dgx-node` via SSH (`docker context create ... host=ssh://proxy-data` etc.) ;
- synchronise le dépôt sur chaque hôte (rsync) ;
- lance les profils Compose nécessaires : hub sur le proxy, client+monitoring sur le DGX.

Endpoints après déploiement :
- Fleet API hub : `http://${PROXY_IP}:${HUB_PORT}`
- Grafana : `http://${DGX_IP}:${GRAFANA_PORT}` (admin/admin)
- Prometheus : `http://${DGX_IP}:${PROMETHEUS_PORT}`

## Services & profils Compose
- **hub** :
  - `superlink` (flwr/superlink) expose Fleet API 8080→`${HUB_PORT}`, ServerAppIo 9091, control 9093.
  - `serverapp` (orchestrator) se connecte au SuperLink via `APPIO_API_ADDRESS=superlink:9091` et lit `NUM_ROUNDS`.
- **client** (DGX) :
  - `supernode` (flwr/supernode) se connecte au hub `${PROXY_IP}:${HUB_PORT}` et expose AppIo 9094.
  - `clientapp` (client) lance `flower-superexec` sur l'AppIo local, avec hyperparamètres `BATCH_SIZE`/`LEARNING_RATE` ou `run_config`.
- **monitor** (DGX) :
  - `cadvisor` pour metrics conteneurs.
  - `prometheus` avec configuration générée (`monitoring/prometheus/prometheus.yml`).
  - `grafana` pré-provisionné (datasource Prometheus + dashboard de base `Flower Federated Overview`).

Lancez sélectivement un profil sur l'hôte courant (si vous ne passez pas par `deploy.sh`) :
```bash
docker compose --profile hub up -d --build        # Sur le proxy
docker compose --profile client --profile monitor up -d --build  # Sur le DGX
```

## Supervision & exploitation
- Logs depuis l'admin Ubuntu :
  - Hub : `docker --context proxy-node logs -f fl-serverapp`
  - Client : `docker --context dgx-node logs -f fl-clientapp`
- Prometheus scrappe :
  - cAdvisor (`localhost:8080`) pour CPU/Mem conteneurs DGX,
  - SuperNode (`fl-supernode:9094`),
  - SuperLink (`${PROXY_IP}:9093`) côté proxy.
- Grafana : dashboard « Flower Federated Overview » affiche CPU/Mem des conteneurs clients et l'état du SuperLink.
- Rapports Flower (traﬁc/rounds) depuis le proxy :
```bash
docker --context proxy-node exec fl-serverapp flwr list
```

## Tests rapides
Après déploiement, vérifiez que les conteneurs sont up :
```bash
docker --context proxy-node ps --filter "name=fl-"
docker --context dgx-node ps --filter "name=fl-"
```
Lancez ensuite un round complet et surveillez les logs client/hub.

## Nettoyage
Depuis chaque hôte (ou via contexte) :
```bash
docker compose --profile hub down           # Proxy
docker compose --profile client --profile monitor down   # DGX
```

## Points clés
- Plus de scripts Windows/legacy : tout passe par Docker Compose + Context depuis Ubuntu.
- Une seule configuration `.env` versionnée en exemple.
- Supervision intégrée (Prometheus + Grafana) déployée automatiquement sur le DGX.
- Certificats TLS générables localement (`scripts/generate_certs.sh`).
