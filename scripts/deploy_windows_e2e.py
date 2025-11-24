#!/usr/bin/env python3
"""Déploiement/validation orchestrateur + client DGX depuis Windows.

Le script utilise uniquement ``ssh``/``scp`` côté machine de pilotage. Sur les
hôtes distants (PROXY et DGX), il peut installer Git/Docker si absents puis :

1. Clone ou met à jour le dépôt sur PROXY (hub) et DGX (client).
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
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import socket
import textwrap
import time
from pathlib import Path
from typing import Iterable

DEFAULT_REPO_URL = "https://github.com/qBrabus/federatedlearning"
DEFAULT_PROXY_HOST = "PROXY"
DEFAULT_DGX_HOST = "DGX"
DEFAULT_PROXY_BASE = "~/federated"
DEFAULT_DGX_BASE = "/raid/workspace/qladane/federated"
DEFAULT_REPO_NAME = "federatedlearning"
DEFAULT_SERVER_PORT = 8443
DEFAULT_SERVER_APP_PORT = 9091
ALLOWED_PORTS = {DEFAULT_SERVER_PORT, DEFAULT_SERVER_APP_PORT}
HOST_ALIASES = {
    "PROXY": "10.200.241.101",
    "DGX": "10.200.50.45",
}

LOG_FORMAT_FILE = "%(asctime)s | %(levelname)-8s | %(message)s"
LOG_FORMAT_CONSOLE = "%(levelname)-8s | %(message)s"

_PASSWORD_ENV_VARS = ("pwdsession", "PWDSSESSION", "PWDSESSION")
_warned_password_tool = False
_use_paramiko = False
_ssh_config = None


def _get_password_from_env() -> str:
    for key in _PASSWORD_ENV_VARS:
        value = os.environ.get(key)
        if value:
            return value
    return ""


def _ensure_paramiko_ready(logger: logging.Logger) -> None:
    """Active Paramiko dès qu'un mot de passe ou Windows est détecté.

    Sans ce déclenchement anticipé, la première commande SSH pouvait repasser
    par ``ssh`` système et déclencher une invite interactive malgré la présence
    de ``pwdsession`` dans l'environnement.
    """

    global _use_paramiko

    if _use_paramiko:
        return

    password = _get_password_from_env()
    if password or os.name == "nt":
        if not _enable_paramiko(logger):
            if password:
                raise RuntimeError(
                    "Variable pwdsession détectée mais Paramiko indisponible. "
                    "Installez Paramiko pour éviter toute invite de mot de passe."
                )
        return


def _resolve_alias(host: str) -> str:
    """Remplace les alias PROXY/DGX par les IP attendues."""

    return HOST_ALIASES.get(host.upper(), host)


def _load_ssh_config() -> "paramiko.SSHConfig | None":
    try:
        import paramiko
    except ImportError:
        return None

    config_path = Path.home() / ".ssh" / "config"
    if not config_path.exists():
        return None

    config = paramiko.SSHConfig()
    with config_path.open("r", encoding="utf-8", errors="replace") as fh:
        config.parse(fh)
    return config


def _enable_paramiko(logger: logging.Logger) -> bool:
    """Active Paramiko si disponible (mot de passe ou clés SSH)."""

    global _use_paramiko, _ssh_config, _warned_password_tool

    if _use_paramiko:
        return True

    try:
        import paramiko  # type: ignore
    except ImportError:
        if not _warned_password_tool:
            logger.warning(
                "Paramiko non installé : impossible d'utiliser l'authentification mot de passe. "
                "Installez-le via 'pip install paramiko' pour éviter les invites interactives."
            )
            _warned_password_tool = True
        return False

    _ssh_config = _load_ssh_config()
    _use_paramiko = True
    info(logger, "Paramiko activé pour les opérations SSH/SCP (password ou clés).")
    return True


def _resolve_ssh_params(host: str) -> tuple[str, str | None, int, list[str]]:
    """Résout l'hôte, l'utilisateur, le port et les clés depuis ~/.ssh/config."""

    username: str | None = None
    hostname = HOST_ALIASES.get(host.upper(), host)
    port = 22
    key_files: list[str] = []

    if "@" in host:
        user, sep, h = host.partition("@")
        if sep:
            username, hostname = user, h

    if _ssh_config is not None:
        lookup = _ssh_config.lookup(host)
        hostname = lookup.get("hostname", hostname)
        username = lookup.get("user", username)
        if "port" in lookup:
            try:
                port = int(lookup["port"])
            except ValueError:
                port = 22
        if "identityfile" in lookup:
            key_files = lookup["identityfile"]

    return hostname, username, port, key_files


def _open_paramiko_client(host: str):
    import paramiko

    hostname, username, port, key_files = _resolve_ssh_params(host)
    password = _get_password_from_env() or None
    logger = logging.getLogger("deploy-win")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    logger.debug(
        "[paramiko] connexion %s (user=%s, port=%s, keys=%s, password=%s)",
        hostname,
        username or "<défaut>",
        port,
        ",".join(key_files) if key_files else "<ssh-agent/def>",
        "oui" if password else "non",
    )
    try:
        client.connect(
            hostname=hostname,
            port=port,
            username=username,
            password=password,
            look_for_keys=True,
            allow_agent=True,
            key_filename=key_files or None,
            timeout=10,
        )
    except paramiko.AuthenticationException:
        global _use_paramiko

        logger.error(
            "Échec d'authentification Paramiko pour %s (user=%s, port=%s). ",
            hostname,
            username or "<défaut>",
            port,
        )
        logger.info(
            "Bascule automatique vers ssh.exe/scp natif afin d'utiliser la configuration OpenSSH existante."
        )
        _use_paramiko = False
        raise
    return client


def _fallback_to_system_ssh(error: Exception, logger: logging.Logger, label: str) -> bool:
    """Permet de repasser sur ssh.exe/scp si Paramiko échoue (ex: clés non chargées)."""

    try:
        import paramiko
    except ImportError:  # pragma: no cover - seulement possible si Paramiko supprimé à chaud
        return False

    if isinstance(error, paramiko.AuthenticationException):
        logger.warning("[%s] Authentification Paramiko échouée, tentative via ssh.exe", label)
    elif isinstance(error, paramiko.SSHException):
        logger.warning("[%s] Erreur Paramiko (%s), tentative via ssh.exe", label, error)
    else:
        return False

    global _use_paramiko
    _use_paramiko = False
    return True


def _ssh_prefix(logger: logging.Logger, tool: str) -> list[str]:
    """Prépare le préfixe ssh/scp en intégrant le mot de passe si possible."""

    if _get_password_from_env():
        _enable_paramiko(logger)

    return [tool]


class _ColorFormatter(logging.Formatter):
    RESET = "\033[0m"
    INFO_COLOR = "\033[32m"  # Green
    TEST_COLOR = "\033[94m"  # Light blue
    COLOR_BY_LEVEL = {
        logging.DEBUG: "\033[36m",  # Cyan
        logging.INFO: INFO_COLOR,
        logging.WARNING: "\033[38;5;208m",  # Orange
        logging.ERROR: "\033[31m",  # Red
        logging.CRITICAL: "\033[35m",  # Magenta
    }

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        base = super().format(record)
        message_lower = record.getMessage().lower()

        is_test_log = getattr(record, "is_test", False) or bool(
            re.search(r"\btests?\b", message_lower)
        )
        color = self.TEST_COLOR if is_test_log else self.COLOR_BY_LEVEL.get(record.levelno, "")

        return f"{color}{base}{self.RESET}" if color else base


def test_log(logger: logging.Logger, msg: str, *args) -> None:
    logger.info(msg, *args, extra={"is_test": True})


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


def run_command(
    command: list[str], logger: logging.Logger, label: str, *, mark_as_test: bool = False
) -> None:
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
        logger.info("[%s] %s", label, line.rstrip(), extra={"is_test": mark_as_test})

    process.wait()
    logger.debug("[%s] terminé (rc=%s)", label, process.returncode)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command)


def run_command_capture(
    command: list[str], logger: logging.Logger, label: str, *, mark_as_test: bool = False
) -> str:
    """Exécute une commande et retourne la sortie standard."""

    quoted = " ".join(shlex.quote(part) for part in command)
    logger.debug("[%s] %s", label, quoted)

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    if result.stdout:
        for line in result.stdout.splitlines():
            logger.info("[%s] %s", label, line, extra={"is_test": mark_as_test})

    if result.stderr:
        for line in result.stderr.splitlines():
            logger.info("[%s] %s", label, line, extra={"is_test": mark_as_test})

    logger.debug("[%s] terminé (rc=%s)", label, result.returncode)
    if result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout, stderr=result.stderr
        )

    return result.stdout


def _scp_paramiko(source: str, destination: str, logger: logging.Logger, label: str) -> None:
    import posixpath

    def _is_remote(path: str) -> bool:
        return Path(path).drive == "" and ":" in path

    def _split_remote(path: str) -> tuple[str, str]:
        host, _, remote_path = path.partition(":")
        return host, remote_path.strip("\"")

    if _is_remote(source) and not _is_remote(destination):
        host, remote_path = _split_remote(source)
        with _open_paramiko_client(host) as client:
            with client.open_sftp() as sftp:
                remote_norm = sftp.normalize(remote_path)
                logger.info("[%s] téléchargement %s -> %s", label, remote_norm, destination)
                sftp.get(remote_norm, destination)
        return

    if _is_remote(destination) and not _is_remote(source):
        host, remote_path = _split_remote(destination)
        with _open_paramiko_client(host) as client:
            with client.open_sftp() as sftp:
                remote_norm = sftp.normalize(remote_path)
                remote_dir = posixpath.dirname(remote_norm)
                if remote_dir:
                    try:
                        sftp.stat(remote_dir)
                    except FileNotFoundError:
                        sftp.mkdir(remote_dir)
                logger.info("[%s] upload %s -> %s", label, source, remote_norm)
                sftp.put(source, remote_norm)
        return

    raise ValueError("La copie Paramiko supporte seulement local<->remote")


def _run_paramiko_command(
    host: str,
    command: str,
    logger: logging.Logger,
    label: str,
    capture: bool = False,
    mark_as_test: bool = False,
) -> str:
    from io import StringIO

    with _open_paramiko_client(host) as client:
        logger.debug("[%s] paramiko exec: %s", label, command)
        stdin, stdout, stderr = client.exec_command(f"set -euo pipefail; {command}")
        stdout.channel.settimeout(10)
        stderr.channel.settimeout(10)

        output_buf = StringIO()
        err_buf = StringIO()

        try:
            if capture:
                stdout_data = stdout.read().decode()
                stderr_data = stderr.read().decode()
                if stdout_data:
                    output_buf.write(stdout_data)
                    for line in stdout_data.splitlines():
                        logger.info("[%s] %s", label, line, extra={"is_test": mark_as_test})
                if stderr_data:
                    err_buf.write(stderr_data)
                    for line in stderr_data.splitlines():
                        logger.info("[%s] %s", label, line, extra={"is_test": mark_as_test})
            else:
                for line in stdout:
                    decoded = line.rstrip("\n")
                    output_buf.write(decoded + "\n")
                    logger.info("[%s] %s", label, decoded, extra={"is_test": mark_as_test})
                for line in stderr:
                    decoded = line.rstrip("\n")
                    err_buf.write(decoded + "\n")
                    logger.info("[%s] %s", label, decoded, extra={"is_test": mark_as_test})
        except socket.timeout:
            # Si aucune sortie n'est émise (ex: docker stop silencieux),
            # on ne considère pas cela comme un échec tant que la commande
            # s'est terminée correctement.
            pass

        rc = stdout.channel.recv_exit_status()
        logger.debug("[%s] terminé (rc=%s)", label, rc)
        if rc:
            raise subprocess.CalledProcessError(
                rc, command, output=output_buf.getvalue(), stderr=err_buf.getvalue()
            )

        return output_buf.getvalue()


def ensure_prerequisites(host: str, logger: logging.Logger) -> None:
    """Installe Git/Docker si absents et démarre Docker.

    Utilise le script officiel Docker (get.docker.com) pour limiter
    les dépendances. Les commandes sont exécutées via SSH afin de
    garder un journal homogène (console + fichier log).
    """

    remote_cmd = r"""
