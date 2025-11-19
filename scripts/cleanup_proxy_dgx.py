#!/usr/bin/env python3
"""Nettoyage des déploiements proxy (orchestrateur) et DGX (client).

Ce script arrête/supprime les conteneurs existants, supprime les images
Docker associées puis efface le dépôt cloné sur chaque machine distante.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from pathlib import Path

import paramiko

DEFAULT_PROXY_HOST = "PROXY"
DEFAULT_DGX_HOST = "DGX"
DEFAULT_PROXY_BASE = "/home/qladane/federated"
DEFAULT_DGX_BASE = "/raid/workspace/qladane/federated"
DEFAULT_REPO_NAME = "federatedlearning"


_PASSWORD_ENV_VARS = ("pwdsession", "PWDSSESSION", "PWDSESSION")
_DEFAULT_PORT = 22


def info(message: str, *args: object) -> None:
    """Affiche un message formaté avec le préfixe cleanup."""

    formatted = message % args if args else message
    print(f"[cleanup] {formatted}")


def _load_ssh_config() -> paramiko.SSHConfig | None:
    config_path = Path.home() / ".ssh" / "config"
    if not config_path.exists():
        return None

    config = paramiko.SSHConfig()
    with config_path.open("r", encoding="utf-8", errors="replace") as fh:
        config.parse(fh)
    return config


def _resolve_ssh_params(host: str) -> tuple[str, str | None, int, list[str]]:
    """Résout les paramètres SSH depuis ~/.ssh/config et host@user."""

    username: str | None = None
    hostname = host
    port = _DEFAULT_PORT
    key_files: list[str] = []

    if "@" in host:
        user, sep, h = host.partition("@")
        if sep:
            username, hostname = user, h

    config = _load_ssh_config()
    if config is not None:
        lookup = config.lookup(host)
        hostname = lookup.get("hostname", hostname)
        username = lookup.get("user", username)
        if "port" in lookup:
            try:
                port = int(lookup["port"])
            except ValueError:
                port = _DEFAULT_PORT
        if "identityfile" in lookup:
            key_files = lookup["identityfile"]

    return hostname, username, port, key_files


def _get_password_from_env() -> str:
    for key in _PASSWORD_ENV_VARS:
        value = os.environ.get(key)
        if value:
            return value
    return ""


def _open_paramiko_client(host: str) -> paramiko.SSHClient:
    hostname, username, port, key_files = _resolve_ssh_params(host)
    password = _get_password_from_env() or None

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    info(
        "connexion paramiko -> %s (user=%s, port=%s, keys=%s)",
        hostname,
        username or "<défaut>",
        port,
        ",".join(key_files) if key_files else "<ssh-agent/def>",
    )
    client.connect(
        hostname=hostname,
        port=port,
        username=username,
        password=password,
        look_for_keys=True,
        allow_agent=True,
        key_filename=key_files or None,
        timeout=15,
    )
    return client


def _ssh_via_paramiko(host: str, command: str) -> None:
    with _open_paramiko_client(host) as client:
        info("[%s] exécution distante: %s", host, command)
        stdin, stdout, stderr = client.exec_command(f"set -euo pipefail; {command}")
        stdout.channel.settimeout(15)
        stderr.channel.settimeout(15)

        for line in stdout:
            info("[%s] %s", host, line.rstrip("\n"))
        for line in stderr:
            info("[%s] %s", host, line.rstrip("\n"))

        rc = stdout.channel.recv_exit_status()
        if rc:
            raise subprocess.CalledProcessError(rc, command)


def ssh(host: str, command: str) -> None:
    """Évalue une commande distante via Paramiko (mot de passe/env), sinon ssh."""

    password = _get_password_from_env()
    if password:
        try:
            _ssh_via_paramiko(host, command)
            return
        except paramiko.ssh_exception.SSHException as exc:
            info(f"[{host}] échec Paramiko ({exc}), tentative via ssh binaire")

    info(f"[{host}] exécution via ssh binaire")
    subprocess.run(
        ["ssh", host, f"set -euo pipefail; {command}"],
        check=True,
    )


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
