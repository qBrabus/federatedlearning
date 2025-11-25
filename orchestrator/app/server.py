"""Serveur Flower minimaliste pour démonstration locale.

Ce module démarre directement un serveur Flower classique (gRPC) en
chargeant la stratégie FedAvg. La mise en œuvre précédente tentait de
lancer ``flwr-serverapp`` avec l'argument ``--app``, mais la version
1.23 de Flower ne supporte plus ce flag, entraînant l'arrêt du conteneur
orchestrateur et donc l'échec de la connexion du client.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

import flwr as fl
from flwr.server import ServerConfig
from flwr.server.strategy import FedAvg


def build_strategy() -> FedAvg:
    """Construit la stratégie FedAvg avec un seul client minimum."""

    return FedAvg(min_fit_clients=1, min_available_clients=1, min_evaluate_clients=1)


def load_certificates(enable_tls: bool) -> Tuple[bytes, bytes, bytes] | None:
    """Charge les certificats serveur si TLS/mTLS est activé.

    Retourne un tuple (ca, cert, key) si les fichiers sont présents, sinon None.
    """

    if not enable_tls:
        return None

    ca_path = os.getenv("CA_CERT_PATH")
    cert_path = os.getenv("SERVER_CERT_PATH")
    key_path = os.getenv("SERVER_KEY_PATH")

    if not (cert_path and key_path):
        print(
            "[orchestrator] TLS demandé mais SERVER_CERT_PATH/SERVER_KEY_PATH manquants. "
            "Démarrage en mode non sécurisé."
        )
        return None

    ca_bytes = Path(ca_path).read_bytes() if ca_path else None
    cert_bytes = Path(cert_path).read_bytes()
    key_bytes = Path(key_path).read_bytes()
    return (ca_bytes, cert_bytes, key_bytes)


def main() -> None:
    """Démarre le serveur Flower en mode gRPC classique."""

    server_host = os.getenv("FLOWER_SERVER_ADDRESS", "0.0.0.0")
    server_port = os.getenv("FLOWER_SERVER_PORT", "8080")
    num_rounds = int(os.getenv("NUM_ROUNDS", "3"))
    use_tls = os.getenv("USE_TLS", "true").lower() in {"1", "true", "yes"}

    certificates = load_certificates(enable_tls=use_tls)

    print(
        f"[orchestrator] Démarrage du serveur Flower sur {server_host}:{server_port} "
        f"(TLS={'activé' if certificates else 'désactivé'})"
    )

    fl.server.start_server(
        server_address=f"{server_host}:{server_port}",
        config=ServerConfig(num_rounds=num_rounds),
        strategy=build_strategy(),
        certificates=certificates,
    )


if __name__ == "__main__":
    main()