set -euo pipefail

have_git=true
have_docker=true

if ! command -v git >/dev/null 2>&1; then
  have_git=false
fi

if ! command -v docker >/dev/null 2>&1; then
  have_docker=false
fi

if [ "$have_git" = false ]; then
  if command -v sudo >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y git
  else
    apt-get update -y
    apt-get install -y git
  fi
fi

if [ "$have_docker" = false ]; then
  curl -fsSL https://get.docker.com | sh
  if command -v sudo >/dev/null 2>&1; then
    sudo systemctl enable --now docker 2>/dev/null || true
    sudo usermod -aG docker "$(whoami)" || true
  else
    systemctl enable --now docker 2>/dev/null || true
    usermod -aG docker "$(whoami)" || true
  fi
fi

# S'assure que Docker répond.
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1; then
    sudo systemctl start docker || true
  else
    systemctl start docker || true
  fi
fi
"""

    info(logger, "[%s] vérification/installation Git & Docker", host)
    ssh(host, remote_cmd, logger, label=f"prereqs {host}")


def ssh(
    host: str,
    command: str,
    logger: logging.Logger,
    label: str | None = None,
    *,
    mark_as_test: bool = False,
) -> None:
    _ensure_paramiko_ready(logger)

    if _use_paramiko:
        try:
            _run_paramiko_command(
                host,
                command,
                logger,
                label or f"ssh {host}",
                mark_as_test=mark_as_test,
            )
            return
        except Exception as exc:  # pragma: no cover - dépend des clés utilisateur
            if not _fallback_to_system_ssh(exc, logger, label or f"ssh {host}"):
                raise

    run_command(
        [*_ssh_prefix(logger, "ssh"), host, f"set -euo pipefail; {command}"],
        logger,
        label or f"ssh {host}",
        mark_as_test=mark_as_test,
    )


def ssh_capture(
    host: str,
    command: str,
    logger: logging.Logger,
    label: str | None = None,
    *,
    mark_as_test: bool = False,
) -> str:
    _ensure_paramiko_ready(logger)

    if _use_paramiko:
        try:
            return _run_paramiko_command(
                host,
                command,
                logger,
                label or f"ssh {host}",
                capture=True,
                mark_as_test=mark_as_test,
            )
        except Exception as exc:  # pragma: no cover - dépend des clés utilisateur
            if not _fallback_to_system_ssh(exc, logger, label or f"ssh {host}"):
                raise

    return run_command_capture(
        [*_ssh_prefix(logger, "ssh"), host, f"set -euo pipefail; {command}"],
        logger,
        label or f"ssh {host}",
        mark_as_test=mark_as_test,
    )


def scp(
    source: str,
    destination: str,
    logger: logging.Logger,
    label: str = "scp",
    *,
    mark_as_test: bool = False,
) -> None:
    _ensure_paramiko_ready(logger)

    if _use_paramiko:
        try:
            _scp_paramiko(source, destination, logger, label)
            return
        except Exception as exc:  # pragma: no cover - dépend des clés utilisateur
            if not _fallback_to_system_ssh(exc, logger, label):
                raise

    run_command(
        [*_ssh_prefix(logger, "scp"), "-r", source, destination],
        logger,
        label,
        mark_as_test=mark_as_test,
    )


def quote(value: str) -> str:
    return shlex.quote(value)


def normalize_remote_path(path: str) -> str:
    """Évite le tilde littéral en SSH en le remplaçant par $HOME."""

    if path == "~":
        return "$HOME"
    if path.startswith("~/"):
        return f"$HOME/{path[2:]}"
    return path


def quote_path(path: str) -> str:
    """Quote un chemin tout en autorisant l'expansion de $HOME."""

    if path.startswith("$HOME"):
        return f'"{path}"'
    return shlex.quote(path)


