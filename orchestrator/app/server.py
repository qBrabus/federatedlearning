"""Orchestrateur Flower moderne (SuperLink + ServerApp dans le même pod).

Ce module lance l'architecture Next-Gen complète sans recourir à l'API
``start_server`` dépréciée. Le workflow est le suivant:

1. Démarrage d'un ``flower-superlink`` (routeur gRPC) en arrière-plan.
2. Démarrage d'un ``flower-server-app`` (stratégie FedAvg) qui se connecte
   au SuperLink via l'API ServerAppIO.

Les deux processus partagent le même conteneur pour simplifier le déploiement
"two-node" (orchestrateur + client). Les paramètres (adresses, ports,
certificats) sont entièrement configurables via les variables d'environnement
utilisées par ``run_docker_FL.sh``.
"""

import os
import shutil
import subprocess
import time
from typing import List, Sequence

from flwr.common import Context
from flwr.server import ServerApp, ServerAppComponents, ServerConfig
from flwr.server.strategy import FedAvg


# ===========================================================================
# Construction du ServerApp (stratégie + configuration)
# ===========================================================================


def server_fn(_: Context) -> ServerAppComponents:
    """Construit les composants ServerApp avec FedAvg et un seul client minimum."""

    num_rounds = int(os.getenv("NUM_ROUNDS", "3"))
    strategy = FedAvg(min_fit_clients=1, min_available_clients=1, min_evaluate_clients=1)
    config = ServerConfig(num_rounds=num_rounds)
    return ServerAppComponents(strategy=strategy, config=config)


# Objet utilisé par ``flower-server-app --app app.server:app``
app = ServerApp(server_fn=server_fn)


# ===========================================================================
# Helpers CLI pour SuperLink et ServerApp
# ===========================================================================


def use_tls() -> bool:
    """Retourne True si TLS/mTLS est activé via l'environnement."""

    return os.getenv("USE_TLS", "true").lower() in {"1", "true", "yes"}


def build_superlink_cmd(server_host: str, fleet_port: str, serverapp_port: str) -> List[str]:
    """Construit la commande ``flower-superlink`` avec ou sans certificats."""

    ca_path = os.getenv("CA_CERT_PATH")
    cert_path = os.getenv("SERVER_CERT_PATH")
    key_path = os.getenv("SERVER_KEY_PATH")

    cmd: List[str] = [
        "flower-superlink",
        "--fleet-api-address",
        f"{server_host}:{fleet_port}",
        "--serverappio-api-address",
        f"{server_host}:{serverapp_port}",
    ]

    if use_tls() and cert_path and key_path:
        cmd.extend(["--ssl-certfile", cert_path, "--ssl-keyfile", key_path])
        if ca_path:
            cmd.extend(["--ssl-ca-certfile", ca_path])
    else:
        cmd.append("--insecure")

    return cmd


def build_serverapp_cmd(serverapp_port: str) -> List[str]:
    """Construit la commande ``flower-server-app`` qui se connecte au SuperLink."""

    ca_path = os.getenv("CA_CERT_PATH")

    # ``flower-server-app`` est installé comme script console avec Flower 1.23,
    # mais dans certains environnements le binaire peut ne pas être exposé dans
    # le PATH (ou être renommé). On détecte sa présence et on bascule
    # automatiquement vers ``python -m flwr.serverapp`` si nécessaire.
    executable = shutil.which("flower-server-app")
    if executable:
        cmd: List[str] = [executable]
    else:
        print("[orchestrator] 'flower-server-app' introuvable, utilisation du module Python")
        cmd = ["python", "-m", "flwr.serverapp"]

    cmd.extend(
        [
            "--serverappio-api-address",
            f"127.0.0.1:{serverapp_port}",
            "--app",
            "app.server:app",
        ]
    )

    if use_tls() and ca_path:
        cmd.extend(["--root-certificates", ca_path])
    elif not use_tls():
        cmd.append("--insecure")

    return cmd


def run_process(command: Sequence[str]) -> subprocess.Popen[bytes]:
    """Lance un processus en arrière-plan en affichant la commande."""

    print("[orchestrator]", " ".join(command))
    return subprocess.Popen(command)


# ===========================================================================
# Entrée principale
# ===========================================================================


def main() -> None:
    """Démarre SuperLink (background) puis ServerApp (foreground)."""

    server_host = os.getenv("FLOWER_SERVER_ADDRESS", "0.0.0.0")
    fleet_port = os.getenv("FLOWER_SERVER_PORT", "8080")
    serverapp_port = os.getenv("FLOWER_SERVERAPP_PORT", "9091")

    superlink_cmd = build_superlink_cmd(server_host, fleet_port, serverapp_port)
    serverapp_cmd = build_serverapp_cmd(serverapp_port)

    superlink_proc = run_process(superlink_cmd)

    try:
        time.sleep(5)
        subprocess.run(serverapp_cmd, check=True)
    except KeyboardInterrupt:
        print("[orchestrator] Arrêt demandé (Ctrl+C)")
    finally:
        superlink_proc.terminate()
        try:
            superlink_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            superlink_proc.kill()


if __name__ == "__main__":
    main()
