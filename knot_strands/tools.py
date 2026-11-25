"""Strands-compatible memory, decision, and active-state tools backed by Knot."""

import json
import threading
from typing import Any, Callable, Dict, List, Optional, Union

from .client import KnotClient, KnotClientError

try:
    from strands.tools.decorator import tool as _strands_tool
    _STRANDS_AVAILABLE = True
except ImportError:
    _strands_tool = None
    _STRANDS_AVAILABLE = False

_DEFAULT_CLIENT_LOCK = threading.Lock()
_DEFAULT_CLIENT: Optional[KnotClient] = None


def is_strands_available() -> bool:
    """Return True when the optional strands-agents SDK is installed."""
    return _STRANDS_AVAILABLE


def set_default_client(client: Optional[KnotClient]) -> None:
    """Configure the process-wide default KnotClient used by standalone tools."""
    global _DEFAULT_CLIENT
    with _DEFAULT_CLIENT_LOCK:
        _DEFAULT_CLIENT = client


def get_default_client() -> KnotClient:
    """Return the configured default KnotClient or construct one from environment variables."""
    global _DEFAULT_CLIENT
    with _DEFAULT_CLIENT_LOCK:
        if _DEFAULT_CLIENT is None:
            _DEFAULT_CLIENT = KnotClient()
        return _DEFAULT_CLIENT


def _resolve_client(client: Any) -> KnotClient:
    if isinstance(client, KnotClient):
        return client
    return get_default_client()


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=False)


KNOT_MEMORY_TOOL_SPEC: Dict[str, Any] = {
    "name": "knot_memory",
    "description": (
        "Governed temporal memory tool backed by Knot EnterpriseBrain with deterministic "
        "v1 -> v2 supersession, point-in-time as_of replay, lineage audit history, and "
        "sub-100ms subject cryptographic erasure.\n\n"
        "Actions:\n"
        "- store: Commit a new memory or supersede prior versions via supersedes=[v1_id].\n"
        "- retrieve: Search active governing v2 context (or historical state when as_of is set).\n"
        "- list: List active governed memories for a user_id and domain.\n"
        "- history: Query /v1/subjects/{user_id}/lineage for v1 -> v2 provenance.\n"
        "- erase: Call DELETE /v1/subjects/{user_id}/key for <100ms crypto-erasure."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Operation to perform: store, retrieve, list, history, or erase.",
                    "enum": ["store", "retrieve", "list", "history", "erase"],
                },
                "content": {
                    "type": "string",
                    "description": "Memory text to store (required when action='store').",
                },
                "query": {
                    "type": "string",
                    "description": "Search query for retrieve or list actions.",
                },
                "user_id": {
                    "type": "string",
                    "description": "Subject or user identifier for tenant isolation.",
                },
                "entity_key": {
                    "type": "string",
                    "description": "Stable canonical entity key shared across v1 -> v2 revisions.",
                },
                "domain": {
                    "type": "string",
                    "description": "Governance domain sent in X-Knot-Domain (e.g. billing, architecture).",
                },
                "as_of": {
                    "type": "string",
                    "description": "ISO-8601 timestamp for deterministic point-in-time replay.",
                },
                "supersedes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Prior context IDs superseded by this store operation (e.g. [v1_id]).",
                },
                "category": {
                    "type": "string",
                    "description": "UCS kind: fact, decision, constraint, goal, project_state, or preference.",
                },
                "observed_at": {
                    "type": "string",
                    "description": "Optional ISO-8601 observation timestamp.",
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional structured provenance metadata.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of items to return for retrieve/list (default 20).",
                },
            },
            "required": ["action"],
        }
    },
}

KNOT_COMMIT_DECISION_TOOL_SPEC: Dict[str, Any] = {
    "name": "knot_commit_decision",
    "description": (
        "Commit an architectural, policy, or operational decision to Knot EnterpriseBrain "
        "with explicit entity_key, optional rationale, and v1 -> v2 supersession IDs."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "description": "Clear decision statement to commit as active truth.",
                },
                "entity_key": {
                    "type": "string",
                    "description": "Canonical entity key for this decision (e.g. 'vector_store').",
                },
                "user_id": {
                    "type": "string",
                    "description": "Subject or team identifier owning this decision.",
                },
                "domain": {
                    "type": "string",
                    "description": "Governance domain sent in X-Knot-Domain.",
                },
                "supersedes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Prior decision IDs replaced by this version.",
                },
                "rationale": {
                    "type": "string",
                    "description": "Optional explanation recorded in decision provenance.",
                },
                "category": {
                    "type": "string",
                    "description": "UCS category (defaults to 'decision').",
                },
                "observed_at": {
                    "type": "string",
                    "description": "Optional ISO-8601 observation timestamp.",
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional structured metadata.",
                },
            },
            "required": ["decision", "entity_key"],
        }
    },
}