def build_remote_repo_path(base_dir: str, repo_name: str) -> str:
    """Construit un chemin repo côté hôte distant en conservant $HOME."""

    base = normalize_remote_path(base_dir).rstrip("/")
    return f"{base}/{repo_name}"


def scp_quote_remote(path: str) -> str:
    """Prépare un chemin distant pour ``scp``.

    ``run_command`` invoque ``scp`` sans passer par un shell, il ne faut donc
    pas entourer le chemin de guillemets (ils seraient transmis littéralement
    et ``scp`` chercherait un fichier nommé """"/path"""").
    On retourne le chemin tel quel pour conserver les espaces éventuels dans le
    même argument.
    """

    return path


def resolve_remote_path(host: str, path: str, logger: logging.Logger) -> str:
    """Résout un chemin distant en chemin absolu sans variables shell.

    ``scp`` n'expanse pas ``$HOME``/``~`` côté distant. On demande donc à la
    machine cible de résoudre le chemin (avec ``expanduser``/``expandvars``) via
    ``python`` puis on réutilise ce chemin absolu pour les transferts. Cela
    évite les erreurs « No such file » lorsque le dépôt vit sous ``$HOME``.
    """

    script = rf"""
python_cmd=$(command -v python3 || command -v python || true)
if [ -z "$python_cmd" ]; then
  echo "Python absent sur {host}" >&2
  exit 1
fi

"$python_cmd" - <<'PY'
import os, pathlib
path = os.path.abspath(os.path.expanduser(os.path.expandvars({path!r})))
print(pathlib.Path(path))
PY
"""

    resolved = ssh_capture(host, script, logger, label=f"realpath {host}")
    return resolved.strip().splitlines()[-1]


def repo_name_from_url(url: str) -> str:
    segment = url.rstrip("/").split("/")[-1]
    return segment[:-4] if segment.endswith(".git") else segment or DEFAULT_REPO_NAME


def clone_or_pull(host: str, base_dir: str, repo_url: str, repo_name: str, logger: logging.Logger) -> None:
    remote_cmd = (
        f"mkdir -p {quote_path(base_dir)} && cd {quote_path(base_dir)} && "
        f"if [ -d {quote(repo_name)}/.git ]; then "
        f"git -C {quote(repo_name)} fetch --all --tags && "
        f"git -C {quote(repo_name)} pull --ff-only; "
        f"else git clone {quote(repo_url)} {quote(repo_name)}; fi"
    )
    info(logger, "[%s] synchro Git", host)
    ssh(host, remote_cmd, logger)


def ensure_env(host: str, base_dir: str, repo_name: str, env_file: str, example_file: str, logger: logging.Logger) -> None:
    remote_cmd = (
        f"cd {quote_path(base_dir)}/{quote(repo_name)} && "
        f"if [ ! -f {quote(env_file)} ]; then cp -f {quote(example_file)} {quote(env_file)}; fi"
    )
    info(logger, "[%s] vérification de %s", host, env_file)
    ssh(host, remote_cmd, logger)


