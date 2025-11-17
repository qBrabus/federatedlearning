#!/usr/bin/env python3
"""Déploiement automatisé orchestrateur (proxy) + client (DGX) en Python.

Ce script est prévu pour être exécuté depuis votre poste. Il se base sur
la configuration SSH (~/.ssh/config) décrite dans la demande initiale
avec deux hôtes :
- PROXY : orchestrateur (chemin par défaut /home/qladane/federated)
- DGX   : client (chemin par défaut /raid/workspace/qladane/federated)

Étapes effectuées :
1. Clone ou met à jour https://github.com/qBrabus/federatedlearning sur
   le proxy et le DGX.
2. Copie les `.env` exemple (orchestrator côté proxy, client côté DGX).
3. Construit et lance l'orchestrateur en mode self-signed sur le proxy.
4. Copie `certs/` généré sur le proxy vers le DGX pour partager la même
   autorité de certification (CA) sur tous les clients.
5. Construit et lance le client sur le DGX en réutilisant ces certificats.
"""

from __future__ import annotations

import argparse
import logging
import shlex
import subprocess
import sys
import tempfile
from threading import Thread
from datetime import datetime
from pathlib import Path
import time

DEFAULT_REPO_URL = "https://github.com/qBrabus/federatedlearning"
DEFAULT_PROXY_HOST = "PROXY"
DEFAULT_DGX_HOST = "DGX"
DEFAULT_PROXY_BASE = "/home/qladane/federated"
DEFAULT_DGX_BASE = "/raid/workspace/qladane/federated"
REPO_NAME_FALLBACK = "federatedlearning"


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
        """Format log records with ANSI colors for the console handler."""

        base = super().format(record)
        color = self.COLORS.get(record.levelno, "")
        return f"{color}{base}{self.RESET}" if color else base


def setup_logger(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("deploy")
    logger.setLevel(logging.DEBUG)

    # File handler (detailed, no colors)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%Y-%m-%d %H:%M:%S")
    )

    # Console handler (colored)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        _ColorFormatter("%(asctime)s | %(levelname)-8s | %(message)s", "%H:%M:%S")
    )

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def info(logger: logging.Logger, message: str) -> None:
    logger.info(message)


def run_command(command: list[str], logger: logging.Logger, label: str) -> None:
    quoted = " ".join(shlex.quote(part) for part in command)
    logger.debug("[%s] Exécution: %s", label, quoted)

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
        logger.error("[%s] Commande terminée avec le code %s", label, process.returncode)
        raise subprocess.CalledProcessError(process.returncode, command)


def ssh(host: str, command: str, logger: logging.Logger) -> None:
    run_command(["ssh", host, f"set -euo pipefail; {command}"], logger, label=f"ssh {host}")


def scp(source: str, destination: str, logger: logging.Logger) -> None:
    run_command(["scp", "-r", source, destination], logger, label="scp")


def stream_logs(host: str, container: str, logger: logging.Logger, tail: int = 500, retries: int = 10) -> Thread:
    """Stream docker logs from a remote host in a background thread."""

    def _target() -> None:
        for attempt in range(1, retries + 1):
            try:
                ssh(host, f"docker logs -f --tail {tail} {quote(container)}", logger)
                break
            except subprocess.CalledProcessError:
                if attempt == retries:
                    logger.error("[%s] Impossible de suivre les logs après %s tentatives", container, retries)
                else:
                    logger.warning(
                        "[%s] Logs indisponibles (tentative %s/%s), nouvel essai dans 2s...",
                        container,
                        attempt,
                        retries,
                    )
                    time.sleep(2)

    thread = Thread(target=_target, daemon=True)
    thread.start()
    return thread


def repo_name_from_url(url: str) -> str:
    last_segment = url.rstrip("/").split("/")[-1]
    if last_segment.endswith(".git"):
        return last_segment[:-4] or REPO_NAME_FALLBACK
    return last_segment or REPO_NAME_FALLBACK


def quote(value: str) -> str:
    return shlex.quote(value)


def clone_or_update(host: str, base_dir: str, repo_url: str, repo_name: str, logger: logging.Logger) -> None:
    remote_cmd = (
        f"mkdir -p {quote(base_dir)} && cd {quote(base_dir)} && "
        f"if [ -d {quote(repo_name)}/.git ]; then "
        f"git -C {quote(repo_name)} fetch --all --tags && "
        f"git -C {quote(repo_name)} pull --ff-only; "
        f"else git clone {quote(repo_url)} {quote(repo_name)}; fi"
    )
    info(logger, f"{host}: clone/pull du dépôt")
    ssh(host, remote_cmd, logger)


