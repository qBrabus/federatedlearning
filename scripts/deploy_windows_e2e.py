#!/usr/bin/env python3
"""Déploiement/validation orchestrateur + client DGX depuis Windows.

Le script ne requiert aucune installation système côté hôtes : seules les
commandes ``ssh``, ``scp`` et ``docker`` (déjà présentes) sont utilisées. Il :

1. Clone ou met à jour le dépôt sur PROXY-DATA (hub) et dgxh200 (client).
2. Copie les ``.env`` exemples si nécessaire et force des valeurs de test
   (1 client suffisant, port gRPC configurable, TLS/mTLS activé).
3. Construit et lance les conteneurs avec certificats auto-signés en option.
4. Exécute une batterie de tests (SSH, Docker, conteneurs, handshake gRPC/mTLS
   depuis le conteneur client, lecture rapide des logs Flower).
5. Arrête proprement les conteneurs et affiche un récapitulatif avec les
   commandes de relance manuelle.

Conçu pour PowerShell/Git Bash sous Windows, mais fonctionne aussi sous Linux.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterable

DEFAULT_REPO_URL = "https://github.com/qBrabus/federatedlearning"
DEFAULT_PROXY_HOST = "PROXY-DATA"
DEFAULT_DGX_HOST = "dgxh200"
DEFAULT_PROXY_BASE = "~/federated"
DEFAULT_DGX_BASE = "/raid/workspace/qladane/federated"
DEFAULT_REPO_NAME = "federatedlearning"
DEFAULT_SERVER_PORT = 443

LOG_FORMAT_FILE = "%(asctime)s | %(levelname)-8s | %(message)s"
LOG_FORMAT_CONSOLE = "%(levelname)-8s | %(message)s"


class _ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[36m",  # Cyan
        logging.INFO: "\033[32m",  # Green
        logging.WARNING: "\033[33m",  # Yellow
        logging.ERROR: "\033[31m",  # Red
        logging.CRITICAL: "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        base = super().format(record)
        color = self.COLORS.get(record.levelno, "")
        return f"{color}{base}{self.RESET}" if color else base


def setup_logger(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("deploy-win")
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(LOG_FORMAT_FILE, "%Y-%m-%d %H:%M:%S"))

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(_ColorFormatter(LOG_FORMAT_CONSOLE))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def info(logger: logging.Logger, msg: str, *args) -> None:
    logger.info(msg, *args)


def run_command(command: list[str], logger: logging.Logger, label: str) -> None:
    quoted = " ".join(shlex.quote(part) for part in command)
    logger.debug("[%s] %s", label, quoted)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )

    assert process.stdout is not None
    for line in process.stdout:
        logger.info("[%s] %s", label, line.rstrip())

    process.wait()
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command)


def ssh(host: str, command: str, logger: logging.Logger, label: str | None = None) -> None:
    run_command(["ssh", host, f"set -euo pipefail; {command}"], logger, label or f"ssh {host}")


def scp(source: str, destination: str, logger: logging.Logger, label: str = "scp") -> None:
    run_command(["scp", "-r", source, destination], logger, label)


def quote(value: str) -> str:
    return shlex.quote(value)


def repo_name_from_url(url: str) -> str:
    segment = url.rstrip("/").split("/")[-1]
    return segment[:-4] if segment.endswith(".git") else segment or DEFAULT_REPO_NAME


def clone_or_pull(host: str, base_dir: str, repo_url: str, repo_name: str, logger: logging.Logger) -> None:
    remote_cmd = (
        f"mkdir -p {quote(base_dir)} && cd {quote(base_dir)} && "
        f"if [ -d {quote(repo_name)}/.git ]; then "
        f"git -C {quote(repo_name)} fetch --all --tags && "
        f"git -C {quote(repo_name)} pull --ff-only; "
        f"else git clone {quote(repo_url)} {quote(repo_name)}; fi"
    )
    info(logger, "[%s] synchro Git", host)
    ssh(host, remote_cmd, logger)


def ensure_env(host: str, base_dir: str, repo_name: str, env_file: str, example_file: str, logger: logging.Logger) -> None:
    remote_cmd = (
        f"cd {quote(base_dir)}/{quote(repo_name)} && "
        f"if [ ! -f {quote(env_file)} ]; then cp -f {quote(example_file)} {quote(env_file)}; fi"
    )
    info(logger, "[%s] vérification de %s", host, env_file)
    ssh(host, remote_cmd, logger)


def write_test_env(host: str, base_dir: str, repo_name: str, component: str, server_port: int, logger: logging.Logger) -> None:
    """Force des valeurs compatibles avec un test 1 client / mTLS."""

    if component == "orchestrator":
        content = f"""FLOWER_SERVER_ADDRESS=0.0.0.0
