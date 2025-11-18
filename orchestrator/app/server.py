"""Démarrage de l'orchestrateur via la CLI moderne ``flower-superlink``.

Les variables d'environnement permettent d'ajuster l'adresse d'écoute et
d'activer TLS/mTLS. L'ancienne API ``fl.server.start_server`` étant
dépréciée, ce module se contente de construire et d'exécuter la commande
``flower-superlink`` avec ou sans certificats.
"""

import os
import shlex
import subprocess


def build_tls_args() -> list[str]:
    """Construit les arguments CLI TLS/mTLS pour ``flower-superlink``."""

    ca_path = os.getenv("CA_CERT_PATH")
    cert_path = os.getenv("SERVER_CERT_PATH")
    key_path = os.getenv("SERVER_KEY_PATH")
    use_tls = os.getenv("USE_TLS", "true").lower() in {"1", "true", "yes"}

    if use_tls and cert_path and key_path:
        args = ["--ssl-certfile", cert_path, "--ssl-keyfile", key_path]
        if ca_path:
            args.extend(["--ssl-ca-certfile", ca_path])
        return args

    return ["--insecure"]


def main() -> None:
    """Démarre un SuperLink Flower via la CLI moderne ``flower-superlink``."""

    server_host = os.getenv("FLOWER_SERVER_ADDRESS", "0.0.0.0")
    server_port = os.getenv("FLOWER_SERVER_PORT", "8080")
    serverapp_port = os.getenv("FLOWER_SERVERAPP_PORT")

    fleet_address = f"{server_host}:{server_port}"
    command = ["flower-superlink", "--fleet-api-address", fleet_address]
    if serverapp_port:
        # "flower-superlink" utilise l'option "--serverappio-api-address" pour
        # exposer l'API ServerApp (nomenclature issue du namespace internal).
        command.extend(["--serverappio-api-address", f"{server_host}:{serverapp_port}"])
    command.extend(build_tls_args())

    print("Starting Flower SuperLink with:", " ".join(shlex.quote(part) for part in command))
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