KNOT_GET_ACTIVE_STATE_TOOL_SPEC: Dict[str, Any] = {
    "name": "knot_get_active_state",
    "description": (
        "Fetch the compiled active governing state (v2) from Knot EnterpriseBrain in <15ms "
        "with zero read-time LLM calls."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "Subject or team identifier to inspect.",
                },
                "domain": {
                    "type": "string",
                    "description": "Governance domain scope.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional filter query.",
                },
                "entity_key": {
                    "type": "string",
                    "description": "Optional canonical entity key filter.",
                },
                "as_of": {
                    "type": "string",
                    "description": "Optional ISO-8601 timestamp for historical state replay.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum active items to return (default 20).",
                },
            },
            "required": [],
        }
    },
}


def _wrap_strands_tool(spec: Dict[str, Any]) -> Callable[[Callable[..., str]], Any]:
    def decorator(fn: Callable[..., str]) -> Any:
        if _STRANDS_AVAILABLE and _strands_tool is not None:
            wrapped = _strands_tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["inputSchema"],
            )(fn)
            wrapped.TOOL_SPEC = spec
            return wrapped

        fn.TOOL_SPEC = spec  # type: ignore[attr-defined]
        fn.tool_spec = spec  # type: ignore[attr-defined]
        fn.tool_name = spec["name"]  # type: ignore[attr-defined]
        fn._tool_func = fn  # type: ignore[attr-defined]
        return fn

    return decorator


def _execute_knot_memory(
    *,
    action: str,
    content: Optional[str] = None,
    query: Optional[str] = None,
    user_id: Optional[str] = None,
    entity_key: Optional[str] = None,
    domain: Optional[str] = None,
    as_of: Optional[str] = None,
    supersedes: Optional[Union[List[str], str]] = None,
    category: Optional[str] = None,
    observed_at: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    limit: int = 20,
    client: Any = None,
) -> str:
    act = (action or "").strip().lower()
    if act not in {"store", "retrieve", "list", "history", "erase"}:
        return _json_dumps(
            {
                "ok": False,
                "action": act or action,
                "error": f"Unsupported action '{action}'. Expected store, retrieve, list, history, or erase.",
            }
        )

    try:
        resolved_client = _resolve_client(client)

        if act == "store":
            if not isinstance(content, str) or not content.strip():
                return _json_dumps(
                    {
                        "ok": False,
                        "action": "store",
                        "error": "content is required for store action.",
                    }
                )
            resp = resolved_client.upsert(
                content=content,
                user_id=user_id,
                entity_key=entity_key,
                category=category or "fact",
                domain=domain,
                observed_at=observed_at,
                supersedes=supersedes,
                metadata=metadata,
            )
            if resp.get("rejected") or resp.get("ok") is False:
                return _json_dumps({"action": "store", **resp})
            return _json_dumps({"ok": True, "action": "store", **resp})

        if act in {"retrieve", "list"}:
            effective_query = (query or "") if act == "retrieve" else (query or "")
            resp = resolved_client.retrieve(
                query=effective_query,
                user_id=user_id,
                domain=domain,
                entity_key=entity_key,
                category=category,
                as_of=as_of,
                limit=limit,
            )
            items = resp.get("items") if isinstance(resp.get("items"), list) else []
            return _json_dumps(
                {
                    "ok": True,
                    "action": act,
                    "as_of": as_of,
                    "count": len(items),
                    **resp,
                }
            )

        if act == "history":
            resp = resolved_client.lineage(
                user_id=user_id,
                entity_key=entity_key,
                domain=domain,
            )
            return _json_dumps({"ok": True, "action": "history", **resp})

        # act == "erase"
        resp = resolved_client.erase_subject(user_id=user_id, domain=domain)
        return _json_dumps({"ok": True, "action": "erase", **resp})

    except (KnotClientError, ValueError) as exc:
        return _json_dumps(
            {
                "ok": False,
                "action": act,
                "error": str(exc),
                "status": getattr(exc, "status", None),
                "code": getattr(exc, "code", None),
            }
        )


