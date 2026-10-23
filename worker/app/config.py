"""Settings and secrets for the worker.

Non-secret config (AWS endpoints, model names, pipeline knobs) lives in
settings.yaml at the repo root; the Docker build copies it into the image.

Secrets (OPENROUTER_API_KEY, SOURCES_API_TOKEN) come from environment variables,
which Lambda sets in production. For local runs only, they may live in .env.local at
the repo root. Inside Lambda the .env files are never read.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_SEARCH_DEPTH = 4  # app/ -> worker/ -> repo root, with a little slack


def running_in_lambda() -> bool:
    return bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def _find_upwards(names: list[str]) -> Path | None:
    """Look for the first of `names` from this file's folder upwards, then in the cwd."""
    current = Path(__file__).resolve().parent
    for _ in range(_SEARCH_DEPTH):
        for name in names:
            if (current / name).is_file():
                return current / name
        current = current.parent
    for name in names:
        if (Path.cwd() / name).is_file():
            return Path.cwd() / name
    return None


def find_settings_path() -> Path | None:
    env_path = os.getenv("SETTINGS_PATH")
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    return _find_upwards(["settings.yaml"])


def load_settings(settings_path: Path | str | None = None) -> dict[str, Any]:
    """Load settings.yaml as a dict, or {} if it is missing or unreadable."""
    target = Path(settings_path) if settings_path else find_settings_path()
    if not target or not target.is_file():
        logger.warning("settings.yaml not found; using built-in defaults")
        return {}
    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Failed to load settings from %s: %s", target, e)
        return {}
    return data if isinstance(data, dict) else {}


def setting(settings: dict[str, Any], path: str, default: Any) -> Any:
    """Read a dotted path such as "worker.top_accounts", falling back to `default`."""
    node: Any = settings
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return default if node is None else node


def find_env_file() -> Path | None:
    """The local secrets file. Never used inside Lambda."""
    if running_in_lambda():
        return None
    return _find_upwards([".env.local", ".env"])


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        logger.warning("Error reading %s: %s", path, e)
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.removeprefix("export ").split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        values[key.strip()] = val
    return values


def get_secret(name: str) -> str | None:
    """Return a secret from the environment, else (locally) from .env.local."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    env_file = find_env_file()
    if env_file:
        value = _read_env_file(env_file).get(name, "").strip()
        if value:
            return value
    return None