def write_test_env(
    host: str,
    base_dir: str,
    repo_name: str,
    component: str,
    server_port: int,
    logger: logging.Logger,
    *,
    server_app_port: int = DEFAULT_SERVER_APP_PORT,
    server_host: str = "127.0.0.1",
) -> None:
    """Force des valeurs compatibles avec un test 1 client / mTLS."""

    if component == "orchestrator":
        content = f"""FLOWER_SERVER_ADDRESS=0.0.0.0
FLOWER_SERVER_PORT=8080
HOST_PORT_OVERRIDE={server_port}
FLOWER_SERVERAPP_PORT={server_app_port}
HOST_SERVERAPP_PORT_OVERRIDE={server_app_port}
GRPC_MAX_MESSAGE_LENGTH=536870912
NUM_ROUNDS=1
MIN_FIT_CLIENTS=1
MIN_AVAILABLE_CLIENTS=1
CA_CERT_PATH=/certs/ca.crt
SERVER_CERT_PATH=/certs/server.crt
SERVER_KEY_PATH=/certs/server.key
"""
        remote_cmd = (
            f"cd {quote_path(base_dir)}/{quote(repo_name)} && "
            f"printf %s {quote(content)} > orchestrator/.env"
        )
    else:
        content = f"""SERVER_ADDRESS={server_host}:{server_port}
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
            f"cd {quote_path(base_dir)}/{quote(repo_name)} && "
            f"printf %s {quote(content)} > client/.env"
        )

    info(logger, "[%s] écriture .env de test (%s)", host, component)
    ssh(host, remote_cmd, logger)


def _list_proxy_addresses(proxy_host: str, logger: logging.Logger) -> list[str]:
    """Retourne les adresses IPv4 connues du proxy (hors loopback et réseaux Docker)."""

    cmd = "hostname -I 2>/dev/null || true"
    output = ssh_capture(proxy_host, cmd, logger, label="hostname -I")
    addresses = []
    for token in output.split():
        if not re.match(r"^\d+\.\d+\.\d+\.\d+$", token):
            continue
        # Exclure localhost et les réseaux docker0 les plus courants pour éviter de
        # sélectionner l'IP interne du conteneur plutôt qu'une adresse LAN joignable.
        if (
            token.startswith("127.")
            or token.startswith("172.17.")
            or token.startswith("172.18.")
        ):
            continue
        addresses.append(token)
    return addresses


def _is_reachable_from_dgx(
    dgx_host: str,
    target_host: str,
    target_port: int,
    logger: logging.Logger,
    accept_conn_refused: bool = False,
) -> bool:
    """Teste une connexion TCP depuis le DGX vers la cible.

    Quand ``accept_conn_refused`` est vrai, une erreur ``ECONNREFUSED`` est
    considérée comme un signe de chemin réseau valide (le port n'écoute pas
    encore mais n'est pas filtré), ce qui permet de choisir un port
    atteignable avant de démarrer l'orchestrateur.
    """

    script = rf"""
pybin=$(command -v python3 || command -v python || true)
if [ -z "$pybin" ]; then
  echo "python3/python introuvable pour le test TCP" >&2
  exit 1
fi

"$pybin" - <<'PY'
import errno, socket, sys
host = {target_host!r}
port = {target_port}
accept_conn_refused = {accept_conn_refused!r}
try:
    with socket.create_connection((host, port), timeout=3):
        sys.exit(0)
except OSError as exc:  # pragma: no cover - diagnostic
    if accept_conn_refused and exc.errno == errno.ECONNREFUSED:
        print("ECONNREFUSED (chemin réseau OK, port fermé)")
        sys.exit(0)
    print(exc)
    sys.exit(1)
PY
"""

    try:
        ssh(dgx_host, script, logger, label="tcp-probe", mark_as_test=True)
        return True
    except subprocess.CalledProcessError:
        return False


def select_server_host(
    proxy_host: str, dgx_host: str, server_port: int, logger: logging.Logger
) -> str:
    """Choisit l'adresse du proxy joignable depuis le DGX."""

    proxy_hostname, _, _, _ = _resolve_ssh_params(proxy_host)
    candidates: list[str] = [proxy_hostname]
    for addr in _list_proxy_addresses(proxy_host, logger):
        if addr not in candidates:
            candidates.append(addr)

    info(logger, "[%s] tentative de sélection de l'adresse proxy joignable", dgx_host)
    for candidate in candidates:
        if _is_reachable_from_dgx(
            dgx_host,
            candidate,
            server_port,
            logger,
            accept_conn_refused=True,
        ):
            info(logger, "[%s] adresse %s joignable, utilisation", dgx_host, candidate)
            return candidate

    info(logger, "[%s] aucune adresse joignable trouvée, utilisation de %s", dgx_host, proxy_hostname)
    return proxy_hostname


def select_server_endpoint(
    proxy_host: str, dgx_host: str, requested_port: int, logger: logging.Logger
) -> tuple[str, int]:
    """Valide l'utilisation stricte du port serveur demandé.

    Seul le port 8443 est autorisé pour l'orchestrateur. Le script échoue si ce
    port est occupé côté proxy ou si aucune adresse du proxy n'est atteignable
    depuis le DGX sur ce port, afin d'éviter toute dérive en production.
    """

    if requested_port != DEFAULT_SERVER_PORT:
        raise ValueError(
            f"Port serveur non autorisé ({requested_port}). Utilisez {DEFAULT_SERVER_PORT}."
        )

    def _ensure_port_available(host: str, port: int) -> None:
        remote_cmd = (
            textwrap.dedent(
                r"""
    python_cmd=$(command -v python3 || command -v python || true)
    if [ -z "$python_cmd" ]; then
      echo "[local] Python est requis pour vérifier le port $requested_port (apt-get install -y python3)" >&2
      exit 1
    fi

    header="[local] Diagnostic port $requested_port sur $(hostname -f 2>/dev/null || hostname)"
    echo "$header"

    details=$("$python_cmd" - <<'PY'
import errno
import socket
import sys
import platform

port = $requested_port
try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        result = s.connect_ex(("127.0.0.1", port))
except OSError as exc:
    print(
        f"[local] Erreur système pendant le test de connexion: {exc} "
        f"(errno={getattr(exc, 'errno', '?')}, platform={platform.system()})"
    )
    sys.exit(2)

if result == 0:
    print(f"[local] Port {port} déjà utilisé : un service écoute sur 127.0.0.1")
    sys.exit(10)

if result == errno.EACCES:
    print(f"[local] Port {port} inaccessible sans privilèges élevés (EACCES)")
    sys.exit(11)

print(f"[local] Port {port} disponible : aucune écoute détectée sur 127.0.0.1")
sys.exit(0)
PY
)

    rc=$?
    printf "%s\n" "$details"

    if [ $rc -eq 10 ] || [ $rc -eq 11 ] || [ $rc -eq 2 ]; then
      echo "[local] Collecte d'informations complémentaires (processus écoutant)..."
      if command -v ss >/dev/null 2>&1; then
        echo "[local] Détails des écoutes existantes (ss) :"
        ss -ltnp 2>/dev/null | grep -E ":$requested_port\\b" || echo "[local] Aucun détail trouvé avec ss"
      elif command -v netstat >/dev/null 2>&1; then
        echo "[local] Détails des écoutes existantes (netstat) :"
        netstat -ano 2>/dev/null | grep -E ":$requested_port\\b" || echo "[local] Aucun détail trouvé avec netstat"
      else
        echo "[local] Aucun outil (ss/netstat) disponible pour afficher les processus en écoute"
      fi
      if command -v lsof >/dev/null 2>&1; then
        echo "[local] Processus utilisant le port (lsof) :"
        lsof -iTCP:$requested_port -sTCP:LISTEN || echo "[local] Aucun processus listé par lsof"
      fi
    fi

    exit $rc
                """
            ).replace("$requested_port", str(port))
        )

        try:
            ssh_capture(host, remote_cmd, logger, label="port-check")
        except subprocess.CalledProcessError as exc:
            output_parts: list[str] = []
            if hasattr(exc, "output") and exc.output:
                output_parts.append(str(exc.output))
            if hasattr(exc, "stderr") and exc.stderr:
                output_parts.append(str(exc.stderr))
            output = "\n".join(part for part in output_parts if part)
            rc = getattr(exc, "returncode", "?")

            if rc == 10:
                reason = f"Port {port} déjà utilisé sur {host} (écoute détectée)."
            elif rc == 11:
                reason = (
                    f"Port {port} inaccessible sur {host} : privilèges élevés requis (EACCES)."
                )
            elif rc == 2:
                reason = (
                    f"Erreur système locale pendant le test de socket sur {host} (voir log ci-dessous)."
                )
            elif rc == 1:
                reason = f"Python manquant sur {host} pour vérifier le port {port}."
            elif rc == 255:
                reason = (
                    f"Connexion SSH vers {host} impossible ou refusée pendant le diagnostic du port {port}."
                )
            else:
                reason = f"Échec du diagnostic du port {port} sur {host} (rc={rc})."

            command_preview = "\n".join(remote_cmd.strip().splitlines()[:8])
            message = textwrap.dedent(
                f"""
                [{host}] {reason}
                Journal du diagnostic SSH (port-check):
                {output.strip() or '<aucune sortie retournée>'}
                Aperçu de la commande envoyée (port-check):
                {command_preview}
                Consultez le journal complet pour les commandes SSH exécutées et libérez le port ou ajustez les permissions avant de relancer.
                """
            ).strip()
            logger.error(message)
            raise RuntimeError(message) from exc

    _ensure_port_available(proxy_host, requested_port)

    server_host = select_server_host(proxy_host, dgx_host, requested_port, logger)

    return server_host, requested_port


def wait_for_proxy_port(
    dgx_host: str,
    server_host: str,
    server_port: int,
    logger: logging.Logger,
    attempts: int = 6,
    delay: int = 5,
) -> None:
    """Attend que le port de l'orchestrateur devienne joignable depuis le DGX."""

    info(
        logger,
        "[%s] attente de l'ouverture du port %s sur %s (tentatives=%s, delai=%ss)",
        dgx_host,
        server_port,
        server_host,
        attempts,
        delay,
    )

    for attempt in range(1, attempts + 1):
        if _is_reachable_from_dgx(
            dgx_host, server_host, server_port, logger, accept_conn_refused=False
        ):
            info(
                logger,
                "[%s] port %s joignable sur %s (tentative %s/%s)",
                dgx_host,
                server_port,
                server_host,
                attempt,
                attempts,
            )
            return

        if attempt < attempts:
            time.sleep(delay)

    raise RuntimeError(
        f"Le port {server_port} sur {server_host} n'est pas joignable depuis {dgx_host} "
        "après démarrage de l'orchestrateur. Vérifiez le démarrage du service ou le filtrage réseau."
    )


def find_available_port(host: str, requested_port: int, logger: logging.Logger) -> int:
    """Valide la disponibilité du port demandé sans fallback.

    Les déploiements n'autorisent que les ports 8443 (orchestrateur) et 9091
    (ServerApp). Le script échoue si le port est occupé ou inaccessible.
    """

    if requested_port not in ALLOWED_PORTS:
        raise ValueError(
            "Port non autorisé. Seuls %s sont acceptés et aucun fallback ne sera tenté." %
            ", ".join(str(p) for p in sorted(ALLOWED_PORTS))
        )

    info(
        logger,
        "[%s] vérification stricte du port %s (aucune tentative sur d'autres ports)",
        host,
        requested_port,
    )
    remote_cmd = (
        textwrap.dedent(
            """
python_cmd=$(command -v python3 || command -v python || true)
if [ -z "$python_cmd" ]; then
  echo "Python est requis pour vérifier le port __PORT__" >&2
  exit 1
fi

"$python_cmd" - <<'PY'
import errno
import socket
import sys

port = __PORT__
try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        result = s.connect_ex(("127.0.0.1", port))
except OSError as exc:
    print(f"Erreur système pendant le test de connexion: {exc}")
    sys.exit(2)

if result == 0:
    print(f"Port {port} déjà utilisé : un service écoute sur 127.0.0.1")
    sys.exit(10)

if result == errno.EACCES:
    print(f"Port {port} inaccessible sans privilèges élevés (EACCES)")
    sys.exit(11)

print(port)
sys.exit(0)
PY
"""
        ).replace("__PORT__", str(requested_port))
    )

    try:
        output = ssh_capture(host, remote_cmd, logger, label="port-check")
    except subprocess.CalledProcessError as exc:  # noqa: PERF203 - diagnostic explicite
        raise RuntimeError(
            f"Port {requested_port} indisponible sur {host}: {exc.output}"
        ) from exc

    last_line = output.strip().splitlines()[-1] if output.strip() else ""
    if last_line != str(requested_port):
        raise RuntimeError(
            f"Port {requested_port} indisponible sur {host}: {output!r}"
        )

    return requested_port


def sync_self_signed_ca(
    proxy_host: str,
    proxy_base: str,
    dgx_host: str,
    dgx_base: str,
    repo_name: str,
    logger: logging.Logger,
) -> None:
    """Copie la CA auto-signée générée sur le proxy vers le DGX.

    Sans cette synchronisation, chaque hôte génère sa propre CA, ce qui entraîne
    un échec du handshake TLS côté client (le certificat du serveur n'est pas
    signé par la même autorité que celle connue du client). On récupère donc le
    couple ``ca.crt``/``ca.key`` du proxy puis on le pousse vers le DGX avant la
    construction/lancement du client.
    """

    cert_root = Path(tempfile.mkdtemp(prefix="flwr-ca-"))
    try:
        proxy_repo = resolve_remote_path(
            proxy_host, build_remote_repo_path(proxy_base, repo_name), logger
        )
        dgx_repo = resolve_remote_path(
            dgx_host, build_remote_repo_path(dgx_base, repo_name), logger
        )

        proxy_ca_key = f"{proxy_repo}/certs/ca.key"

        # La CA devrait se trouver dans certs/ca.crt, mais certains environnements
        # n'ont que la copie placée dans certs/orchestrator/ca.crt (lorsqu'aucune
        # CA globale n'a été initialisée avant le build). On cherche donc le
        # premier fichier existant pour éviter un échec « No such file or
        # directory » lors du scp.
        proxy_ca_crt_candidates = [
            f"{proxy_repo}/certs/ca.crt",
            f"{proxy_repo}/certs/orchestrator/ca.crt",
        ]
        proxy_ca_crt = ""

        ca_detection_script = "\n".join(
            [
                "for p in \"" + "\" \"".join(proxy_ca_crt_candidates) + "\"; do",
                "  if [ -f \"$p\" ]; then",
                "    echo \"$p\"",
                "    break",
                "  fi",
                "done",
            ]
        )

        proxy_ca_crt = (
            ssh_capture(proxy_host, ca_detection_script, logger, label="locate ca.crt")
            .strip()
            .splitlines()
        )

        proxy_ca_crt = proxy_ca_crt[-1] if proxy_ca_crt else ""

        if not proxy_ca_crt:
            raise RuntimeError(
                "Impossible de localiser ca.crt sur le proxy. Relancez le build avec --self-signed."
            )

        # S'assurer que la clef de CA existe également avant la copie.
        ssh(
            proxy_host,
            f"test -f {quote_path(proxy_ca_key)}",
            logger,
            label="check ca.key",
        )

        local_ca_crt = cert_root / "ca.crt"
        local_ca_key = cert_root / "ca.key"

        info(logger, "[cert-sync] récupération de la CA auto-signée depuis PROXY")
        scp(
            f"{proxy_host}:{scp_quote_remote(proxy_ca_crt)}",
            str(local_ca_crt),
            logger,
            label="scp ca.crt",
        )
        scp(
            f"{proxy_host}:{scp_quote_remote(proxy_ca_key)}",
            str(local_ca_key),
            logger,
            label="scp ca.key",
        )

        info(logger, "[cert-sync] distribution de la CA vers DGX")
        ssh(
            dgx_host,
            f"mkdir -p {quote_path(dgx_repo)}/certs && chmod 755 {quote_path(dgx_repo)}/certs",
            logger,
            label="prepare cert dir",
        )
        dgx_ca_crt = f"{dgx_repo}/certs/ca.crt"
        dgx_ca_key = f"{dgx_repo}/certs/ca.key"

        scp(
            str(local_ca_crt),
            f"{dgx_host}:{scp_quote_remote(dgx_ca_crt)}",
            logger,
            label="push ca.crt",
        )
        scp(
            str(local_ca_key),
            f"{dgx_host}:{scp_quote_remote(dgx_ca_key)}",
            logger,
            label="push ca.key",
        )
        ssh(
            dgx_host,
            f"chmod 644 {quote_path(dgx_repo)}/certs/ca.crt {quote_path(dgx_repo)}/certs/ca.key",
            logger,
            label="fix perms",
        )
    finally:
        shutil.rmtree(cert_root, ignore_errors=True)


def build_and_run(host: str, base_dir: str, repo_name: str, component: str, logger: logging.Logger, self_signed: bool) -> None:
    flags = "--self-signed" if self_signed else ""
    # Conserver systématiquement les conteneurs pour pouvoir consulter les logs
    # même si le processus se termine prématurément (ex: crash orchestrateur).
    keep_logs_prefix = "KEEP_CONTAINER_LOGS=true "
    container_name = "fl-orchestrator" if component == "orchestrator" else "fl-client-dgx"
    remote_cmd = (
        f"cd {quote_path(base_dir)}/{quote(repo_name)} && "
        f"docker rm -f {container_name} >/dev/null 2>&1 || true && "
        f"./build_docker_FL.sh {component} {flags} && "
        f"{keep_logs_prefix}./run_docker_FL.sh {component} {flags} --detach"
    )
    info(logger, "[%s] build + run %s", host, component)
    ssh(host, remote_cmd, logger)


def check_docker(host: str, logger: logging.Logger) -> None:
    ssh(host, "docker --version", logger, label=f"docker {host}")
    ssh(
        host,
        "docker info --format 'Engine: {{.ServerVersion}} | Storage: {{.Driver}} | Root: {{.DockerRootDir}}'",
        logger,
        label=f"docker-info {host}",
    )


def check_containers(host: str, logger: logging.Logger) -> None:
    ssh(
        host,
        "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'",
        logger,
        label=f"docker-ps {host}",
        mark_as_test=True,
    )
    ssh(
        host,
        "docker ps -a --format 'table {{.Names}}\t{{.CreatedAt}}\t{{.Status}}'",
        logger,
        label=f"docker-history {host}",
        mark_as_test=True,
    )


def container_exists(host: str, container: str, logger: logging.Logger) -> bool:
    """Retourne True si le conteneur existe (en cours ou arrêté)."""

    cmd = (
        "if docker ps -a --format '{{.Names}}' | "
        f"grep -Fx --quiet {quote(container)}; then echo present; fi"
    )
    output = ssh_capture(host, cmd, logger, label="check-container")
    return "present" in output


def assert_container_running(host: str, container: str, logger: logging.Logger) -> None:
    """Vérifie que le conteneur est démarré avant d'enchaîner les tests."""

    status_cmd = (
        "state=$(docker inspect -f '{{.State.Status}}' "
        f"{quote(container)} 2>/dev/null || true); echo $state"
    )

    status = ssh_capture(host, status_cmd, logger, label="container-state").strip()
    if status != "running":
        tail_logs(host, container, logger)
        raise RuntimeError(
            f"[{host}] conteneur {container} indisponible (état: {status or 'inconnu'})"
        )


def orchestrator_network_debug(
    host: str, server_port: int, server_app_port: int, logger: logging.Logger
) -> None:
    """Collecte des diagnostics réseau détaillés sur l'orchestrateur.

    Objectif: comprendre rapidement pourquoi le port attendu n'est pas
    accessible (conteneur arrêté, port non publié, service non démarré).
    """

    remote_cmd = "".join(
        [
            "docker inspect -f 'State: {{.State.Status}} | Restart: {{json .HostConfig.RestartPolicy}} | Ports: {{json .NetworkSettings.Ports}}' fl-orchestrator || true; ",
            f"if command -v ss >/dev/null 2>&1; then ss -ltnp | grep -E ':{server_port}|:{server_app_port}' || true; fi; ",
            "if docker ps -q --filter name=fl-orchestrator >/dev/null 2>&1; then ",
            "docker exec fl-orchestrator python - <<'PY'\n",
            "import os, socket\n",
            "ports = [int(os.getenv('FLOWER_SERVER_PORT', '8080')), int(os.getenv('FLOWER_SERVERAPP_PORT', '9091'))]\n",
            "for port in ports:\n",
            "    try:\n",
            "        with socket.create_connection(('127.0.0.1', port), timeout=3) as sock:\n",
            "            print(f'[net] écoute interne OK sur {port} via {sock.family.name}')\n",
            "    except Exception as exc:  # pragma: no cover - diagnostic\n",
            "        print(f'[net] écoute interne KO sur {port}:', exc)\n",
            "PY\n",
            "fi",
        ]
    )

    info(logger, "[%s] diagnostic réseau orchestrateur", host)
    ssh(host, remote_cmd, logger, label="net-debug", mark_as_test=True)


def verify_certificates(host: str, base_dir: str, repo_name: str, logger: logging.Logger) -> None:
    """Inspecte en détail CA/serveur/client pour vérifier le SAN et la validité."""

    remote_cmd = (
        f"cd {quote_path(base_dir)}/{quote(repo_name)} && "
        "ca=$(pwd)/certs/ca.crt; "
        "server=$(pwd)/certs/orchestrator/server.crt; "
        "client=$(pwd)/certs/client/client.crt; "
        "envfile=$(pwd)/client/.env; "
        "set -a; [ -f $envfile ] && . $envfile; set +a; "
        "server_host=${SERVER_ADDRESS%%:*}; server_port=${SERVER_ADDRESS##*:}; "
        "echo '[cert] CA fingerprint:' && openssl x509 -noout -fingerprint -sha256 -in \"$ca\"; "
        "echo '[cert] serveur:' && openssl x509 -noout -subject -issuer -dates -in \"$server\"; "
        "echo '[cert] serveur SAN:' && openssl x509 -noout -ext subjectAltName -in \"$server\"; "
        "echo '[cert] client:' && openssl x509 -noout -subject -issuer -dates -in \"$client\"; "
        "echo '[cert] client SAN:' && openssl x509 -noout -ext subjectAltName -in \"$client\"; "
        "if command -v openssl >/dev/null 2>&1; then "
        "  echo '[cert] vérification SAN attendu:' $server_host; "
        "  openssl x509 -in \"$server\" -noout -ext subjectAltName | grep -E \"DNS:${server_host}|IP Address:${server_host}\" || true; "
        "fi; "
        "echo '[cert] connexion testée via openssl s_client:'; "
        "openssl s_client -connect \"$server_host:$server_port\" -CAfile \"$ca\" -servername \"$server_host\" -verify_return_error -brief </dev/null || true"
    )

    info(logger, "[%s] vérification détaillée des certificats/SAN", host)
    ssh(host, remote_cmd, logger, label="cert-audit", mark_as_test=True)


def hub_client_link_diagnostics(
    dgx_host: str, base_dir: str, repo_name: str, logger: logging.Logger
) -> None:
    """Diagnostics réseau détaillés entre le hub (orchestrateur) et le client."""

    remote_cmd = "".join(
        [
            f"cd {quote_path(base_dir)}/{quote(repo_name)} && ",
            "set -a; . client/.env; set +a; ",
            "server_host=${SERVER_ADDRESS%%:*}; server_port=${SERVER_ADDRESS##*:}; ",
            "ca_file=$(pwd)/certs/ca.crt; ",
            "pybin=$(command -v python3 || command -v python || echo python); ",
            "echo '[link] résumé réseau local' && hostname -f && date; ",
            "echo '[link] interfaces:' && (ip -4 addr show || true); ",
            "echo '[link] routes:' && (ip route show || true); ",
            "echo '[link] ports écoutés (top 20):' && (ss -ltnp 2>/dev/null | head -n 20 || true); ",
            "echo '[link] cible:' $server_host:$server_port; ",
            "$pybin - <<'PY'\n",
            "import os, socket, sys\n",
            "target = os.environ.get('SERVER_ADDRESS', '127.0.0.1:8080')\n",
            "host, port = target.rsplit(':', 1)\n",
            "ok = True\n",
            "try:\n",
            "    infos = socket.getaddrinfo(host, int(port))\n",
            "    unique = sorted({f\"{i[4][0]} ({i[0].name})\" for i in infos})\n",
            "    print('[link] résolutions DNS:', ', '.join(unique))\n",
            "except Exception as exc:  # pragma: no cover - diagnostic\n",
            "    print('[link] échec résolution DNS:', exc)\n",
            "    ok = False\n",
            "print(f'[link] tentative TCP vers {target}...')\n",
            "try:\n",
            "    with socket.create_connection((host, int(port)), timeout=5) as sock:\n",
            "        print('[link] TCP OK via', sock.family, 'proto', sock.proto)\n",
            "except Exception as exc:  # pragma: no cover - diagnostic\n",
            "    print('[link] TCP échec:', type(exc).__name__, exc)\n",
            "    ok = False\n",
            "sys.exit(0 if ok else 13)\n",
            "PY\n",
            "tcp_status=$?; ",
            "ping -c 2 -W 2 $server_host || true; ",
            "if command -v openssl >/dev/null 2>&1; then ",
            "  echo '[link] vérification TLS openssl'; ",
            "  openssl s_client -connect \"$server_host:$server_port\" -CAfile \"$ca_file\" ",
            "-servername \"$server_host\" -verify_return_error -brief </dev/null || true; ",
            "fi; ",
            "if [ \"$tcp_status\" -ne 0 ]; then exit $tcp_status; fi",
        ]
    )

    info(logger, "[%s] diagnostics réseau hub <> client", dgx_host)
    ssh(dgx_host, remote_cmd, logger, label="link-check", mark_as_test=True)


def grpc_smoke_test(host: str, base_dir: str, repo_name: str, logger: logging.Logger) -> None:
    """Test gRPC/mTLS en lançant un conteneur éphémère client.

    Le conteneur ``fl-client-dgx`` utilisé pour l'entraînement peut s'arrêter
    rapidement (1 round seulement). On démarre donc un conteneur dédié pour
    valider la connectivité gRPC/mTLS à l'aide du même fichier ``.env`` et des
    certificats déjà générés.
    """

    payload = r'''
import os, grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

addr = os.environ.get("SERVER_ADDRESS", "127.0.0.1:8080")
use_tls = os.environ.get("USE_TLS", "true").lower() in {"1", "true", "yes"}
ca = os.environ.get("CA_CERT_PATH", "/certs/ca.crt")
cert = os.environ.get("CLIENT_CERT_PATH")
key = os.environ.get("CLIENT_KEY_PATH")
print("[grpc] cible:", addr)
print("[grpc] TLS activé:", use_tls)
if use_tls:
    with open(ca, "rb") as f:
        ca_bytes = f.read()
    print("[grpc] CA chargée, taille:", len(ca_bytes))
    if cert and key:
        with open(cert, "rb") as fc, open(key, "rb") as fk:
            creds = grpc.ssl_channel_credentials(
                root_certificates=ca_bytes,
                certificate_chain=fc.read(),
                private_key=fk.read(),
            )
        print("[grpc] certificat client fourni")
    else:
        creds = grpc.ssl_channel_credentials(root_certificates=ca_bytes)
        print("[grpc] connexion TLS sans certificat client")
    channel = grpc.secure_channel(addr, creds)
else:
    channel = grpc.insecure_channel(addr)

grpc.channel_ready_future(channel).result(timeout=10)
state = channel._channel.check_connectivity_state(True)  # pragma: no cover - introspection
print("[grpc] channel ready ->", addr, "state:", state)
print("[grpc] cible effective:", channel._channel.target().decode())

stub = health_pb2_grpc.HealthStub(channel)
try:
    response = stub.Check(health_pb2.HealthCheckRequest(service=""), timeout=5)
    print("[grpc] health status:", health_pb2.HealthCheckResponse.ServingStatus.Name(response.status))
except Exception as exc:  # pragma: no cover - diagnostic
    print("[grpc] échec health-check:", type(exc).__name__, exc)
'''
    test_log(logger, "[%s] test gRPC/mTLS (hub <> client)", host)

    remote_cmd = (
        f"cd {quote_path(base_dir)}/{quote(repo_name)} && "
        "docker run --rm "
        "--env-file client/.env "
        "-e USE_TLS=${USE_TLS:-true} "
        "-e CA_CERT_PATH=${CA_CERT_PATH:-/certs/ca.crt} "
        "-e CLIENT_CERT_PATH=${CLIENT_CERT_PATH:-/certs/client.crt} "
        "-e CLIENT_KEY_PATH=${CLIENT_KEY_PATH:-/certs/client.key} "
        "-v $(pwd)/certs/client:/certs:ro "
        "fl-client-dgx:latest python - <<'PY'\n"
        f"{payload}\n"
        "PY"
    )
    ssh(host, remote_cmd, logger, label="grpc-test", mark_as_test=True)


def simulate_training_round(host: str, base_dir: str, repo_name: str, logger: logging.Logger) -> None:
    """Démarre un client éphémère qui entraîne/évalue et renvoie des poids.

    On s'appuie sur le serveur Flower déjà lancé dans le conteneur orchestrateur.
    Le client est volontairement minimal : il envoie des poids nuls, reçoit ceux
    du serveur, applique un fit (poids = 1), puis renvoie ces paramètres. Cela
    valide le chemin complet : connexion TLS/mTLS, fit, evaluate et retour des
    poids agrégés par l'orchestrateur.
    """

    payload = r'''
import os
import grpc
from pathlib import Path

import flwr as fl
import numpy as np

addr = os.environ["SERVER_ADDRESS"]
use_tls = os.environ.get("USE_TLS", "true").lower() in {"1", "true", "yes"}
ca = os.environ.get("CA_CERT_PATH")
cert = os.environ.get("CLIENT_CERT_PATH")
key = os.environ.get("CLIENT_KEY_PATH")


class EphemeralClient(fl.client.NumPyClient):
    def get_parameters(self, config):
        baseline = np.zeros((4,), dtype=np.float32)
        print("[round] paramètres initiaux:", baseline.tolist())
        return [baseline]

    def fit(self, parameters, config):
        # renvoie un vecteur de poids non nuls pour vérifier le round
        updated = [np.ones_like(parameters[0])]
        print("[round] fit -> somme envoyée:", float(updated[0].sum()))
        return updated, 1, {"sum": float(updated[0].sum())}

    def evaluate(self, parameters, config):
        # simple somme pour confirmer la réception
        metric = float(np.sum(parameters[0]))
        print("[round] evaluate -> somme reçue:", metric)
        return metric, 1, {"received_sum": metric}


client = EphemeralClient()
tls_kwargs = {}

if use_tls and ca:
    tls_kwargs["root_certificates"] = Path(ca).read_bytes()
    if cert and key:
        tls_kwargs["client_certificates"] = (
            Path(cert).read_bytes(),
            Path(key).read_bytes(),
        )

print("[round] destination:", addr)
print("[round] TLS activé:", bool(tls_kwargs))
if tls_kwargs:
    print("[round] certificats fournis:", "client_certificates" in tls_kwargs)

try:
    fl.client.start_client(server_address=addr, client=client.to_client(), **tls_kwargs)
except grpc.RpcError as exc:  # pragma: no cover - diagnostic
    print("[round] RPC échouée:", exc)
    if exc.code() == grpc.StatusCode.UNIMPLEMENTED:
        print(
            "[round] Méthode gRPC inconnue : vérifiez que SERVER_ADDRESS vise le port ServerApp exposé (ex: 9091 ou son mapping hôte)"
        )
    raise

print("[round] entraînement éphémère terminé")
'''

    remote_cmd = (
        f"cd {quote_path(base_dir)}/{quote(repo_name)} && "
        "docker run --rm "
        "--env-file client/.env "
        "-e USE_TLS=${USE_TLS:-true} "
        "-e CA_CERT_PATH=${CA_CERT_PATH:-/certs/ca.crt} "
        "-e CLIENT_CERT_PATH=${CLIENT_CERT_PATH:-/certs/client.crt} "
        "-e CLIENT_KEY_PATH=${CLIENT_KEY_PATH:-/certs/client.key} "
        "-v $(pwd)/certs/client:/certs:ro "
        "fl-client-dgx:latest python - <<'PY'\n"
        f"{payload}\n"
        "PY"
    )
    test_log(logger, "[%s] test d'entraînement éphémère", host)
    ssh(host, remote_cmd, logger, label="round-test", mark_as_test=True)


def tail_logs(host: str, container: str, logger: logging.Logger, lines: int = 20) -> None:
    if not container_exists(host, container, logger):
        info(logger, "[%s] conteneur %s introuvable (probablement terminé), logs sautés", host, container)
        return

    try:
        ssh(
            host,
            f"docker logs --tail {lines} {quote(container)}",
            logger,
            label=f"logs {container}",
            mark_as_test=True,
        )
    except subprocess.CalledProcessError:
        info(
            logger,
            "[%s] conteneur %s a disparu avant la lecture des logs, saut",  # pragma: no cover - dépend du timing Docker
            host,
            container,
        )


def stop_containers(hosts: Iterable[str], logger: logging.Logger) -> None:
    for host in hosts:
        ssh(
            host,
            "docker stop fl-orchestrator >/dev/null 2>&1 || true; "
            "docker stop fl-client-dgx >/dev/null 2>&1 || true; "
            "docker rm -f fl-orchestrator >/dev/null 2>&1 || true; "
            "docker rm -f fl-client-dgx >/dev/null 2>&1 || true",
            logger,
            label=f"stop {host}",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Déploiement/validation orchestrateur + client DGX depuis Windows")
    parser.add_argument("--proxy-host", default=DEFAULT_PROXY_HOST)
    parser.add_argument("--dgx-host", default=DEFAULT_DGX_HOST)
    parser.add_argument("--proxy-base", default=DEFAULT_PROXY_BASE)
    parser.add_argument("--dgx-base", default=DEFAULT_DGX_BASE)
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    parser.add_argument("--repo-name", default=DEFAULT_REPO_NAME)
    parser.add_argument(
        "--server-port",
        type=int,
        default=DEFAULT_SERVER_PORT,
        choices=[DEFAULT_SERVER_PORT],
        help="Port orchestrateur (8443 uniquement)",
    )
    parser.add_argument("--log-file", default="")
    parser.add_argument("--self-signed", action="store_true", help="Génère des certificats auto-signés et les synchronise")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_name = args.repo_name or repo_name_from_url(args.repo_url)

    # On conserve les alias fournis (PROXY/DGX) afin de bénéficier de la
    # configuration ``~/.ssh/config`` (utilisateur, clé, port). L'alias ne
    # doit pas être remplacé trop tôt par l'adresse IP sous peine de perdre
    # ces informations et de retomber sur l'utilisateur par défaut avec
    # Paramiko, ce qui aboutit à une invite de mot de passe.
    proxy_host = args.proxy_host
    dgx_host = args.dgx_host

    proxy_base = normalize_remote_path(args.proxy_base)
    dgx_base = normalize_remote_path(args.dgx_base)

    log_file = Path(args.log_file) if args.log_file else Path.cwd() / f"deploy_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
    logger = setup_logger(log_file)
    info(logger, "Journal détaillé : %s", log_file)

    if _get_password_from_env():
        _enable_paramiko(logger)
    elif os.name == "nt":
        # Sur Windows on privilégie Paramiko pour éviter les surprises avec ssh.exe
        _enable_paramiko(logger)

    try:
        # 0bis) Validation du port serveur (8443 imposé) + accessibilité
        server_host, server_port = select_server_endpoint(
            proxy_host, dgx_host, args.server_port, logger
        )

        # 0) Prérequis (Git + Docker)
        ensure_prerequisites(proxy_host, logger)
        ensure_prerequisites(dgx_host, logger)

        # 1) Connectivité SSH + Git clone/pull
        clone_or_pull(proxy_host, proxy_base, args.repo_url, repo_name, logger)
        clone_or_pull(dgx_host, dgx_base, args.repo_url, repo_name, logger)

        # 2) .env et valeurs de test minimalistes
        ensure_env(proxy_host, proxy_base, repo_name, "orchestrator/.env", "orchestrator/.env.example", logger)
        ensure_env(dgx_host, dgx_base, repo_name, "client/.env", "client/.env.example", logger)
        server_app_port = find_available_port(proxy_host, DEFAULT_SERVER_APP_PORT, logger)
        write_test_env(
            proxy_host,
            proxy_base,
            repo_name,
            "orchestrator",
            server_port,
            logger,
            server_app_port=server_app_port,
        )
        write_test_env(
            dgx_host,
            dgx_base,
            repo_name,
            "client",
            server_port,
            logger,
            server_app_port=server_app_port,
            server_host=server_host,
        )

        # 3) Build + run
        build_and_run(proxy_host, proxy_base, repo_name, "orchestrator", logger, args.self_signed)
        assert_container_running(proxy_host, "fl-orchestrator", logger)
        wait_for_proxy_port(dgx_host, server_host, server_port, logger)
        orchestrator_network_debug(proxy_host, server_port, server_app_port, logger)
        if args.self_signed:
            sync_self_signed_ca(
                proxy_host=proxy_host,
                proxy_base=proxy_base,
                dgx_host=dgx_host,
                dgx_base=dgx_base,
                repo_name=repo_name,
                logger=logger,
            )
        build_and_run(dgx_host, dgx_base, repo_name, "client", logger, args.self_signed)
        assert_container_running(dgx_host, "fl-client-dgx", logger)

        # 4) Tests
        check_docker(proxy_host, logger)
        check_docker(dgx_host, logger)
        check_containers(proxy_host, logger)
        check_containers(dgx_host, logger)
        verify_certificates(dgx_host, dgx_base, repo_name, logger)
        hub_client_link_diagnostics(dgx_host, dgx_base, repo_name, logger)
        grpc_smoke_test(dgx_host, dgx_base, repo_name, logger)
        simulate_training_round(dgx_host, dgx_base, repo_name, logger)
        tail_logs(proxy_host, "fl-orchestrator", logger)
        tail_logs(dgx_host, "fl-client-dgx", logger)

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
        "  Client DGX:    cd %s/%s && SERVER_ADDRESS=%s:%s ./run_docker_FL.sh client --self-signed --detach",
        proxy_base,
        repo_name,
        dgx_base,
        repo_name,
        server_host,
        server_port,
    )


if __name__ == "__main__":
    main()
