"""Zero-dependency HTTP client for Knot EnterpriseBrain and hosted Knot APIs."""

from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
import json
import os
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_KNOT_BASE_URL = "https://production.connectknot.com/v1"

_VPC_INTERNAL_SUFFIXES = (
    ".internal",
    ".local",
    ".svc",
    ".cluster.local",
    ".vpc",
    ".lan",
    ".corp",
)


class KnotClientError(Exception):
    """Base error raised for Knot configuration, network, or API failures."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload or {}


class KnotPolicyRejectedError(KnotClientError):
    """Raised when EnterpriseBrain write-door rejects a payload (e.g. HTTP 422)."""


class KnotAPIError(KnotClientError):
    """Raised for unexpected non-2xx HTTP responses from Knot endpoints."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slugify(value: str, max_len: int = 120) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return (slug or "memory")[:max_len].strip("_") or "memory"


def _normalize_supersedes(supersedes: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if supersedes is None:
        return []
    if isinstance(supersedes, str):
        cleaned = supersedes.strip()
        return [cleaned] if cleaned else []
    return [str(item).strip() for item in supersedes if str(item).strip()]


def _is_loopback_or_vpc_host(hostname: str) -> bool:
    host = hostname.strip().lower().strip("[]")
    if not host:
        return False
    if host in {"localhost", "localhost.localdomain", "host.docker.internal"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        pass
    if "." not in host:
        # Single-label Kubernetes / ECS service DNS inside VPC.
        return True
    return any(host.endswith(suffix) for suffix in _VPC_INTERNAL_SUFFIXES)


def _validate_and_normalize_base_url(
    base_url: str | None,
    *,
    allow_insecure_http: bool = False,
) -> str:
    raw = (base_url or DEFAULT_KNOT_BASE_URL).strip()
    if not raw:
        raw = DEFAULT_KNOT_BASE_URL

    parsed = urllib.parse.urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme not in {"https", "http"} or not parsed.netloc or not parsed.hostname:
        raise KnotClientError(
            f"Invalid Knot base_url '{raw}'. Expected an https:// or VPC-internal http:// URL."
        )

    if scheme == "http" and not allow_insecure_http and not _is_loopback_or_vpc_host(parsed.hostname):
        raise KnotClientError(
            f"Refusing unencrypted http:// URL for non-VPC host '{parsed.hostname}'. "
            "Use https:// or a loopback/VPC-internal endpoint, or set allow_insecure_http=True."
        )

    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1"
    elif not path.endswith("/v1"):
        path = f"{path}/v1"

    return urllib.parse.urlunparse((scheme, parsed.netloc, path, "", "", ""))


class KnotClient:
    """Zero-dependency Python stdlib client for Knot EnterpriseBrain REST APIs."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        domain: str | None = None,
        default_user_id: str | None = None,
        timeout_seconds: float = 15.0,
        allow_insecure_http: bool = False,
        raise_on_policy_reject: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize a Knot EnterpriseBrain REST client.

        Args:
            api_key: Bearer token or EnterpriseBrain service key. Falls back to KNOT_API_KEY.
            base_url: HTTPS endpoint or loopback/VPC internal URL. Falls back to KNOT_BASE_URL.
            domain: Default Knot governance domain sent in X-Knot-Domain. Falls back to KNOT_DOMAIN.
            default_user_id: Default subject/user ID when not supplied per call.
            timeout_seconds: HTTP socket timeout in seconds.
            allow_insecure_http: Permit non-VPC http:// URLs when explicitly requested.
            raise_on_policy_reject: If True, raise KnotPolicyRejectedError on HTTP 422 PCI rejections
                instead of returning a structured rejection dict.
            headers: Optional extra HTTP headers sent on every request.
        """
        resolved_key = api_key if api_key is not None else os.environ.get("KNOT_API_KEY", "")
        self.api_key = resolved_key.strip()
        raw_url = base_url if base_url is not None else os.environ.get("KNOT_BASE_URL", DEFAULT_KNOT_BASE_URL)
        self.base_url = _validate_and_normalize_base_url(
            raw_url,
            allow_insecure_http=allow_insecure_http,
        )
        self.domain = (domain or os.environ.get("KNOT_DOMAIN") or "default").strip() or "default"
        self.default_user_id = (
            default_user_id or os.environ.get("KNOT_USER_ID") or "default"
        ).strip() or "default"
        self.timeout_seconds = float(timeout_seconds)
        self.raise_on_policy_reject = raise_on_policy_reject
        self.extra_headers = dict(headers or {})

    def __repr__(self) -> str:
        masked = f"{self.api_key[:4]}..." if len(self.api_key) > 4 else ("***" if self.api_key else "")
        return (
            f"KnotClient(base_url={self.base_url!r}, domain={self.domain!r}, "
            f"default_user_id={self.default_user_id!r}, api_key={masked!r})"
        )

    def _resolve_user_id(self, user_id: str | None) -> str:
        resolved = (user_id or self.default_user_id or "").strip()
        if not resolved:
            raise ValueError("user_id cannot be empty.")
        return resolved

    def _resolve_domain(self, domain: str | None) -> str:
        resolved = (domain or self.domain or "default").strip()
        return resolved or "default"

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        effective_domain = self._resolve_domain(domain)
        url = f"{self.base_url}{path}"
        if query:
            qs = urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
            if qs:
                url = f"{url}?{qs}"

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Knot-Domain": effective_domain,
            "User-Agent": "knot-strands-agents/1.0.0",
            **self.extra_headers,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url=url, data=body, headers=headers, method=method.upper())

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            with err:
                raw_err = err.read().decode("utf-8", errors="replace")
            err_obj: dict[str, Any] = {}
            code: str | None = None
            msg = raw_err[:400]
            try:
                parsed_err = json.loads(raw_err)
                if isinstance(parsed_err, dict):
                    err_obj = parsed_err
                    code = parsed_err.get("code") or parsed_err.get("error")
                    msg = str(parsed_err.get("message") or msg)
            except json.JSONDecodeError:
                pass

            if err.code == 422 and code == "PCI_CDE_PAYLOAD_REJECTED":
                rejection: dict[str, Any] = {
                    "ok": False,
                    "status": "rejected",
                    "rejected": True,
                    "http_status": 422,
                    "code": "PCI_CDE_PAYLOAD_REJECTED",
                    "message": msg or "Payload rejected by Knot PCI CDE write-door firewall.",
                    "domain": effective_domain,
                }
                if self.raise_on_policy_reject:
                    raise KnotPolicyRejectedError(
                        rejection["message"],
                        status=422,
                        code="PCI_CDE_PAYLOAD_REJECTED",
                        payload=rejection,
                    ) from err
                return rejection

            raise KnotAPIError(
                f"HTTP {err.code} from {method.upper()} {url}: {msg}",
                status=err.code,
                code=code,
                payload=err_obj,
            ) from err
        except (urllib.error.URLError, OSError, TimeoutError) as err:
            reason = getattr(err, "reason", str(err))
            raise KnotClientError(f"Network error calling {method.upper()} {url}: {reason}") from err

        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as err:
            raise KnotClientError(f"Invalid JSON response from {method.upper()} {url}") from err
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    def upsert(
        self,
        content: str,
        *,
        user_id: str | None = None,
        entity_key: str | None = None,
        category: str = "fact",
        domain: str | None = None,
        observed_at: str | None = None,
        supersedes: str | list[str] | tuple[str, ...] | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Commit or supersede a context item via POST /v1/context/upsert."""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string.")

        effective_user = self._resolve_user_id(user_id)
        effective_domain = self._resolve_domain(domain)
        resolved_key = (entity_key or "").strip() or _slugify(" ".join(content.strip().split()[:6]))
        supersedes_list = _normalize_supersedes(supersedes)

        merged_provenance: dict[str, Any] = {
            "source": "knot-strands-agents",
            **(provenance or {}),
        }
        if metadata:
            merged_provenance["metadata"] = metadata

        payload: dict[str, Any] = {
            "subjectId": effective_user,
            "domain": effective_domain,
            "category": category or "fact",
            "entityKey": resolved_key,
            "content": content.strip(),
            "observedAt": observed_at or _utc_now_iso(),
            "supersedes": supersedes_list,
            "provenance": merged_provenance,
        }
        return self._request("POST", "/context/upsert", payload=payload, domain=effective_domain)

    def retrieve(
        self,
        query: str = "",
        *,
        user_id: str | None = None,
        domain: str | None = None,
        scopes: list[str] | None = None,
        entity_key: str | None = None,
        category: str | None = None,
        as_of: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Query current governing truth or as_of historical truth via POST /v1/context/retrieve."""
        effective_user = self._resolve_user_id(user_id)
        effective_domain = self._resolve_domain(domain)
        resolved_scopes = scopes if scopes is not None else [effective_domain]

        payload: dict[str, Any] = {
            "subjectId": effective_user,
            "query": query or "",
            "scopes": resolved_scopes,
            "limit": int(limit),
        }
        if as_of is not None:
            payload["asOf"] = as_of
        if entity_key is not None:
            payload["entityKey"] = entity_key
        if category is not None:
            payload["category"] = category

        return self._request("POST", "/context/retrieve", payload=payload, domain=effective_domain)

    def lineage(
        self,
        user_id: str | None = None,
        *,
        entity_key: str | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """Fetch v1 -> v2 supersession provenance via GET /v1/subjects/{id}/lineage."""
        effective_user = self._resolve_user_id(user_id)
        effective_domain = self._resolve_domain(domain)
        encoded = urllib.parse.quote(effective_user, safe="")
        query: dict[str, str] = {}
        if entity_key:
            query["entityKey"] = entity_key
        if domain:
            query["domain"] = effective_domain
        return self._request(
            "GET",
            f"/subjects/{encoded}/lineage",
            query=query or None,
            domain=effective_domain,
        )

    def erase_subject(
        self,
        user_id: str | None = None,
        *,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """Cryptographically erase a subject key epoch (<100ms) via DELETE /v1/subjects/{id}/key."""
        effective_user = self._resolve_user_id(user_id)
        effective_domain = self._resolve_domain(domain)
        encoded = urllib.parse.quote(effective_user, safe="")
        return self._request("DELETE", f"/subjects/{encoded}/key", domain=effective_domain)
