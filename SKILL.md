---
name: knot-strands-agents
description: Upgrade AWS Strands Agents SDK agents from flat memory (mem0_memory) to governed v1->v2 temporal context using knot_strands and Knot EnterpriseBrain on AWS EKS and Bedrock AgentCore.
---

# Knot Governance for AWS Strands Agents SDK

Use this skill when building, migrating, or hardening agents built with the **AWS Strands Agents SDK** (`strands-agents`) or **Amazon Bedrock AgentCore**.

## When to Apply

- Migrating off `mem0_memory` (deprecated and removed in `strands-agents-tools` v0.9.0) to `knot_memory` with deterministic `v1 -> v2` supersession.
- Preventing stale memory conflicts where agents retrieve outdated `v1` decisions alongside active `v2` overrides.
- Wiring Turn-0 deterministic context recovery (`KnotStrandsHookProvider`) before the first Bedrock model call.
- Deploying self-hosted **Knot EnterpriseBrain** inside an AWS EKS VPC with AWS KMS customer-managed keys (`alias/knot-enterprisebrain-root`), `KNOT_ALLOW_REMOTE_MODELS: "false"`, and Amazon Bedrock AgentCore Gateway.
- Enforcing PCI-DSS CDE write-door isolation (`HTTP 422 PCI_CDE_PAYLOAD_REJECTED`) and `<100ms` GDPR/CCPA subject crypto-erasure (`DELETE /v1/subjects/{user_id}/key`).

## Core Integration Pattern

1. Install zero-dependency core or full Strands SDK integration:
   ```bash
   pip install knot-strands-agents
   # Or with Strands Agents SDK:
   pip install "knot-strands-agents[strands]"
   ```
2. Attach tools and Turn-0 hook to `strands.Agent`:
   ```python
   from strands import Agent
   from knot_strands import (
       KnotClient,
       KnotStrandsHookProvider,
       knot_commit_decision,
       knot_get_active_state,
       knot_memory,
   )

   client = KnotClient()  # Reads KNOT_BASE_URL, KNOT_API_KEY, KNOT_DOMAIN
   agent = Agent(
       tools=[knot_memory, knot_commit_decision, knot_get_active_state],
       hooks=[KnotStrandsHookProvider(client=client, user_id="svc-orders", domain="checkout")],
   )
   ```
3. For multi-tenant production services, isolate principals with `KnotToolset(client=client, user_id=authenticated_user_id, domain=tenant_domain)` so `user_id` and `domain` are pinned by the operator and excluded from model-editable tool schemas.

## Key Files in This Repository

- `knot_strands/client.py`: Zero-dependency stdlib client supporting HTTPS and loopback/VPC internal URLs, sending `Authorization: Bearer ...` and `X-Knot-Domain`, and handling `HTTP 422 PCI_CDE_PAYLOAD_REJECTED` cleanly without crashing.
- `knot_strands/tools.py`: Strands `TOOL_SPEC`-compatible `knot_memory`, `knot_commit_decision`, `knot_get_active_state`, and multi-tenant `KnotToolset`.
- `knot_strands/hooks.py`: `KnotStrandsHookProvider` for Turn-0 `<15ms` context recovery with automatic graceful degradation when empty or offline.
- `deploy/aws-eks-agentcore/aws-eks-values.yaml`: EKS Helm preset with AWS KMS SigV4 3-level HKDF key hierarchy, `KNOT_ALLOW_REMOTE_MODELS: "false"`, Aurora `pgvector`, SQS ingest worker, and Bedrock AgentCore Gateway setup instructions.
