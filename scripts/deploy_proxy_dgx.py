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
import shlex
import subprocess
import tempfile
from pathlib import Path

DEFAULT_REPO_URL = "https://github.com/qBrabus/federatedlearning"
DEFAULT_PROXY_HOST = "PROXY"
DEFAULT_DGX_HOST = "DGX"
DEFAULT_PROXY_BASE = "/home/qladane/federated"
DEFAULT_DGX_BASE = "/raid/workspace/qladane/federated"
REPO_NAME_FALLBACK = "federatedlearning"


def info(message: str) -> None:
    print(f"[deploy] {message}")


def run_command(command: list[str]) -> None:
    subprocess.run(command, check=True)


def ssh(host: str, command: str) -> None:
    run_command(["ssh", host, f"set -euo pipefail; {command}"])


def scp(source: str, destination: str) -> None:
    run_command(["scp", "-r", source, destination])


def repo_name_from_url(url: str) -> str:
    last_segment = url.rstrip("/").split("/")[-1]
    if last_segment.endswith(".git"):
        return last_segment[:-4] or REPO_NAME_FALLBACK
    return last_segment or REPO_NAME_FALLBACK


def quote(value: str) -> str:
    return shlex.quote(value)


def clone_or_update(host: str, base_dir: str, repo_url: str, repo_name: str) -> None:
    remote_cmd = (
        f"mkdir -p {quote(base_dir)} && cd {quote(base_dir)} && "
        f"if [ -d {quote(repo_name)}/.git ]; then "
        f"git -C {quote(repo_name)} fetch --all --tags && "
        f"git -C {quote(repo_name)} pull --ff-only; "
        f"else git clone {quote(repo_url)} {quote(repo_name)}; fi"
    )
    info(f"{host}: clone/pull du dépôt")
    ssh(host, remote_cmd)


def copy_env(host: str, base_dir: str, repo_name: str, env_file: str, example_file: str) -> None:
    remote_cmd = (
        f"cd {quote(base_dir)}/{quote(repo_name)} && "
        f"cp -f {quote(example_file)} {quote(env_file)}"
    )
    info(f"{host}: copie {example_file} -> {env_file}")
    ssh(host, remote_cmd)


def build_and_run(host: str, base_dir: str, repo_name: str, component: str) -> None:
    remote_cmd = (
        f"cd {quote(base_dir)}/{quote(repo_name)} && "
        f"./build_docker_FL.sh {component} --self-signed && "
        f"./run_docker_FL.sh {component} --self-signed"
    )
    info(f"{host}: build + run {component}")
    ssh(host, remote_cmd)


def sync_certs(proxy_host: str, proxy_base: str, dgx_host: str, dgx_base: str, repo_name: str) -> None:
    proxy_certs = f"{proxy_base}/{repo_name}/certs"
    dgx_target = f"{dgx_base}/{repo_name}/"
    info(f"Copie des certificats {proxy_host}:{proxy_certs} -> {dgx_host}:{dgx_target}")

    with tempfile.TemporaryDirectory() as tmp:
        local_bundle = Path(tmp) / "certs"
        scp(f"{proxy_host}:{proxy_certs}", tmp)
        scp(str(local_bundle), f"{dgx_host}:{dgx_target}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Déploiement orchestrateur (proxy) + client (DGX)")
    parser.add_argument("--proxy-host", default=DEFAULT_PROXY_HOST, help="Entrée SSH pour le proxy (orchestrateur)")
    parser.add_argument("--dgx-host", default=DEFAULT_DGX_HOST, help="Entrée SSH pour le DGX (client)")
    parser.add_argument("--proxy-base", default=DEFAULT_PROXY_BASE, help="Répertoire racine sur le proxy")
    parser.add_argument("--dgx-base", default=DEFAULT_DGX_BASE, help="Répertoire racine sur le DGX")
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL, help="URL du dépôt Git à cloner")
    parser.add_argument("--repo-name", default="", help="Nom du dossier du dépôt (déduit de l'URL si vide)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_name = args.repo_name or repo_name_from_url(args.repo_url)

    clone_or_update(args.proxy_host, args.proxy_base, args.repo_url, repo_name)
    clone_or_update(args.dgx_host, args.dgx_base, args.repo_url, repo_name)

    copy_env(args.proxy_host, args.proxy_base, repo_name, "orchestrator/.env", "orchestrator/.env.example")
    copy_env(args.dgx_host, args.dgx_base, repo_name, "client/.env", "client/.env.example")

    build_and_run(args.proxy_host, args.proxy_base, repo_name, "orchestrator")
    sync_certs(args.proxy_host, args.proxy_base, args.dgx_host, args.dgx_base, repo_name)
    build_and_run(args.dgx_host, args.dgx_base, repo_name, "client")

    info("Déploiement terminé")


if __name__ == "__main__":
    main()
