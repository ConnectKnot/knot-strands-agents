"""Unit and integration tests for knot_strands (zero external dependencies)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
from pathlib import Path
import socket
import threading
import unittest
from typing import Any
import urllib.parse


REPO_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_EM_DASH = chr(0x2014)


class _MockEnterpriseBrainState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self.counter = 0
        self.items_by_subject: dict[str, list[dict[str, Any]]] = {}
        self.erased_subjects: set[str] = set()

    def reset_or_record(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.requests.append(entry)


def _start_mock_server() -> tuple[ThreadingHTTPServer, str, _MockEnterpriseBrainState]:
    state = _MockEnterpriseBrainState()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            return json.loads(raw) if raw.strip() else {}

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            body = self._read_json()
            state.reset_or_record(
                {
                    "method": "POST",
                    "path": parsed.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
            )

            if parsed.path == "/v1/context/upsert":
                content = str(body.get("content", ""))
                if "4111-1111-1111-1111" in content or "4111111111111111" in content:
                    self._send_json(
                        422,
                        {
                            "code": "PCI_CDE_PAYLOAD_REJECTED",
                            "message": "Raw CDE PAN/CVV payload rejected by Knot write-door policy.",
                        },
                    )
                    return

                with state.lock:
                    state.counter += 1
                    subject_id = str(body.get("subjectId") or "default")
                    state.erased_subjects.discard(subject_id)
                    domain = str(body.get("domain") or self.headers.get("X-Knot-Domain") or "default")
                    entity_key = str(body.get("entityKey") or f"item_{state.counter}")
                    supersedes = body.get("supersedes") or []
                    if isinstance(supersedes, str):
                        supersedes = [supersedes]

                    subject_items = state.items_by_subject.setdefault(subject_id, [])
                    prior_versions = [
                        it for it in subject_items if it["entityKey"] == entity_key and it["domain"] == domain
                    ]
                    version = len(prior_versions) + 1
                    item_id = f"ctx_v{version}_{state.counter}"

                    for existing in subject_items:
                        if existing["id"] in supersedes or (
                            existing["entityKey"] == entity_key
                            and existing["domain"] == domain
                            and existing["status"] == "active"
                            and supersedes
                        ):
                            existing["status"] = "superseded"
                            existing["supersededBy"] = item_id

                    record = {
                        "id": item_id,
                        "subjectId": subject_id,
                        "domain": domain,
                        "category": body.get("category") or "fact",
                        "entityKey": entity_key,
                        "content": content,
                        "observedAt": body.get("observedAt") or "2026-06-01T00:00:00Z",
                        "supersedes": list(supersedes),
                        "provenance": body.get("provenance") or {},
                        "version": version,
                        "status": "active",
                    }
                    subject_items.append(record)

                self._send_json(200, record)
                return

            if parsed.path == "/v1/context/retrieve":
                with state.lock:
                    subject_id = str(body.get("subjectId") or "default")
                    if subject_id in state.erased_subjects:
                        self._send_json(
                            200,
                            {
                                "subjectId": subject_id,
                                "items": [],
                                "erased": True,
                                "latencyMs": 2.1,
                            },
                        )
                        return

                    scopes = body.get("scopes") or []
                    query = str(body.get("query") or "").strip().lower()
                    as_of = body.get("asOf")
                    entity_key = body.get("entityKey")
                    category = body.get("category")
                    limit = int(body.get("limit") or 20)

                    items = [
                        dict(it)
                        for it in state.items_by_subject.get(subject_id, [])
                        if (not scopes or it["domain"] in scopes)
                        and (not entity_key or it["entityKey"] == entity_key)
                        and (not category or it["category"] == category)
                    ]

                    if as_of:
                        eligible = [it for it in items if it["observedAt"] <= as_of]
                        latest_by_key: dict[tuple[str, str], dict[str, Any]] = {}
                        for it in eligible:
                            k = (it["domain"], it["entityKey"])
                            prev = latest_by_key.get(k)
                            if prev is None or (it["observedAt"], it["version"]) >= (
                                prev["observedAt"],
                                prev["version"],
                            ):
                                latest_by_key[k] = it
                        selected = list(latest_by_key.values())
                    else:
                        selected = [it for it in items if it["status"] == "active"]

                    if query:
                        selected = [
                            it
                            for it in selected
                            if query in it["content"].lower()
                            or query in it["entityKey"].lower()
                            or query in it["category"].lower()
                        ]

                    selected = selected[:limit]

                self._send_json(
                    200,
                    {
                        "subjectId": subject_id,
                        "items": selected,
                        "asOf": as_of,
                        "latencyMs": 4.4,
                    },
                )
                return

            self._send_json(404, {"code": "NOT_FOUND", "message": f"Unknown route: {parsed.path}"})

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            state.reset_or_record(
                {
                    "method": "GET",
                    "path": parsed.path,
                    "query": qs,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                }
            )
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "subjects" and parts[3] == "lineage":
                subject_id = urllib.parse.unquote(parts[2])
                entity_filter = (qs.get("entityKey") or [None])[0]
                domain_filter = (qs.get("domain") or [None])[0]
                with state.lock:
                    items = [
                        dict(it)
                        for it in state.items_by_subject.get(subject_id, [])
                        if (not entity_filter or it["entityKey"] == entity_filter)
                        and (not domain_filter or it["domain"] == domain_filter)
                    ]
                    chains = []
                    for it in items:
                        if it["supersedes"]:
                            chains.append(f"{','.join(it['supersedes'])} -> {it['id']}")
                self._send_json(
                    200,
                    {
                        "subjectId": subject_id,
                        "items": items,
                        "lineage": items,
                        "chains": chains,
                    },
                )
                return

            self._send_json(404, {"code": "NOT_FOUND", "message": f"Unknown route: {parsed.path}"})

        def do_DELETE(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            state.reset_or_record(
                {
                    "method": "DELETE",
                    "path": parsed.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                }
            )
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) == 4 and parts[0] == "v1" and parts[1] == "subjects" and parts[3] == "key":
                subject_id = urllib.parse.unquote(parts[2])
                with state.lock:
                    state.erased_subjects.add(subject_id)
                    state.items_by_subject.pop(subject_id, None)
                self._send_json(
                    200,
                    {
                        "erased": True,
                        "subjectId": subject_id,
                        "keyEpoch": 0,
                        "latencyMs": 2.8,
                    },
                )
                return

            self._send_json(404, {"code": "NOT_FOUND", "message": f"Unknown route: {parsed.path}"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}", state


def _unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class TestKnotClient(unittest.TestCase):
    def test_supports_https_loopback_and_vpc_internal_urls_and_rejects_public_http(self) -> None:
        from knot_strands.client import KnotClient, KnotClientError

        c_https = KnotClient(api_key="tok", base_url="https://production.connectknot.com")
        self.assertEqual(c_https.base_url, "https://production.connectknot.com/v1")

        c_loopback = KnotClient(api_key="tok", base_url="http://127.0.0.1:8080/v1/")
        self.assertEqual(c_loopback.base_url, "http://127.0.0.1:8080/v1")

        c_localhost = KnotClient(api_key="tok", base_url="http://localhost:9000")
        self.assertEqual(c_localhost.base_url, "http://localhost:9000/v1")

        c_vpc_svc = KnotClient(
            api_key="tok",
            base_url="http://knot-enterprisebrain.knot-system.svc.cluster.local:8080",
        )
        self.assertEqual(
            c_vpc_svc.base_url,
            "http://knot-enterprisebrain.knot-system.svc.cluster.local:8080/v1",
        )

        c_vpc_internal = KnotClient(api_key="tok", base_url="http://knot.vpc.internal")
        self.assertEqual(c_vpc_internal.base_url, "http://knot.vpc.internal/v1")

        with self.assertRaises(KnotClientError):
            KnotClient(api_key="tok", base_url="http://api.public-example.org/v1")

    def test_sends_bearer_and_x_knot_domain_and_handles_422_pci_rejection_cleanly(self) -> None:
        from knot_strands.client import KnotClient

        server, base_url, state = _start_mock_server()
        try:
            client = KnotClient(api_key="knot_vpc_secret", base_url=base_url, domain="payments-guard")
            ok_resp = client.upsert(
                "Use tokenized card refs only.",
                user_id="usr_101",
                entity_key="card_policy",
                category="constraint",
            )
            self.assertEqual(ok_resp["status"], "active")

            last_req = state.requests[-1]
            self.assertEqual(last_req["headers"].get("authorization"), "Bearer knot_vpc_secret")
            self.assertEqual(last_req["headers"].get("x-knot-domain"), "payments-guard")

            pci_resp = client.upsert(
                "Store customer card 4111-1111-1111-1111 for convenience",
                user_id="usr_101",
                entity_key="card_raw",
                domain="checkout-cde",
            )
            self.assertFalse(pci_resp.get("ok", True))
            self.assertTrue(pci_resp.get("rejected"))
            self.assertEqual(pci_resp.get("status"), "rejected")
            self.assertEqual(pci_resp.get("http_status"), 422)
            self.assertEqual(pci_resp.get("code"), "PCI_CDE_PAYLOAD_REJECTED")
            self.assertEqual(state.requests[-1]["headers"].get("x-knot-domain"), "checkout-cde")
        finally:
            server.shutdown()
            server.server_close()


class TestKnotStrandsTools(unittest.TestCase):
    def test_knot_memory_tool_spec_and_mem0_signature_parity(self) -> None:
        from knot_strands.tools import knot_memory

        spec = getattr(knot_memory, "TOOL_SPEC", None)
        self.assertIsInstance(spec, dict)
        self.assertEqual(spec["name"], "knot_memory")
        props = spec["inputSchema"]["json"]["properties"]
        for expected_param in (
            "action",
            "content",
            "query",
            "user_id",
            "entity_key",
            "domain",
            "as_of",
            "supersedes",
        ):
            self.assertIn(expected_param, props)
        self.assertEqual(
            set(props["action"]["enum"]),
            {"store", "retrieve", "list", "history", "erase"},
        )

        target_fn = getattr(knot_memory, "_tool_func", knot_memory)
        sig = inspect.signature(target_fn)
        for expected_param in (
            "action",
            "content",
            "query",
            "user_id",
            "entity_key",
            "domain",
            "as_of",
            "supersedes",
        ):
            self.assertIn(expected_param, sig.parameters)

    def test_knot_memory_v1_v2_supersession_as_of_history_list_and_erase(self) -> None:
        from knot_strands.client import KnotClient
        from knot_strands.tools import knot_memory

        server, base_url, state = _start_mock_server()
        try:
            client = KnotClient(api_key="knot_test", base_url=base_url, domain="fulfillment")

            v1_raw = knot_memory(
                action="store",
                content="Shipping SLA cutoff is 14:00 UTC.",
                user_id="usr_42",
                entity_key="shipping_sla",
                domain="fulfillment",
                observed_at="2026-01-10T00:00:00Z",
                client=client,
            )
            v1 = json.loads(v1_raw)
            self.assertTrue(v1["ok"])
            v1_id = v1["id"]

            v2_raw = knot_memory(
                action="store",
                content="Shipping SLA cutoff moved to 17:00 UTC.",
                user_id="usr_42",
                entity_key="shipping_sla",
                domain="fulfillment",
                supersedes=[v1_id],
                observed_at="2026-06-15T00:00:00Z",
                client=client,
            )
            v2 = json.loads(v2_raw)
            self.assertTrue(v2["ok"])
            v2_id = v2["id"]
            self.assertEqual(v2["supersedes"], [v1_id])

            active_raw = knot_memory(
                action="retrieve",
                query="Shipping SLA",
                user_id="usr_42",
                domain="fulfillment",
                client=client,
            )
            active = json.loads(active_raw)
            self.assertTrue(active["ok"])
            self.assertEqual(len(active["items"]), 1)
            self.assertEqual(active["items"][0]["id"], v2_id)
            self.assertIn("17:00 UTC", active["items"][0]["content"])

            historical_raw = knot_memory(
                action="retrieve",
                query="Shipping SLA",
                user_id="usr_42",
                domain="fulfillment",
                as_of="2026-02-01T00:00:00Z",
                client=client,
            )
            historical = json.loads(historical_raw)
            self.assertTrue(historical["ok"])
            self.assertEqual(len(historical["items"]), 1)
            self.assertEqual(historical["items"][0]["id"], v1_id)
            self.assertIn("14:00 UTC", historical["items"][0]["content"])

            listed = json.loads(
                knot_memory(action="list", user_id="usr_42", domain="fulfillment", client=client)
            )
            self.assertTrue(listed["ok"])
            self.assertEqual(len(listed["items"]), 1)
            self.assertEqual(listed["items"][0]["id"], v2_id)

            history_raw = knot_memory(
                action="history",
                user_id="usr_42",
                entity_key="shipping_sla",
                domain="fulfillment",
                client=client,
            )
            history = json.loads(history_raw)
            self.assertTrue(history["ok"])
            self.assertEqual(len(history["lineage"]), 2)
            self.assertIn(f"{v1_id} -> {v2_id}", history["chains"])
            self.assertEqual(state.requests[-1]["method"], "GET")
            self.assertEqual(state.requests[-1]["path"], "/v1/subjects/usr_42/lineage")

            erased_raw = knot_memory(
                action="erase",
                user_id="usr_42",
                domain="fulfillment",
                client=client,
            )
            erased = json.loads(erased_raw)
            self.assertTrue(erased["ok"])
            self.assertTrue(erased["erased"])
            self.assertLess(erased["latencyMs"], 100.0)
            self.assertEqual(state.requests[-1]["method"], "DELETE")
            self.assertEqual(state.requests[-1]["path"], "/v1/subjects/usr_42/key")

            after_erase = json.loads(
                knot_memory(
                    action="retrieve",
                    query="Shipping SLA",
                    user_id="usr_42",
                    domain="fulfillment",
                    client=client,
                )
            )
            self.assertEqual(after_erase["items"], [])
        finally:
            server.shutdown()
            server.server_close()

    def test_knot_commit_decision_and_knot_get_active_state(self) -> None:
        from knot_strands.client import KnotClient
        from knot_strands.tools import knot_commit_decision, knot_get_active_state

        server, base_url, _ = _start_mock_server()
        try:
            client = KnotClient(api_key="knot_test", base_url=base_url, domain="architecture")

            self.assertEqual(knot_commit_decision.TOOL_SPEC["name"], "knot_commit_decision")
            self.assertEqual(knot_get_active_state.TOOL_SPEC["name"], "knot_get_active_state")

            d1_raw = knot_commit_decision(
                decision="Use PostgreSQL pgvector inside VPC for governed context.",
                entity_key="context_db",
                user_id="team_core",
                domain="architecture",
                rationale="Zero egress inside EKS cluster.",
                client=client,
            )
            d1 = json.loads(d1_raw)
            self.assertTrue(d1["ok"])
            self.assertEqual(d1["category"], "decision")

            d2_raw = knot_commit_decision(
                decision="Use Amazon Aurora PostgreSQL with pgvector and KMS envelope keys.",
                entity_key="context_db",
                user_id="team_core",
                domain="architecture",
                supersedes=[d1["id"]],
                rationale="Multi-AZ failover and AWS KMS SigV4 root unwrapping.",
                client=client,
            )
            d2 = json.loads(d2_raw)
            self.assertTrue(d2["ok"])

            state_raw = knot_get_active_state(
                user_id="team_core",
                domain="architecture",
                client=client,
            )
            active = json.loads(state_raw)
            self.assertTrue(active["ok"])
            self.assertEqual(active["count"], 1)
            self.assertEqual(active["active_state"][0]["id"], d2["id"])
            self.assertIn("Amazon Aurora PostgreSQL", active["active_state"][0]["content"])
        finally:
            server.shutdown()
            server.server_close()

    def test_knot_memory_handles_422_pci_and_validation_errors_cleanly(self) -> None:
        from knot_strands.client import KnotClient
        from knot_strands.tools import knot_memory

        server, base_url, _ = _start_mock_server()
        try:
            client = KnotClient(api_key="knot_test", base_url=base_url, domain="payments")
            pci_raw = knot_memory(
                action="store",
                content="Customer card 4111-1111-1111-1111",
                user_id="usr_pci",
                entity_key="card_note",
                domain="payments",
                client=client,
            )
            pci = json.loads(pci_raw)
            self.assertFalse(pci["ok"])
            self.assertEqual(pci["code"], "PCI_CDE_PAYLOAD_REJECTED")
            self.assertEqual(pci["http_status"], 422)

            missing_content = json.loads(
                knot_memory(action="store", user_id="usr_pci", client=client)
            )
            self.assertFalse(missing_content["ok"])
            self.assertIn("content", missing_content["error"].lower())

            bad_action = json.loads(
                knot_memory(action="purge_everything", user_id="usr_pci", client=client)
            )
            self.assertFalse(bad_action["ok"])
            self.assertIn("action", bad_action["error"].lower())
        finally:
            server.shutdown()
            server.server_close()

    def test_knot_toolset_binds_tenant_and_domain_and_optional_strands_detection(self) -> None:
        from knot_strands import KnotToolset, is_strands_available
        from knot_strands.client import KnotClient

        self.assertIsInstance(is_strands_available(), bool)

        server, base_url, state = _start_mock_server()
        try:
            client = KnotClient(api_key="knot_test", base_url=base_url, domain="claims")
            toolset = KnotToolset(client=client, user_id="usr_bound_99", domain="claims")
            self.assertEqual(len(toolset.tools), 3)

            committed = json.loads(
                toolset.knot_commit_decision(
                    decision="Require dual approval above 5000 USD.",
                    entity_key="approval_limit",
                )
            )
            self.assertTrue(committed["ok"])
            self.assertEqual(state.requests[-1]["body"]["subjectId"], "usr_bound_99")
            self.assertEqual(state.requests[-1]["headers"].get("x-knot-domain"), "claims")

            active = json.loads(toolset.knot_get_active_state())
            self.assertTrue(active["ok"])
            self.assertEqual(active["count"], 1)
            self.assertIn("5000 USD", active["active_state"][0]["content"])
        finally:
            server.shutdown()
            server.server_close()


class TestKnotStrandsHookProvider(unittest.TestCase):
    def test_formats_turn0_context_recovery_when_active_facts_exist(self) -> None:
        from knot_strands.client import KnotClient
        from knot_strands.hooks import KnotStrandsHookProvider

        server, base_url, _ = _start_mock_server()
        try:
            client = KnotClient(api_key="knot_test", base_url=base_url, domain="platform")
            client.upsert(
                "All inference runs on Amazon Bedrock with KNOT_ALLOW_REMOTE_MODELS=false.",
                user_id="usr_ops",
                entity_key="llm_egress",
                category="constraint",
            )

            provider = KnotStrandsHookProvider(
                client=client,
                user_id="usr_ops",
                domain="platform",
            )
            block = provider.format_recovery_block()
            self.assertIn("[KNOT GOVERNED ACTIVE CONTEXT]", block)
            self.assertIn("llm_egress", block)
            self.assertIn("KNOT_ALLOW_REMOTE_MODELS=false", block)

            class DummyAgent:
                def __init__(self) -> None:
                    self.system_prompt = "You are a platform engineer."

            class DummyBeforeInvocationEvent:
                def __init__(self, agent: Any) -> None:
                    self.agent = agent

            class DummyRegistry:
                def __init__(self) -> None:
                    self.callbacks: list[tuple[Any, Any]] = []

                def add_callback(self, event_type: Any, callback: Any) -> None:
                    self.callbacks.append((event_type, callback))

            registry = DummyRegistry()
            provider.register_hooks(registry)
            self.assertGreaterEqual(len(registry.callbacks), 1)

            agent = DummyAgent()
            event = DummyBeforeInvocationEvent(agent)
            provider.on_before_invocation(event)
            self.assertIn("You are a platform engineer.", agent.system_prompt)
            self.assertIn("KNOT GOVERNED ACTIVE CONTEXT", agent.system_prompt)

            prompt_after_turn0 = agent.system_prompt
            provider.on_before_invocation(event)
            self.assertEqual(agent.system_prompt, prompt_after_turn0)
        finally:
            server.shutdown()
            server.server_close()

    def test_degrades_gracefully_when_empty_or_offline(self) -> None:
        from knot_strands.client import KnotClient
        from knot_strands.hooks import KnotStrandsHookProvider

        server, base_url, _ = _start_mock_server()
        try:
            client = KnotClient(api_key="knot_test", base_url=base_url, domain="empty-domain")
            provider_empty = KnotStrandsHookProvider(
                client=client,
                user_id="usr_empty",
                domain="empty-domain",
            )
            self.assertEqual(provider_empty.format_recovery_block(), "")

            class DummyAgent:
                system_prompt = "Base prompt."

            class DummyEvent:
                agent = DummyAgent()

            provider_empty.on_before_invocation(DummyEvent())
            self.assertEqual(DummyEvent.agent.system_prompt, "Base prompt.")
        finally:
            server.shutdown()
            server.server_close()

        closed_port = _unused_local_port()
        offline_client = KnotClient(
            api_key="knot_test",
            base_url=f"http://127.0.0.1:{closed_port}",
            domain="offline-domain",
            timeout_seconds=0.5,
        )
        provider_offline = KnotStrandsHookProvider(
            client=offline_client,
            user_id="usr_offline",
            domain="offline-domain",
        )
        self.assertEqual(provider_offline.format_recovery_block(), "")

        class OfflineAgent:
            system_prompt = "Offline safe prompt."

        class OfflineEvent:
            agent = OfflineAgent()

        provider_offline.on_before_invocation(OfflineEvent())
        self.assertEqual(OfflineEvent.agent.system_prompt, "Offline safe prompt.")


class TestDeploymentAndRepoHygiene(unittest.TestCase):
    def test_aws_eks_values_yaml_has_kms_airgap_and_agentcore_instructions(self) -> None:
        values_path = REPO_ROOT / "deploy" / "aws-eks-agentcore" / "aws-eks-values.yaml"
        self.assertTrue(values_path.is_file(), f"Missing {values_path}")
        content = values_path.read_text(encoding="utf-8")
        self.assertIn("kms", content.lower())
        self.assertIn('KNOT_ALLOW_REMOTE_MODELS: "false"', content)
        self.assertIn("AgentCore Gateway", content)

    def test_repo_wide_zero_unicode_em_dash(self) -> None:
        checked_suffixes = {".py", ".md", ".yaml", ".yml", ".toml", ".json", ".txt", ".sh"}
        offenders: list[str] = []
        for path in sorted(REPO_ROOT.rglob("*")):
            if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if path.suffix in checked_suffixes or path.name == "LICENSE":
                text = path.read_text(encoding="utf-8")
                if FORBIDDEN_EM_DASH in text:
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [], f"Forbidden Unicode em-dash found in: {offenders}")


if __name__ == "__main__":
    unittest.main()
