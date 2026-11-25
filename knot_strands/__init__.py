"""Governed v1 -> v2 temporal memory and context toolset for AWS Strands Agents SDK."""

from .client import (
    DEFAULT_KNOT_BASE_URL,
    KnotAPIError,
    KnotClient,
    KnotClientError,
    KnotPolicyRejectedError,
)
from .hooks import KnotStrandsHookProvider
from .tools import (
    KNOT_COMMIT_DECISION_TOOL_SPEC,
    KNOT_GET_ACTIVE_STATE_TOOL_SPEC,
    KNOT_MEMORY_TOOL_SPEC,
    KnotToolset,
    get_default_client,
    is_strands_available,
    knot_commit_decision,
    knot_get_active_state,
    knot_memory,
    set_default_client,
)

__version__ = "1.0.0"

__all__ = [
    "DEFAULT_KNOT_BASE_URL",
    "KNOT_COMMIT_DECISION_TOOL_SPEC",
    "KNOT_GET_ACTIVE_STATE_TOOL_SPEC",
    "KNOT_MEMORY_TOOL_SPEC",
    "KnotAPIError",
    "KnotClient",
    "KnotClientError",
    "KnotPolicyRejectedError",
    "KnotStrandsHookProvider",
    "KnotToolset",
    "__version__",
    "get_default_client",
    "is_strands_available",
    "knot_commit_decision",
    "knot_get_active_state",
    "knot_memory",
    "set_default_client",
]