FLOWER_SERVER_PORT={server_port}
GRPC_MAX_MESSAGE_LENGTH=536870912
NUM_ROUNDS=1
MIN_FIT_CLIENTS=1
MIN_AVAILABLE_CLIENTS=1
CA_CERT_PATH=/certs/ca.crt
SERVER_CERT_PATH=/certs/server.crt
SERVER_KEY_PATH=/certs/server.key
"""
        remote_cmd = (
            f"cd {quote(base_dir)}/{quote(repo_name)} && "
            f"printf %s {quote(content)} > orchestrator/.env"
        )
    else:
        content = f"""SERVER_ADDRESS=10.200.241.101:{server_port}
CLIENT_ID=dgx-client
N_LOCAL_EPOCHS=1
BATCH_SIZE=64
LEARNING_RATE=0.01
USE_TLS=true
CA_CERT_PATH=/certs/ca.crt
CLIENT_CERT_PATH=/certs/client.crt
CLIENT_KEY_PATH=/certs/client.key
"""
        remote_cmd = (
            f"cd {quote(base_dir)}/{quote(repo_name)} && "
            f"printf %s {quote(content)} > client/.env"
        )

    info(logger, "[%s] écriture .env de test (%s)", host, component)
    ssh(host, remote_cmd, logger)


def build_and_run(host: str, base_dir: str, repo_name: str, component: str, logger: logging.Logger, self_signed: bool) -> None:
    flags = "--self-signed" if self_signed else ""
    remote_cmd = (
        f"cd {quote(base_dir)}/{quote(repo_name)} && "
        f"./build_docker_FL.sh {component} {flags} && "
        f"./run_docker_FL.sh {component} {flags} --detach"
    )
    info(logger, "[%s] build + run %s", host, component)
    ssh(host, remote_cmd, logger)


def check_docker(host: str, logger: logging.Logger) -> None:
    ssh(host, "docker --version", logger, label=f"docker {host}")


def check_containers(host: str, logger: logging.Logger) -> None:
    ssh(host, "docker ps --format 'table {{.Names}}\t{{.Status}}'", logger, label=f"docker-ps {host}")


def grpc_smoke_test(host: str, logger: logging.Logger) -> None:
    """Test gRPC/mTLS depuis le conteneur client."""

    payload = r'''
import os, grpc
addr = os.environ.get("SERVER_ADDRESS", "127.0.0.1:8080")
use_tls = os.environ.get("USE_TLS", "true").lower() in {"1", "true", "yes"}
ca = os.environ.get("CA_CERT_PATH", "/certs/ca.crt")
cert = os.environ.get("CLIENT_CERT_PATH")
key = os.environ.get("CLIENT_KEY_PATH")
if use_tls:
    with open(ca, "rb") as f:
        if cert and key:
            with open(cert, "rb") as fc, open(key, "rb") as fk:
                creds = grpc.ssl_channel_credentials(root_certificates=f.read(), certificate_chain=fc.read(), private_key=fk.read())
        else:
            creds = grpc.ssl_channel_credentials(root_certificates=f.read())
    channel = grpc.secure_channel(addr, creds)
else:
    channel = grpc.insecure_channel(addr)
grpc.channel_ready_future(channel).result(timeout=10)
print("gRPC channel ready ->", addr)
'''
    cmd = f"docker exec fl-client-dgx python - <<'PY'\n{payload}\nPY"
    ssh(host, cmd, logger, label="grpc-test")


def tail_logs(host: str, container: str, logger: logging.Logger, lines: int = 20) -> None:
    ssh(host, f"docker logs --tail {lines} {quote(container)}", logger, label=f"logs {container}")


def stop_containers(hosts: Iterable[str], logger: logging.Logger) -> None:
    for host in hosts:
        ssh(host, "docker stop fl-orchestrator >/dev/null 2>&1 || true; docker stop fl-client-dgx >/dev/null 2>&1 || true", logger, label=f"stop {host}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Déploiement/validation orchestrateur + client DGX depuis Windows")
    parser.add_argument("--proxy-host", default=DEFAULT_PROXY_HOST)
    parser.add_argument("--dgx-host", default=DEFAULT_DGX_HOST)
    parser.add_argument("--proxy-base", default=DEFAULT_PROXY_BASE)
    parser.add_argument("--dgx-base", default=DEFAULT_DGX_BASE)
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    parser.add_argument("--repo-name", default=DEFAULT_REPO_NAME)
    parser.add_argument("--server-port", type=int, default=DEFAULT_SERVER_PORT)
    parser.add_argument("--log-file", default="")
    parser.add_argument("--self-signed", action="store_true", help="Génère des certificats auto-signés et les synchronise")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_name = args.repo_name or repo_name_from_url(args.repo_url)

    log_file = Path(args.log_file) if args.log_file else Path.cwd() / f"deploy_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
    logger = setup_logger(log_file)
    info(logger, "Journal détaillé : %s", log_file)

    try:
        # 1) Connectivité SSH + Git clone/pull
        clone_or_pull(args.proxy_host, args.proxy_base, args.repo_url, repo_name, logger)
        clone_or_pull(args.dgx_host, args.dgx_base, args.repo_url, repo_name, logger)

        # 2) .env et valeurs de test minimalistes
        ensure_env(args.proxy_host, args.proxy_base, repo_name, "orchestrator/.env", "orchestrator/.env.example", logger)
        ensure_env(args.dgx_host, args.dgx_base, repo_name, "client/.env", "client/.env.example", logger)
        write_test_env(args.proxy_host, args.proxy_base, repo_name, "orchestrator", args.server_port, logger)
        write_test_env(args.dgx_host, args.dgx_base, repo_name, "client", args.server_port, logger)

        # 3) Build + run
        build_and_run(args.proxy_host, args.proxy_base, repo_name, "orchestrator", logger, args.self_signed)
        build_and_run(args.dgx_host, args.dgx_base, repo_name, "client", logger, args.self_signed)

        # 4) Tests
        check_docker(args.proxy_host, logger)
        check_docker(args.dgx_host, logger)
        check_containers(args.proxy_host, logger)
        check_containers(args.dgx_host, logger)
        grpc_smoke_test(args.dgx_host, logger)
        tail_logs(args.proxy_host, "fl-orchestrator", logger)
        tail_logs(args.dgx_host, "fl-client-dgx", logger)

        info(logger, "Tests terminés avec succès. Arrêt des conteneurs...")
    except Exception as exc:  # noqa: BLE001
        logger.error("Échec du déploiement/validation: %s", exc)
        stop_containers([args.proxy_host, args.dgx_host], logger)
        sys.exit(1)

    stop_containers([args.proxy_host, args.dgx_host], logger)

    info(
        logger,
        "Déploiement validé. Pour relancer manuellement:\n"
        "  Orchestrateur: cd %s/%s && ./run_docker_FL.sh orchestrator --self-signed --detach\n"
        "  Client DGX:    cd %s/%s && SERVER_ADDRESS=10.200.241.101:%s ./run_docker_FL.sh client --self-signed --detach",
        args.proxy_base,
        repo_name,
        args.dgx_base,
        repo_name,
        args.server_port,
    )


if __name__ == "__main__":
    main()
