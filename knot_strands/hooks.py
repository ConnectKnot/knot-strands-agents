"""Turn-0 context recovery HookProvider for AWS Strands Agents SDK."""

from __future__ import annotations

import logging
from typing import Any

from .client import KnotClient, KnotClientError

try:
    from strands.hooks import AgentInitializedEvent, BeforeInvocationEvent
    _STRANDS_HOOKS_AVAILABLE = True
except ImportError:
    AgentInitializedEvent = None  # type: ignore[assignment,misc]
    BeforeInvocationEvent = None  # type: ignore[assignment,misc]
    _STRANDS_HOOKS_AVAILABLE = False

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_HEADER = "[KNOT GOVERNED ACTIVE CONTEXT]"
DEFAULT_CONTEXT_FOOTER = "[/KNOT GOVERNED ACTIVE CONTEXT]"


class KnotStrandsHookProvider:
    """Strands HookProvider that injects governed active Knot truth on Turn 0.

    Works both when ``strands-agents`` is installed (satisfying ``strands.hooks.HookProvider``)
    and in standalone zero-dependency environments. Degrades gracefully to a no-op when
    active context is empty or EnterpriseBrain is unreachable.
    """

    def __init__(
        self,
        client: KnotClient | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        user_id: str | None = None,
        domain: str | None = None,
        query: str = "",
        limit: int = 10,
        header: str = DEFAULT_CONTEXT_HEADER,
        footer: str = DEFAULT_CONTEXT_FOOTER,
        inject_on_init: bool = False,
    ) -> None:
        self._client = client
        self._client_init_kwargs = {
            "api_key": api_key,
            "base_url": base_url,
            "domain": domain,
            "default_user_id": user_id,
        }
        self.user_id = user_id
        self.domain = domain
        self.query = query
        self.limit = int(limit)
        self.header = header
        self.footer = footer
        self.inject_on_init = inject_on_init

    def _get_client(self) -> KnotClient:
        if self._client is None:
            self._client = KnotClient(**self._client_init_kwargs)
        return self._client

    def fetch_recovery_items(self) -> list[dict[str, Any]]:
        """Retrieve active governing context items, returning [] if empty or offline."""
        try:
            client = self._get_client()
            resp = client.retrieve(
                query=self.query,
                user_id=self.user_id,
                domain=self.domain,
                limit=self.limit,
            )
        except (KnotClientError, ValueError, OSError) as exc:
            logger.debug("Knot Turn-0 recovery degraded gracefully: %s", exc)
            return []
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.warning("Unexpected error during Knot Turn-0 recovery: %s", exc)
            return []

        raw_items = resp.get("items")
        if not isinstance(raw_items, list):
            return []
        return [item for item in raw_items if isinstance(item, dict)]

    def format_recovery_block(self, items: list[dict[str, Any]] | None = None) -> str:
        """Format active Knot items into a Turn-0 prompt block, or '' when empty/offline."""
        resolved_items = self.fetch_recovery_items() if items is None else items
        if not resolved_items:
            return ""

        lines = [self.header]
        for item in resolved_items:
            category = str(item.get("category") or "fact")
            entity_key = str(item.get("entityKey") or item.get("entity_key") or "context")
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            version = item.get("version")
            item_id = item.get("id")
            meta_parts: list[str] = []
            if version is not None:
                meta_parts.append(f"v{version}")
            if item_id:
                meta_parts.append(f"id: {item_id}")
            suffix = f" ({', '.join(meta_parts)})" if meta_parts else ""
            lines.append(f"- [{category} | {entity_key}] {content}{suffix}")

        if len(lines) == 1:
            return ""
        lines.append(self.footer)
        return "\n".join(lines)

    def build_turn0_context(self) -> str:
        """Alias for ``format_recovery_block()``."""
        return self.format_recovery_block()

    def inject_into_agent(self, agent: Any) -> bool:
        """Inject the Turn-0 recovery block into ``agent.system_prompt`` once."""
        if agent is None or not hasattr(agent, "system_prompt"):
            return False

        existing = getattr(agent, "system_prompt", None) or ""
        if isinstance(existing, str) and self.header in existing:
            return False

        block = self.format_recovery_block()
        if not block:
            return False

        updated = f"{existing}\n\n{block}".strip() if existing else block
        try:
            agent.system_prompt = updated
        except AttributeError:
            return False
        return True

    def on_before_invocation(self, event: Any) -> None:
        """Strands BeforeInvocationEvent callback injecting Turn-0 recovery context."""
        agent = getattr(event, "agent", None)
        self.inject_into_agent(agent)

    def on_agent_initialized(self, event: Any) -> None:
        """Strands AgentInitializedEvent callback for optional eager Turn-0 hydration."""
        agent = getattr(event, "agent", None)
        self.inject_into_agent(agent)

    def register_hooks(self, registry: Any, **kwargs: Any) -> None:
        """Register Turn-0 context recovery callbacks on a Strands HookRegistry."""
        if _STRANDS_HOOKS_AVAILABLE and BeforeInvocationEvent is not None:
            registry.add_callback(BeforeInvocationEvent, self.on_before_invocation)
            if self.inject_on_init and AgentInitializedEvent is not None:
                registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
            return

        if hasattr(registry, "add_callback"):
            registry.add_callback("before_invocation", self.on_before_invocation)
            if self.inject_on_init:
                registry.add_callback("agent_initialized", self.on_agent_initialized)
