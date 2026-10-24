"""LLM provider abstraction and factory (Req 11).

This module defines the thin ``LLMProvider`` protocol that every pipeline node
(Extractor, Rights_Checker, Skeptic, Writer) calls through, plus a
``build_provider(config)`` factory that selects a concrete implementation from
the validated configuration. Once startup succeeds, every node LLM call routes
through the returned provider with no code change required to switch providers
(Req 11.4) -- a different ``LLM_PROVIDER`` value simply yields a different
concrete provider from the factory.

The ``node`` argument to :meth:`LLMProvider.complete` identifies the calling
pipeline node so the provider can resolve that node's effective model via the
config's per-node resolution (Req 11.5). Model resolution itself lives in
``config.py`` (task 2.1); this module only consumes it.

Security: the API key is held in memory for SDK/HTTP calls only. It is never
logged, printed, embedded in error messages, or otherwise emitted.

The configured provider in this project is ``anthropic``. No provider SDK (and
no third-party HTTP client such as ``httpx``) is installed in the backend
virtualenv, so the Anthropic implementation calls the Messages HTTP API using
the standard-library :mod:`urllib.request`, keeping the dependency surface
unchanged.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Config imports with a defensive fallback.
#
# config.py (task 2.1) owns ``LLMConfig``, the supported-providers set, the
# node-name identifier (``NodeName``), and per-node model resolution. Prefer
# importing those symbols. Task 2.1 is being implemented in parallel, so if the
# needed symbols are not yet present we fall back to minimal, structurally
# compatible stand-ins. These are intentionally minimal and MUST be replaced by
# the real symbols from config.py once it lands.
# ---------------------------------------------------------------------------
if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.config import LLMConfig, NodeName
else:
    try:  # Prefer the real config symbols when task 2.1 has landed.
        from backend.config import LLMConfig, NodeName  # type: ignore
    except Exception:  # pragma: no cover - fallback until config.py lands
        try:
            from config import LLMConfig, NodeName  # type: ignore
        except Exception:
            # TODO(task 2.1): remove these fallbacks once config.py exports
            # ``LLMConfig`` and ``NodeName``. They exist only so this module can
            # be imported and exercised before config.py is complete.
            NodeName = str  # type: ignore[assignment,misc]

            class LLMConfig:  # type: ignore[no-redef]
                """Minimal fallback mirroring the expected config.py surface.

                The real ``LLMConfig`` (task 2.1) carries the provider name, the
                API key, the shared model, and per-node overrides, and exposes a
                ``model_for(node)`` resolution. This fallback provides just
                enough shape for ``build_provider`` and the concrete providers
                to function and be import/signature-verified.
                """

                def __init__(
                    self,
                    *,
                    provider: str,
                    api_key: str,
                    model: str,
                    node_models: "dict[str, str] | None" = None,
                    base_url: "str | None" = None,
                ) -> None:
                    self.provider = provider
                    self.api_key = api_key
                    self.model = model
                    self.node_models = dict(node_models or {})
                    self.base_url = base_url

                def model_for(self, node: "NodeName") -> str:
                    """Resolve a node's effective model: override, else shared."""
                    return self.node_models.get(str(node), self.model)


__all__ = [
    "LLMProvider",
    "AnthropicProvider",
    "build_provider",
    "UnsupportedProviderError",
    "resolve_model",
]


def resolve_model(config: "LLMConfig", node: "NodeName") -> str:
    """Return the effective model for ``node`` from ``config`` (Req 11.5).

    Delegates to ``config.model_for(node)`` when available (the resolution
    contract defined by config.py in task 2.1), otherwise falls back to the
    shared ``model`` attribute. This keeps per-node resolution owned by
    config.py while letting providers ask for a node's model by name.
    """
    model_for = getattr(config, "model_for", None)
    if callable(model_for):
        return model_for(node)
    return getattr(config, "model")


