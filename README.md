# Knot for AWS Strands Agents SDK (`knot-strands-agents`)

[![CI](https://github.com/ConnectKnot/knot-strands-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/ConnectKnot/knot-strands-agents/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+ Zero Deps](https://img.shields.io/badge/Python-3.10%2B%20zero%20deps-3776AB.svg)](pyproject.toml)
[![AWS Strands SDK](https://img.shields.io/badge/AWS-Strands%20Agents%20%26%20Bedrock%20AgentCore-FF9900.svg)](https://connectknot.com/enterprise-brain)

**Governed `v1 -> v2` temporal memory, decision provenance, and Turn-0 context recovery for the AWS Strands Agents SDK and Amazon Bedrock AgentCore.**

---

## Why Upgrade from `mem0_memory` to `knot_memory`?

`mem0_memory` was deprecated and removed in `strands-agents-tools` v0.9.0, and traditional vector memory stores return outdated `v1` notes right beside governing `v2` rules. When a compliance constraint, budget cap, or architecture decision changes, flat similarity search forces Bedrock models to guess which note still applies.

`knot_memory` replaces flat vector recall with deterministic temporal state governance backed by [Knot](https://connectknot.com) and [Knot EnterpriseBrain](https://connectknot.com/enterprise-brain):

| Capability | Legacy `mem0_memory` / Flat Vectors | `knot_memory` (`knot_strands`) |
| :--- | :--- | :--- |
| **Version updates (`v1 -> v2`)** | Appends duplicate vector chunks | Deterministic supersession (`supersedes=[v1_id]`) compiles active `v2` at write time |
| **Read latency and cost** | Embedding + re-ranking or LLM extraction | `<15ms` deterministic reads with zero read-time LLM calls |
| **Point-in-time audit (`as_of`)** | Not supported | Exact `as_of` timestamp replay across historical state |
| **Lineage provenance (`history`)** | Flat change log at best | Full `v1 -> v2` supersession graph via `GET /v1/subjects/{user_id}/lineage` |
| **Right to erasure (`erase`)** | Expensive vector index rewrites | `<100ms` subject crypto-erasure via `DELETE /v1/subjects/{user_id}/key` |
| **PCI / secret protection** | Unfiltered storage | Write-door firewall rejects raw PAN/CVV (`HTTP 422 PCI_CDE_PAYLOAD_REJECTED`) |
| **Cross-AI continuity** | Isolated inside one agent | Shared across Claude, ChatGPT, Gemini, Cursor, Kiro, and Bedrock via MCP |

---

## Installation

Core `knot_strands` uses **zero external dependencies** (Python 3.10+ standard library only) and detects `strands-agents` automatically at import time:

```bash
# Zero-dependency core package (ideal for Lambda layers, lightweight containers, and CI)
pip install knot-strands-agents

# Full AWS Strands Agents SDK integration
pip install "knot-strands-agents[strands]"
```

---

## Quickstart: Strands Agent + Turn-0 Context Recovery

### 1. Drop-in Replacement for `mem0_memory`

```python
from strands import Agent
from knot_strands import (
    KnotClient,
    KnotStrandsHookProvider,
    knot_commit_decision,
    knot_get_active_state,
    knot_memory,
)

client = KnotClient(
    base_url="https://knot-internal.your-vpc.internal/v1",  # or https://production.connectknot.com/v1
    api_key="your_knot_token",
    domain="enterprise-platform",
)

agent = Agent(
    tools=[knot_memory, knot_commit_decision, knot_get_active_state],
    hooks=[
        KnotStrandsHookProvider(
            client=client,
            user_id="svc-checkout",
            domain="enterprise-platform",
        )
    ],
)
```

### 2. Multi-Tenant Production Pattern (`KnotToolset`)

Pin `user_id` and `domain` on the server side so untrusted prompts cannot read or mutate another tenant's context:

```python
from strands import Agent
from knot_strands import KnotClient, KnotStrandsHookProvider, KnotToolset

client = KnotClient(base_url="https://knot-internal.your-vpc.internal/v1")
toolset = KnotToolset(client=client, user_id="tenant_42_usr_9", domain="claims")
hooks = KnotStrandsHookProvider(client=client, user_id="tenant_42_usr_9", domain="claims")

agent = Agent(tools=toolset.tools, hooks=[hooks])
```

### 3. Direct `v1 -> v2` Supersession, `as_of` Replay, Lineage, and Crypto-Erasure

```python
import json
from knot_strands import KnotClient, knot_memory

client = KnotClient(base_url="http://127.0.0.1:8080", api_key="knot_vpc_key", domain="fulfillment")

# 1. Commit v1
v1 = json.loads(knot_memory(
    action="store",
    content="Same-day shipping cutoff is 14:00 UTC.",
    user_id="usr_42",
    entity_key="shipping_sla",
    domain="fulfillment",
    observed_at="2026-01-10T00:00:00Z",
    client=client,
))

# 2. Supersede v1 with v2 on the same entity_key
v2 = json.loads(knot_memory(
    action="store",
    content="Same-day shipping cutoff moved to 17:00 UTC.",
    user_id="usr_42",
    entity_key="shipping_sla",
    domain="fulfillment",
    supersedes=[v1["id"]],
    observed_at="2026-06-15T00:00:00Z",
    client=client,
))

# 3. Current retrieve returns ONLY active v2
active = json.loads(knot_memory(action="retrieve", query="cutoff", user_id="usr_42", domain="fulfillment", client=client))

# 4. Point-in-time as_of returns v1
historical = json.loads(knot_memory(
    action="retrieve",
    query="cutoff",
    user_id="usr_42",
    domain="fulfillment",
    as_of="2026-02-01T00:00:00Z",
    client=client,
))

# 5. Inspect full v1 -> v2 lineage provenance
lineage = json.loads(knot_memory(action="history", user_id="usr_42", entity_key="shipping_sla", domain="fulfillment", client=client))

# 6. Sub-100ms subject cryptographic erasure (DELETE /v1/subjects/{user_id}/key)
erased = json.loads(knot_memory(action="erase", user_id="usr_42", domain="fulfillment", client=client))
```

Try the local self-contained walkthrough right now without AWS credentials:

```bash
python3 examples/strands_enterprise_agent.py --offline-demo
```

---

## Architecture: AWS EKS + AWS KMS + Amazon Bedrock AgentCore Gateway

Run **Knot EnterpriseBrain** inside your private AWS VPC alongside your Strands Agents and Bedrock AgentCore runtime:

```text
+------------------------------------------------------------------------------+
| AWS Private VPC                                                              |
|                                                                              |
|  +---------------------------+        +-----------------------------------+  |
|  | Strands Agents Runtime    |        | Amazon Bedrock AgentCore Gateway  |  |
|  | (EKS / AgentCore / Lambda)|        | (Streamable HTTP MCP Target)      |  |
|  | - knot_memory             |        | - OAuth2 JWKS / SigV4 / mTLS      |  |
|  | - knot_commit_decision    |        +-----------------+-----------------+  |
|  | - knot_get_active_state   |                          |                    |
|  | - KnotStrandsHookProvider |                          v                    |
|  +-------------+-------------+        +-----------------------------------+  |
|                | HTTPS / VPC REST     | Knot EnterpriseBrain (EKS Pods)   |  |
|                +--------------------->| - KNOT_ALLOW_REMOTE_MODELS=false  |  |
|                  X-Knot-Domain        | - Write-Door PCI/Secret Firewall  |  |
|                  Authorization Bearer | - knot-policy.yaml Domain ACLs    |  |
|                                       +--------+-----------------+--------+  |
|                                                |                 |           |
|                             IRSA + AWS KMS SigV4                 v           |
|                       +------------------------+   +----------------------+  |
|                       | AWS KMS Root Key       |   | Aurora PostgreSQL    |  |
|                       | alias/knot-enterprise- |   | with pgvector        |  |
|                       | brain-root (HKDF 3-lvl)|   | + SQS Ingest Worker  |  |
|                       +------------------------+   +----------------------+  |
+------------------------------------------------------------------------------+
```

Helm preset and Bedrock AgentCore Gateway setup instructions live in [`deploy/aws-eks-agentcore/aws-eks-values.yaml`](deploy/aws-eks-agentcore/aws-eks-values.yaml) and [`deploy/aws-eks-agentcore/README.md`](deploy/aws-eks-agentcore/README.md).

---

## Security, PCI-DSS, and AWS KMS FAQ

### How does Knot prevent raw credit card numbers or secrets from entering agent memory?
EnterpriseBrain enforces a deterministic write-door gate before embedding or storage. Payloads containing raw cardholder data (PAN/CVV) are rejected with `HTTP 422 PCI_CDE_PAYLOAD_REJECTED`, and high-entropy credential assignments (`Shannon entropy H >= 3.55 bits/char`, excluding git SHAs and UUIDv4s) are blocked. `KnotClient` and `knot_memory` handle `HTTP 422 PCI_CDE_PAYLOAD_REJECTED` cleanly and return a structured rejection payload without crashing the agent loop.

### How does AWS KMS key custody and 3-level HKDF work?
On pod startup via EKS IRSA (`eks.amazonaws.com/role-arn`), EnterpriseBrain unwraps `K_org_root` through AWS KMS SigV4 (`alias/knot-enterprisebrain-root`), then derives domain and subject keys locally via HKDF-SHA256 (`K_org_root -> K_domain -> K_subject`) with orthogonal `pgvector` Householder rotation per subject.

### How fast is subject deletion for GDPR or CCPA compliance?
Calling `knot_memory(action="erase", user_id=...)` invokes `DELETE /v1/subjects/{user_id}/key`, rotating or tombstoning the subject key epoch in `<100ms` so all historical ciphertext and rotated vectors become permanently unreadable without waiting for vacuum or vector index rebuilds.

### Does EnterpriseBrain require outbound internet access or external LLM calls?
No. Setting `KNOT_ALLOW_REMOTE_MODELS: "false"` disables all outbound model calls, telemetry, and license phone-home traffic. Reads execute deterministically in `<15ms` using compiled state and local ONNX embeddings.

### Does `KnotClient` allow plain HTTP URLs?
Only for loopback (`127.0.0.1`, `localhost`, `::1`) and private VPC-internal addresses (`.internal`, `.local`, `.svc`, `.cluster.local`, RFC1918 private IPs, or single-label Kubernetes service DNS). Public internet hosts require `https://`.

---

## Documentation and References

- [Knot Platform](https://connectknot.com)
- [Knot EnterpriseBrain (Self-Hosted VPC)](https://connectknot.com/enterprise-brain)
- [EnterpriseBrain VPC Reference](https://connectknot.com/enterprise-brain/reference)
- [CARMA-16 & Universal Context Schema Whitepaper](https://connectknot.com/whitepaper)
- [Full Machine-Readable Context Reference (`llms-full.txt`)](https://connectknot.com/llms-full.txt)

## License

MIT License. See [LICENSE](LICENSE).
