"""Environment-based configuration and LLM provider config validation (Req 11).

This module loads the LLM provider configuration from the ``backend/.env`` file
at startup and validates it:

* Required variables ``LLM_PROVIDER``, ``LLM_API_KEY`` and ``LLM_MODEL`` must be
  present and non-empty; any missing/empty value aborts startup naming *each*
  offending environment variable (Req 11.2).
* The provider value must be one of the supported providers; otherwise startup
  aborts naming the bad value and listing the supported providers (Req 11.3).
* Per-node model overrides (e.g. ``LLM_MODEL_EXTRACTOR``) apply to that node;
  otherwise the shared ``LLM_MODEL`` applies (Req 11.5).

Security note: the API key is referenced only by its variable name. Its *value*
is never logged, printed, or included in any error message or ``repr``.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

# Location of the .env file that holds the provider configuration. The file
# lives alongside this module in the ``backend/`` directory.
ENV_PATH = Path(__file__).resolve().parent / ".env"


class NodeName(str, Enum):
    """Shared identifiers for the five pipeline nodes.

    Defined here so ``llm/provider.py`` (task 2.2) can import a single canonical
    set of node names for ``LLMProvider.complete(..., node=...)`` and per-node
    model resolution. Values are lowercase string literals so they can be used
    interchangeably with plain strings and in environment-variable suffixes.
    """

    EXTRACTOR = "extractor"
    AUDITOR = "auditor"
    RIGHTS_CHECKER = "rights_checker"
    SKEPTIC = "skeptic"
    WRITER = "writer"


# The nodes that issue LLM calls (Req 11.4). The Auditor is a pure deterministic
# function and never calls the provider, so it carries no model override, but it
# remains part of ``NodeName`` because the pipeline is a five-node concept.
LLM_NODES: tuple[NodeName, ...] = (
    NodeName.EXTRACTOR,
    NodeName.RIGHTS_CHECKER,
    NodeName.SKEPTIC,
    NodeName.WRITER,
)

# Providers the backend knows how to route to (Req 11.3). The value configured
# in the committed ``.env`` ("anthropic") is included at minimum.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("anthropic",)

# Required environment variables (Req 11.2).
REQUIRED_VARS: tuple[str, ...] = ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL")

# Environment-variable suffixes used for per-node model overrides (Req 11.5),
# e.g. ``LLM_MODEL_EXTRACTOR``. Keyed by node.
_NODE_MODEL_ENV = {node: f"LLM_MODEL_{node.name}" for node in LLM_NODES}


class ConfigError(RuntimeError):
    """Raised when the LLM provider configuration is missing or invalid.

    Carrying a dedicated exception type lets the startup path abort cleanly
    (Req 11.2, 11.3) while keeping the triggering values out of generic logging.
    """


class LLMConfig(BaseModel):
    """Validated LLM provider configuration.

    Holds the provider, API key, the shared model, and the resolved per-node
    model map. ``repr`` is customized so the API key is never rendered.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    # Excluded from repr so the secret value cannot leak via logging/str().
    api_key: str = Field(repr=False)
    model: str  # shared model (LLM_MODEL)
    # Effective model per LLM node (override when present, else shared).
    node_models: dict[NodeName, str]

    def model_for(self, node: NodeName) -> str:
        """Return the effective model for ``node`` (Req 11.5).

        Falls back to the shared model for nodes without an LLM call or override.
        """
        return self.node_models.get(node, self.model)

    def __repr__(self) -> str:  # pragma: no cover - trivial, but guards the key
        return (
            f"LLMConfig(provider={self.provider!r}, model={self.model!r}, "
            f"api_key=<redacted>, node_models={self.node_models!r})"
        )

    __str__ = __repr__


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal ``.env`` file into a dict.

    Supports ``KEY=VALUE`` lines with surrounding whitespace, ``#`` comments,
    blank lines, optional ``export`` prefixes, and single/double quoted values.
    Returns an empty dict when the file does not exist so that validation can
    report missing variables uniformly (Req 11.2).
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Strip a single matching pair of surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _resolve_source(env: Optional[Mapping[str, str]]) -> dict[str, str]:
    """Build the lookup used for validation.

    When ``env`` is provided it is used verbatim (used for tests so the real
    ``.env`` is never mutated). Otherwise the ``.env`` file is parsed and then
    overlaid with the live process environment, so ``os.environ`` wins over the
    file for the same key.
    """
    if env is not None:
        return dict(env)
    merged = parse_env_file(ENV_PATH)
    for key in (*REQUIRED_VARS, *_NODE_MODEL_ENV.values()):
        if key in os.environ:
            merged[key] = os.environ[key]
    return merged