def copy_env(
    host: str, base_dir: str, repo_name: str, env_file: str, example_file: str, logger: logging.Logger
) -> None:
    remote_cmd = (
        f"cd {quote(base_dir)}/{quote(repo_name)} && "
        f"cp -f {quote(example_file)} {quote(env_file)}"
    )
    info(logger, f"{host}: copie {example_file} -> {env_file}")
    ssh(host, remote_cmd, logger)


def build_and_run(host: str, base_dir: str, repo_name: str, component: str, logger: logging.Logger) -> None:
    remote_cmd = (
        f"cd {quote(base_dir)}/{quote(repo_name)} && "
        f"./build_docker_FL.sh {component} --self-signed && "
        f"./run_docker_FL.sh {component} --self-signed --detach"
    )
    info(logger, f"{host}: build + run {component}")
    ssh(host, remote_cmd, logger)


def sync_certs(
    proxy_host: str, proxy_base: str, dgx_host: str, dgx_base: str, repo_name: str, logger: logging.Logger
) -> None:
    proxy_certs = f"{proxy_base}/{repo_name}/certs"
    dgx_target = f"{dgx_base}/{repo_name}/"
    info(logger, f"Copie des certificats {proxy_host}:{proxy_certs} -> {dgx_host}:{dgx_target}")

    with tempfile.TemporaryDirectory() as tmp:
        local_bundle = Path(tmp) / "certs"
        scp(f"{proxy_host}:{proxy_certs}", tmp, logger)
        scp(str(local_bundle), f"{dgx_host}:{dgx_target}", logger)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Déploiement orchestrateur (proxy) + client (DGX)")
    parser.add_argument("--proxy-host", default=DEFAULT_PROXY_HOST, help="Entrée SSH pour le proxy (orchestrateur)")
    parser.add_argument("--dgx-host", default=DEFAULT_DGX_HOST, help="Entrée SSH pour le DGX (client)")
    parser.add_argument("--proxy-base", default=DEFAULT_PROXY_BASE, help="Répertoire racine sur le proxy")
    parser.add_argument("--dgx-base", default=DEFAULT_DGX_BASE, help="Répertoire racine sur le DGX")
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL, help="URL du dépôt Git à cloner")
    parser.add_argument("--repo-name", default="", help="Nom du dossier du dépôt (déduit de l'URL si vide)")
    parser.add_argument(
        "--log-file",
        default="",
        help="Chemin du fichier de log (par défaut deploy_YYYYMMDD_HHMMSS.log dans le répertoire courant)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_name = args.repo_name or repo_name_from_url(args.repo_url)

    log_file = Path(args.log_file) if args.log_file else Path.cwd() / f"deploy_{datetime.now():%Y%m%d_%H%M%S}.log"
    logger = setup_logger(log_file)

    info(logger, f"Logs détaillés enregistrés dans {log_file}")

    clone_or_update(args.proxy_host, args.proxy_base, args.repo_url, repo_name, logger)
    clone_or_update(args.dgx_host, args.dgx_base, args.repo_url, repo_name, logger)

    copy_env(
        args.proxy_host,
        args.proxy_base,
        repo_name,
        "orchestrator/.env",
        "orchestrator/.env.example",
        logger,
    )
    copy_env(args.dgx_host, args.dgx_base, repo_name, "client/.env", "client/.env.example", logger)

    build_and_run(args.proxy_host, args.proxy_base, repo_name, "orchestrator", logger)
    sync_certs(args.proxy_host, args.proxy_base, args.dgx_host, args.dgx_base, repo_name, logger)
    build_and_run(args.dgx_host, args.dgx_base, repo_name, "client", logger)

    info(logger, "Déploiement terminé, streaming des logs docker (Ctrl+C pour arrêter)...")

    log_threads = [
        stream_logs(args.proxy_host, "fl-orchestrator", logger),
        stream_logs(args.dgx_host, "fl-client-dgx", logger),
    ]

    for thread in log_threads:
        thread.join()


if __name__ == "__main__":
    main()
