"""Example AWS Strands Agent using Knot EnterpriseBrain governed v1 -> v2 memory.

Run a zero-dependency local simulation without AWS Bedrock credentials:
    python3 examples/strands_enterprise_agent.py --offline-demo

Run against live Amazon Bedrock + Knot EnterpriseBrain VPC:
    export KNOT_BASE_URL="https://knot-internal.your-vpc.internal/v1"
    export KNOT_API_KEY="your-token"
    export KNOT_DOMAIN="enterprise-architecture"
    python3 examples/strands_enterprise_agent.py
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any

from knot_strands import (
    KnotClient,
    KnotStrandsHookProvider,
    KnotToolset,
    is_strands_available,
    knot_commit_decision,
    knot_get_active_state,
    knot_memory,
)


def _run_offline_demo() -> None:
    """Exercise full v1 -> v2 supersession, as_of replay, and Turn-0 hook recovery locally."""
    store: list[dict[str, Any]] = []
    lock = threading.Lock()

    class DemoHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _reply(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if self.path == "/v1/context/upsert":
                with lock:
                    supersedes = data.get("supersedes") or []
                    for prev in store:
                        if prev["id"] in supersedes:
                            prev["status"] = "superseded"
                    item_id = f"ctx_{len(store) + 1}"
                    rec = {
                        "id": item_id,
                        "subjectId": data["subjectId"],
                        "domain": data["domain"],
                        "category": data["category"],
                        "entityKey": data["entityKey"],
                        "content": data["content"],
                        "observedAt": data["observedAt"],
                        "supersedes": supersedes,
                        "version": len(store) + 1,
                        "status": "active",
                    }
                    store.append(rec)
                self._reply(200, rec)
                return

            if self.path == "/v1/context/retrieve":
                as_of = data.get("asOf")
                with lock:
                    if as_of:
                        eligible = [r for r in store if r["observedAt"] <= as_of]
                        items = eligible[-1:] if eligible else []
                    else:
                        items = [r for r in store if r["status"] == "active"]
                self._reply(200, {"subjectId": data["subjectId"], "items": items})
                return

            self._reply(404, {"error": "not_found"})

        def do_GET(self) -> None:
            if self.path.startswith("/v1/subjects/") and "/lineage" in self.path:
                with lock:
                    self._reply(200, {"lineage": list(store)})
                return
            self._reply(404, {"error": "not_found"})

        def do_DELETE(self) -> None:
            if self.path.startswith("/v1/subjects/") and self.path.endswith("/key"):
                with lock:
                    store.clear()
                self._reply(200, {"erased": True, "latencyMs": 3.4})
                return
            self._reply(404, {"error": "not_found"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address

    try:
        client = KnotClient(
            api_key="demo_token",
            base_url=f"http://{host}:{port}",
            domain="enterprise-architecture",
            default_user_id="svc-platform",
        )
        v1 = json.loads(
            knot_memory(
                action="store",
                content="Orders service retries 2 times synchronously.",
                user_id="svc-platform",
                entity_key="orders_retry_policy",
                domain="enterprise-architecture",
                observed_at="2026-01-15T00:00:00Z",
                client=client,
            )
        )
        print("Committed v1:", v1["id"])

        v2 = json.loads(
            knot_commit_decision(
                decision="Orders service uses 4 exponential backoff retries via SQS DLQ.",
                entity_key="orders_retry_policy",
                user_id="svc-platform",
                domain="enterprise-architecture",
                supersedes=[v1["id"]],
                rationale="Prevent synchronous checkout timeout cascades.",
                observed_at="2026-06-01T00:00:00Z",
                client=client,
            )
        )
        print("Superseded with v2:", v2["id"])

        active = json.loads(
            knot_get_active_state(
                user_id="svc-platform",
                domain="enterprise-architecture",
                client=client,
            )
        )
        print("Active governing state:", active["active_state"][0]["content"])

        replay = json.loads(
            knot_memory(
                action="retrieve",
                query="retry",
                user_id="svc-platform",
                domain="enterprise-architecture",
                as_of="2026-02-01T00:00:00Z",
                client=client,
            )
        )
        print("Point-in-time as_of (2026-02-01):", replay["items"][0]["content"])

        hook = KnotStrandsHookProvider(
            client=client,
            user_id="svc-platform",
            domain="enterprise-architecture",
        )
        print("\nTurn-0 recovery block:\n" + hook.format_recovery_block())
    finally:
        server.shutdown()
        server.server_close()


def _run_live_strands_agent(prompt: str) -> None:
    if not is_strands_available():
        raise SystemExit(
            "strands-agents is not installed. Run `pip install 'knot-strands-agents[strands]'` "
            "or pass `--offline-demo`."
        )

    from strands import Agent

    client = KnotClient()
    toolset = KnotToolset(client=client, user_id="svc-platform", domain=client.domain)
    hook = KnotStrandsHookProvider(client=client, user_id="svc-platform", domain=client.domain)
    agent = Agent(tools=toolset.tools, hooks=[hook])
    response = agent(prompt)
    print(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Knot + AWS Strands Agents example")
    parser.add_argument("--offline-demo", action="store_true", help="Run local mock server demo")
    parser.add_argument(
        "--prompt",
        default="Check the active governing state before answering about retry policies.",
        help="Prompt to send to the live Strands Agent",
    )
    args = parser.parse_args()

    if args.offline_demo:
        _run_offline_demo()
    else:
        _run_live_strands_agent(args.prompt)


if __name__ == "__main__":
    main()