def get_setting(name: str, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Return a non-secret deployment setting, or ``None`` when unset/blank.

    Resolution matches :func:`load_config`: an explicit ``env`` mapping is used
    verbatim; otherwise ``os.environ`` wins over ``backend/.env``. Used for
    settings such as ``ALLOWED_ORIGINS`` and ``MAX_DAILY_ANALYSES``.
    """
    if env is not None:
        value = env.get(name)
    elif name in os.environ:
        value = os.environ[name]
    else:
        value = parse_env_file(ENV_PATH).get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


# Default CORS origin: the local Next.js dev server.
DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = ("http://localhost:3000",)


def load_allowed_origins(env: Optional[Mapping[str, str]] = None) -> list[str]:
    """Parse ``ALLOWED_ORIGINS`` (comma-separated) into a CORS origin list.

    Blank entries and trailing slashes are dropped (browsers send origins with
    no trailing slash). Falls back to ``http://localhost:3000`` when unset.
    """
    raw = get_setting("ALLOWED_ORIGINS", env)
    if raw is None:
        return list(DEFAULT_ALLOWED_ORIGINS)
    origins = [part.strip().rstrip("/") for part in raw.split(",")]
    origins = [o for o in origins if o]
    return origins or list(DEFAULT_ALLOWED_ORIGINS)


# Default global cap on bill analyses per UTC day (cost protection).
DEFAULT_MAX_DAILY_ANALYSES = 50


def load_max_daily_analyses(env: Optional[Mapping[str, str]] = None) -> int:
    """Return ``MAX_DAILY_ANALYSES`` as a non-negative int (default 50).

    Raises:
        ConfigError: when the value is not a whole number >= 0. The message
            names the variable only.
    """
    raw = get_setting("MAX_DAILY_ANALYSES", env)
    if raw is None:
        return DEFAULT_MAX_DAILY_ANALYSES
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        raise ConfigError(
            "Invalid MAX_DAILY_ANALYSES value. Use a whole number of 0 or more."
        )
    return value


def load_config(env: Optional[Mapping[str, str]] = None) -> LLMConfig:
    """Load and validate the LLM provider configuration (Req 11.1, 11.2, 11.3, 11.5).

    Args:
        env: Optional explicit mapping of configuration values. When ``None``
            (the default) the ``backend/.env`` file is read and overlaid with
            ``os.environ``. Passing an explicit mapping lets callers validate
            configuration without touching the real ``.env`` file.

    Returns:
        A validated, immutable :class:`LLMConfig`.

    Raises:
        ConfigError: if any required variable is missing/empty (naming each one)
            or the provider is unsupported (naming the value and the supported
            providers). Error messages never contain any variable's value.
    """
    source = _resolve_source(env)

    # Req 11.2: collect EVERY missing/empty required variable and name them all.
    missing = [
        name
        for name in REQUIRED_VARS
        if not source.get(name, "").strip()
    ]
    if missing:
        raise ConfigError(
            "Missing or empty required LLM configuration variable(s): "
            + ", ".join(missing)
            + ". Set them in backend/.env before starting the backend."
        )

    provider = source["LLM_PROVIDER"].strip()
    # Req 11.3: unsupported provider names the bad value and lists supported ones.
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigError(
            f"Unsupported LLM_PROVIDER value {provider!r}. "
            "Supported providers: " + ", ".join(SUPPORTED_PROVIDERS) + "."
        )

    shared_model = source["LLM_MODEL"].strip()

    # Req 11.5: per-node override when present and non-empty, else shared model.
    node_models: dict[NodeName, str] = {}
    for node, env_name in _NODE_MODEL_ENV.items():
        override = source.get(env_name, "").strip()
        node_models[node] = override if override else shared_model

    return LLMConfig(
        provider=provider,
        api_key=source["LLM_API_KEY"],
        model=shared_model,
        node_models=node_models,
    )