@runtime_checkable
class LLMProvider(Protocol):
    """Thin abstraction over an LLM backend (Req 11.4).

    A provider turns a system prompt and a user prompt into completion text.
    The ``node`` argument lets the provider resolve that node's effective model
    (Req 11.5). ``timeout_s`` bounds the call so callers such as the Extractor
    (60s, Req 2.8) and Rights_Checker (15s, Req 5.1) can enforce their limits.
    """

    def complete(
        self,
        *,
        system: str,
        user: str,
        timeout_s: float,
        node: "NodeName",
    ) -> str:
        """Produce a completion for ``user`` under ``system`` for ``node``."""
        ...


class UnsupportedProviderError(ValueError):
    """Raised when the factory is asked for an unsupported provider.

    Config validation (task 2.1, Req 11.3) is the primary guard against
    unsupported providers at startup; this is a defensive backstop at the
    factory boundary.
    """


class AnthropicProvider:
    """``LLMProvider`` backed by the Anthropic Messages HTTP API.

    No Anthropic SDK or third-party HTTP client is installed in the backend
    virtualenv, so this calls the HTTP API directly with the standard-library
    :mod:`urllib.request`. The API key is stored only for request
    authentication and is never logged, printed, or placed in error messages.
    """

    DEFAULT_BASE_URL = "https://api.anthropic.com"
    API_VERSION = "2023-06-01"
    DEFAULT_MAX_TOKENS = 4096

    def __init__(self, config: "LLMConfig") -> None:
        self._config = config
        # Held in memory for request auth only; never emitted.
        self._api_key = getattr(config, "api_key")
        base = getattr(config, "base_url", None) or self.DEFAULT_BASE_URL
        self._base_url = base.rstrip("/")

    def complete(
        self,
        *,
        system: str,
        user: str,
        timeout_s: float,
        node: "NodeName",
    ) -> str:
        """Call the Messages API and return the concatenated text content.

        Resolves the effective model for ``node`` (Req 11.5), applies
        ``timeout_s`` to the HTTP request, and returns the completion text.
        """
        model = resolve_model(self._config, node)
        payload = {
            "model": model,
            "max_tokens": self.DEFAULT_MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/v1/messages",
            data=data,
            method="POST",
            headers={
                "content-type": "application/json",
                "anthropic-version": self.API_VERSION,
                "x-api-key": self._api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Surface status without echoing request headers (which carry the
            # API key). The key is never part of an HTTPError representation.
            raise RuntimeError(
                f"Anthropic request failed with HTTP {exc.code}"
            ) from None
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Anthropic request failed: {exc.reason}"
            ) from None

        return self._extract_text(body)

    @staticmethod
    def _extract_text(body: dict) -> str:
        """Concatenate the text blocks of a Messages API response."""
        blocks = body.get("content", []) or []
        parts = [
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)


# Registry mapping a supported provider name to its concrete constructor. Adding
# a new provider means adding an entry here -- node code never changes (Req 11.4).
_PROVIDER_REGISTRY: "dict[str, type]" = {
    "anthropic": AnthropicProvider,
}


def build_provider(config: "LLMConfig") -> "LLMProvider":
    """Return the concrete ``LLMProvider`` selected by ``config`` (Req 11.4).

    The provider name is matched case-insensitively against the registry. An
    unknown provider raises :class:`UnsupportedProviderError`; in normal
    operation config validation (task 2.1, Req 11.3) rejects unsupported
    providers at startup before this is reached.
    """
    provider_name = str(getattr(config, "provider", "")).strip().lower()
    try:
        provider_cls = _PROVIDER_REGISTRY[provider_name]
    except KeyError:
        supported = ", ".join(sorted(_PROVIDER_REGISTRY)) or "(none)"
        raise UnsupportedProviderError(
            f"unsupported LLM provider {provider_name!r}; "
            f"supported providers: {supported}"
        ) from None
    return provider_cls(config)
