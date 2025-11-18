#!/usr/bin/env python3
"""Nettoyage des déploiements proxy (orchestrateur) et DGX (client).

Ce script arrête/supprime les conteneurs existants, supprime les images
Docker associées puis efface le dépôt cloné sur chaque machine distante.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess

DEFAULT_PROXY_HOST = "PROXY"
DEFAULT_DGX_HOST = "DGX"
DEFAULT_PROXY_BASE = "/home/qladane/federated"
DEFAULT_DGX_BASE = "/raid/workspace/qladane/federated"
DEFAULT_REPO_NAME = "federatedlearning"


def info(message: str) -> None:
    print(f"[cleanup] {message}")


def run_command(command: list[str]) -> None:
    subprocess.run(command, check=True)


def ssh(host: str, command: str) -> None:
    run_command(["ssh", host, f"set -euo pipefail; {command}"])


def quote(value: str) -> str:
    return shlex.quote(value)


def cleanup_host(host: str, base_dir: str, repo_name: str) -> None:
    repo_path = f"{base_dir}/{repo_name}"
    remote_cmd = (
        # Supprime tous les conteneurs Flower (y compris les variantes -e2e)
        "containers=$(docker ps -aq --filter \"name=fl-orchestrator\" --filter \"name=fl-client-dgx\"); "
        "if [ -n \"$containers\" ]; then docker rm -f $containers >/dev/null 2>&1 || true; fi; "
        # Supprime toutes les images construites localement pour le PoC
        "images=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep '^fl-' || true); "
        "if [ -n \"$images\" ]; then docker rmi -f $images >/dev/null 2>&1 || true; fi; "
        # Nettoie dépôt/clones, certificats et données générées
        f"rm -rf {quote(repo_path)}"
    )
    info(f"{host}: suppression conteneurs/images et dépôt {repo_path}")
    ssh(host, remote_cmd)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nettoyage proxy + DGX")
    parser.add_argument("--proxy-host", default=DEFAULT_PROXY_HOST, help="Entrée SSH pour le proxy (orchestrateur)")
    parser.add_argument("--dgx-host", default=DEFAULT_DGX_HOST, help="Entrée SSH pour le DGX (client)")
    parser.add_argument("--proxy-base", default=DEFAULT_PROXY_BASE, help="Répertoire racine sur le proxy")
    parser.add_argument("--dgx-base", default=DEFAULT_DGX_BASE, help="Répertoire racine sur le DGX")
    parser.add_argument("--repo-name", default=DEFAULT_REPO_NAME, help="Nom du dossier du dépôt à supprimer")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cleanup_host(args.proxy_host, args.proxy_base, args.repo_name)
    cleanup_host(args.dgx_host, args.dgx_base, args.repo_name)
    info("Nettoyage terminé")


if __name__ == "__main__":
    main()