def _execute_commit_decision(
    *,
    decision: str,
    entity_key: str,
    user_id: Optional[str] = None,
    domain: Optional[str] = None,
    supersedes: Optional[Union[List[str], str]] = None,
    rationale: Optional[str] = None,
    category: str = "decision",
    observed_at: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    client: Any = None,
) -> str:
    if not isinstance(decision, str) or not decision.strip():
        return _json_dumps(
            {
                "ok": False,
                "action": "commit_decision",
                "error": "decision must be a non-empty string.",
            }
        )
    if not isinstance(entity_key, str) or not entity_key.strip():
        return _json_dumps(
            {
                "ok": False,
                "action": "commit_decision",
                "error": "entity_key must be a non-empty string.",
            }
        )

    try:
        resolved_client = _resolve_client(client)
        provenance: Dict[str, Any] = {"source": "knot_commit_decision"}
        if rationale:
            provenance["rationale"] = rationale.strip()
        resp = resolved_client.upsert(
            content=decision,
            user_id=user_id,
            entity_key=entity_key,
            category=category or "decision",
            domain=domain,
            observed_at=observed_at,
            supersedes=supersedes,
            provenance=provenance,
            metadata=metadata,
        )
        if resp.get("rejected") or resp.get("ok") is False:
            return _json_dumps({"action": "commit_decision", **resp})
        return _json_dumps({"ok": True, "action": "commit_decision", **resp})
    except (KnotClientError, ValueError) as exc:
        return _json_dumps(
            {
                "ok": False,
                "action": "commit_decision",
                "error": str(exc),
                "status": getattr(exc, "status", None),
                "code": getattr(exc, "code", None),
            }
        )


def _execute_get_active_state(
    *,
    user_id: Optional[str] = None,
    domain: Optional[str] = None,
    query: Optional[str] = None,
    entity_key: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: int = 20,
    client: Any = None,
) -> str:
    try:
        resolved_client = _resolve_client(client)
        resp = resolved_client.retrieve(
            query=query or "",
            user_id=user_id,
            domain=domain,
            entity_key=entity_key,
            as_of=as_of,
            limit=limit,
        )
        items = resp.get("items") if isinstance(resp.get("items"), list) else []
        return _json_dumps(
            {
                "ok": True,
                "action": "get_active_state",
                "user_id": resp.get("subjectId") or user_id or resolved_client.default_user_id,
                "domain": domain or resolved_client.domain,
                "as_of": as_of,
                "count": len(items),
                "active_state": items,
                "latency_ms": resp.get("latencyMs"),
            }
        )
    except (KnotClientError, ValueError) as exc:
        return _json_dumps(
            {
                "ok": False,
                "action": "get_active_state",
                "error": str(exc),
                "status": getattr(exc, "status", None),
                "code": getattr(exc, "code", None),
            }
        )


@_wrap_strands_tool(KNOT_MEMORY_TOOL_SPEC)
def knot_memory(
    action: str,
    content: Optional[str] = None,
    query: Optional[str] = None,
    user_id: Optional[str] = None,
    entity_key: Optional[str] = None,
    domain: Optional[str] = None,
    as_of: Optional[str] = None,
    supersedes: Optional[Union[List[str], str]] = None,
    category: Optional[str] = None,
    observed_at: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    limit: int = 20,
    client: Any = None,
) -> str:
    """Governed v1 -> v2 temporal memory tool with Strands mem0_memory signature parity.

    Args:
        action: Operation to perform (store, retrieve, list, history, erase).
        content: Memory text to store when action is store.
        query: Search query for retrieve or list actions.
        user_id: Subject or user identifier for tenant isolation.
        entity_key: Stable canonical entity key shared across v1 -> v2 revisions.
        domain: Governance domain sent in X-Knot-Domain.
        as_of: ISO-8601 timestamp for deterministic point-in-time replay.
        supersedes: Prior context IDs superseded by this store operation.
        category: UCS kind (fact, decision, constraint, goal, project_state, preference).
        observed_at: Optional ISO-8601 observation timestamp.
        metadata: Optional structured provenance metadata.
        limit: Maximum number of items to return.
        client: Optional KnotClient instance override.
    """
    return _execute_knot_memory(
        action=action,
        content=content,
        query=query,
        user_id=user_id,
        entity_key=entity_key,
        domain=domain,
        as_of=as_of,
        supersedes=supersedes,
        category=category,
        observed_at=observed_at,
        metadata=metadata,
        limit=limit,
        client=client,
    )


@_wrap_strands_tool(KNOT_COMMIT_DECISION_TOOL_SPEC)
def knot_commit_decision(
    decision: str,
    entity_key: str,
    user_id: Optional[str] = None,
    domain: Optional[str] = None,
    supersedes: Optional[Union[List[str], str]] = None,
    rationale: Optional[str] = None,
    category: str = "decision",
    observed_at: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    client: Any = None,
) -> str:
    """Commit a durable architectural or policy decision to Knot EnterpriseBrain.

    Args:
        decision: Clear decision statement to commit as active truth.
        entity_key: Canonical entity key for this decision.
        user_id: Subject or team identifier owning this decision.
        domain: Governance domain sent in X-Knot-Domain.
        supersedes: Prior decision IDs replaced by this version.
        rationale: Optional explanation recorded in decision provenance.
        category: UCS category (defaults to decision).
        observed_at: Optional ISO-8601 observation timestamp.
        metadata: Optional structured metadata.
        client: Optional KnotClient instance override.
    """
    return _execute_commit_decision(
        decision=decision,
        entity_key=entity_key,
        user_id=user_id,
        domain=domain,
        supersedes=supersedes,
        rationale=rationale,
        category=category,
        observed_at=observed_at,
        metadata=metadata,
        client=client,
    )


