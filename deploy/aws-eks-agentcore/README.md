# Deploying Knot EnterpriseBrain on AWS EKS for Strands Agents and Bedrock AgentCore

This guide explains how to run **Knot EnterpriseBrain** inside your own AWS VPC on Amazon EKS with AWS KMS key custody, zero outbound model egress (`KNOT_ALLOW_REMOTE_MODELS: "false"`), and Amazon Bedrock AgentCore Gateway connectivity.

## 1. Prerequisites

- Amazon EKS cluster with IAM Roles for Service Accounts (IRSA) enabled
- Amazon Aurora PostgreSQL or RDS PostgreSQL with `pgvector`
- AWS KMS customer-managed key (`alias/knot-enterprisebrain-root`)
- AWS PrivateLink / VPC Lattice endpoints for KMS and SQS (required when `KNOT_ALLOW_REMOTE_MODELS: "false"`)
- `DATABASE_URL` and offline Ed25519 `KNOT_ORG_LICENSE` stored in Kubernetes Secret `knot-enterprisebrain-secrets`

## 2. Install via Helm

```bash
helm upgrade --install knot-enterprisebrain oci://ghcr.io/connectknot/charts/knot-enterprisebrain \
  --namespace knot-system --create-namespace \
  -f deploy/aws-eks-agentcore/aws-eks-values.yaml
```

Run the built-in preflight verifier inside the API pod:

```bash
kubectl -n knot-system exec deploy/knot-enterprisebrain-api -- knot-enterprisebrain --doctor
```

## 3. Register with Amazon Bedrock AgentCore Gateway

1. Point an internal ALB or PrivateLink VPC service to `knot-internal.your-vpc.internal`.
2. In **Amazon Bedrock AgentCore Gateway**, register an MCP target:
   - **Target Type**: MCP Server (Streamable HTTP)
   - **Endpoint**: `https://knot-internal.your-vpc.internal/mcp`
   - **Authentication**: OAuth 2.1 JWKS or mTLS (`X-Forwarded-Client-Cert`)
3. Configure `knot-policy.yaml` domain authority ranks so Strands agents in customer-facing channels cannot overwrite higher-authority billing, legal, or platform domains.

## 4. Connect Strands Agents SDK (`knot_strands`)

Inside EKS pods, Lambda functions, or Bedrock AgentCore Runtime containers within your VPC:

```bash
export KNOT_BASE_URL="https://knot-internal.your-vpc.internal/v1"
export KNOT_API_KEY="your-service-or-oidc-token"
export KNOT_DOMAIN="enterprise-platform"
```

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
    hooks=[KnotStrandsHookProvider(client=client, user_id="svc-orders", domain="enterprise-platform")],
)
```

## References

- Knot EnterpriseBrain Overview: https://connectknot.com/enterprise-brain
- EnterpriseBrain VPC Reference: https://connectknot.com/enterprise-brain/reference
- Knot Whitepaper (CARMA-16 and UCS): https://connectknot.com/whitepaper
