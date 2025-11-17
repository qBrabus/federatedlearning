"""Serveur Flower orchestrateur configurable.

Les variables d'environnement permettent d'ajuster l'adresse d'écoute,
le nombre de rounds et les paramètres du strategy FedAvg. TLS/mTLS est
activé automatiquement si les trois fichiers de certificats sont fournis
(et montés dans `/certs`).
"""

import os
from pathlib import Path
from typing import Optional, Tuple

import flwr as fl

Certificates = Tuple[bytes, bytes, bytes]


def get_env(name: str, default: str | None = None) -> str:
    """Retourne la valeur d'une variable d'environnement ou lève une erreur."""

    value = os.getenv(name, default)
    if value is None:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def build_certificates() -> Optional[Certificates]:
    """Charge les certificats TLS si les trois chemins sont présents."""

    ca_path = os.getenv("CA_CERT_PATH")
    cert_path = os.getenv("SERVER_CERT_PATH")
    key_path = os.getenv("SERVER_KEY_PATH")

    if ca_path and cert_path and key_path:
        return (
            Path(ca_path).read_bytes(),
            Path(cert_path).read_bytes(),
            Path(key_path).read_bytes(),
        )
    return None


def main() -> None:
    """Démarre le serveur Flower avec la stratégie FedAvg par défaut."""

    server_host = get_env("FLOWER_SERVER_ADDRESS", "0.0.0.0")
    server_port = get_env("FLOWER_SERVER_PORT", "8080")
    num_rounds = int(os.getenv("NUM_ROUNDS", "3"))
    grpc_max_message_length = int(get_env("GRPC_MAX_MESSAGE_LENGTH", "536870912"))

    # Seuils clients attendus par FedAvg
    strategy = fl.server.strategy.FedAvg(
        min_fit_clients=int(os.getenv("MIN_FIT_CLIENTS", "2")),
        min_available_clients=int(os.getenv("MIN_AVAILABLE_CLIENTS", "2")),
    )

    certificates = build_certificates()
    print(f"Starting Flower server on {server_host}:{server_port}")
    fl.server.start_server(
        server_address=f"{server_host}:{server_port}",
        config=fl.server.ServerConfig(num_rounds=num_rounds),
        strategy=strategy,
        grpc_max_message_length=grpc_max_message_length,
        certificates=certificates,
    )


if __name__ == "__main__":
    main()