@_wrap_strands_tool(KNOT_GET_ACTIVE_STATE_TOOL_SPEC)
def knot_get_active_state(
    user_id: Optional[str] = None,
    domain: Optional[str] = None,
    query: Optional[str] = None,
    entity_key: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: int = 20,
    client: Any = None,
) -> str:
    """Fetch compiled active governing state (v2) from Knot EnterpriseBrain.

    Args:
        user_id: Subject or team identifier to inspect.
        domain: Governance domain scope.
        query: Optional filter query.
        entity_key: Optional canonical entity key filter.
        as_of: Optional ISO-8601 timestamp for historical state replay.
        limit: Maximum active items to return.
        client: Optional KnotClient instance override.
    """
    return _execute_get_active_state(
        user_id=user_id,
        domain=domain,
        query=query,
        entity_key=entity_key,
        as_of=as_of,
        limit=limit,
        client=client,
    )


def _tenant_pinned_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of a ToolSpec with user_id and domain omitted for bound toolsets."""
    json_schema = spec["inputSchema"]["json"]
    pinned_props = {
        k: v for k, v in json_schema["properties"].items() if k not in {"user_id", "domain"}
    }
    pinned_required = [r for r in json_schema.get("required", []) if r not in {"user_id", "domain"}]
    return {
        "name": spec["name"],
        "description": (
            f"{spec['description']}\n\nNote: user_id and domain are pinned by the operator."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": pinned_props,
                "required": pinned_required,
            }
        },
    }


class KnotToolset:
    """Multi-tenant operator factory binding KnotClient, user_id, and domain to tools."""

    def __init__(
        self,
        client: Optional[KnotClient] = None,
        *,
        user_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> None:
        self.client = client or KnotClient(default_user_id=user_id, domain=domain)
        self.user_id = user_id or self.client.default_user_id
        self.domain = domain or self.client.domain

        bound_client = self.client
        bound_user = self.user_id
        bound_domain = self.domain

        @_wrap_strands_tool(_tenant_pinned_spec(KNOT_MEMORY_TOOL_SPEC))
        def bound_knot_memory(
            action: str,
            content: Optional[str] = None,
            query: Optional[str] = None,
            entity_key: Optional[str] = None,
            as_of: Optional[str] = None,
            supersedes: Optional[Union[List[str], str]] = None,
            category: Optional[str] = None,
            observed_at: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
            limit: int = 20,
        ) -> str:
            """Tenant-pinned governed v1 -> v2 temporal memory tool."""
            return _execute_knot_memory(
                action=action,
                content=content,
                query=query,
                user_id=bound_user,
                entity_key=entity_key,
                domain=bound_domain,
                as_of=as_of,
                supersedes=supersedes,
                category=category,
                observed_at=observed_at,
                metadata=metadata,
                limit=limit,
                client=bound_client,
            )

        @_wrap_strands_tool(_tenant_pinned_spec(KNOT_COMMIT_DECISION_TOOL_SPEC))
        def bound_commit_decision(
            decision: str,
            entity_key: str,
            supersedes: Optional[Union[List[str], str]] = None,
            rationale: Optional[str] = None,
            category: str = "decision",
            observed_at: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None,
        ) -> str:
            """Tenant-pinned decision commit tool."""
            return _execute_commit_decision(
                decision=decision,
                entity_key=entity_key,
                user_id=bound_user,
                domain=bound_domain,
                supersedes=supersedes,
                rationale=rationale,
                category=category,
                observed_at=observed_at,
                metadata=metadata,
                client=bound_client,
            )

        @_wrap_strands_tool(_tenant_pinned_spec(KNOT_GET_ACTIVE_STATE_TOOL_SPEC))
        def bound_get_active_state(
            query: Optional[str] = None,
            entity_key: Optional[str] = None,
            as_of: Optional[str] = None,
            limit: int = 20,
        ) -> str:
            """Tenant-pinned active state read tool."""
            return _execute_get_active_state(
                user_id=bound_user,
                domain=bound_domain,
                query=query,
                entity_key=entity_key,
                as_of=as_of,
                limit=limit,
                client=bound_client,
            )

        self.knot_memory = bound_knot_memory
        self.knot_commit_decision = bound_commit_decision
        self.knot_get_active_state = bound_get_active_state
        self.tools = [
            self.knot_memory,
            self.knot_commit_decision,
            self.knot_get_active_state,
        ]
