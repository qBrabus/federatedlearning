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


def tls_supported_by_serverapp() -> bool:
    """Indique si ``flwr-serverapp`` gère le TLS (non supporté en 1.23)."""

    return False


def build_superlink_cmd(
    server_host: str, fleet_port: str, serverapp_port: str, enable_tls: bool
) -> List[str]:
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

    if enable_tls and cert_path and key_path:
        cmd.extend(["--ssl-certfile", cert_path, "--ssl-keyfile", key_path])
        if ca_path:
            cmd.extend(["--ssl-ca-certfile", ca_path])
    else:
        cmd.append("--insecure")

    return cmd


def build_serverapp_cmd(serverapp_port: str, enable_tls: bool) -> List[str]:
    """Construit la commande ``flwr-serverapp`` qui se connecte au SuperLink."""

    # Depuis Flower 1.23 le binaire s'appelle ``flwr-serverapp`` (sans tirets),
    # mais certains environnements historiques peuvent encore fournir
    # ``flower-server-app``. On supporte les deux noms, avec priorité au binaire
    # officiel.
    executable = shutil.which("flwr-serverapp") or shutil.which("flower-server-app")
    if executable:
        cmd: List[str] = [executable]
    else:
        print(
            "[orchestrator] 'flwr-serverapp' introuvable, utilisation du binaire ``flwr``"
        )
        cmd = ["flwr", "serverapp"]

    cmd.extend(
        [
            "--serverappio-api-address",
            f"127.0.0.1:{serverapp_port}",
            "--app",
            "app.server:app",
        ]
    )

    # ``flwr-serverapp`` ne gère pas encore TLS: on force donc le mode
    # --insecure même si des certificats sont fournis.
    if not enable_tls:
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

    tls_requested = use_tls()
    if tls_requested and not tls_supported_by_serverapp():
        print(
            "[orchestrator] TLS demandé mais non supporté par `flwr-serverapp` (Flower 1.23)."
            " Bascule en mode --insecure pour SuperLink et ServerApp."
        )
        tls_requested = False

    superlink_cmd = build_superlink_cmd(
        server_host, fleet_port, serverapp_port, enable_tls=tls_requested
    )
    serverapp_cmd = build_serverapp_cmd(serverapp_port, enable_tls=tls_requested)

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
