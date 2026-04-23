# SIEM Export Surface (Event Hub)

RAC exports audit and operational events to Azure Event Hub for SIEM ingestion. This runbook documents the surface and how to subscribe a test consumer.

## Overview

The RAC platform generates two streams of append-only events:

1. **Access logs** — Shim HTTP request/response events
   - Hub: `eh-rac-access-logs`
   - Source: Shim container app (Phase 6)
   - Captured via: Log Analytics diagnostic settings forwarding from `RAC_AccessLog_CL` custom table

2. **Approval events** — Control Plane submission approval state transitions
   - Hub: `eh-rac-approval-events`
   - Source: Control Plane container app (Phase 2)
   - Captured via: Log Analytics diagnostic settings forwarding from `RAC_ApprovalEvent_CL` custom table

No application code changes are required to subscribe a consumer; all wiring happens in Bicep at infrastructure deployment time.

## Event Hub Infrastructure

**Namespace:** `evhns-rac-<env>` (Standard SKU, 1 throughput unit, 24-hour retention by default)

**Authorization Rule:** `Listen`-only credentials are stored in the platform Key Vault secret `eh-listener-connstring`. Only the listen key is exposed to external consumers; publish/manage keys remain internal.

## Consumer Prerequisites

To subscribe to the Event Hub, you need:

1. **Network access** to the Event Hub namespace (if the hub is behind a firewall, coordinate with the platform operator).
2. **Connection string** with Listen permission, stored in the platform Key Vault.
3. **Azure CLI** with the `eventhubs` extension.

### Retrieving the Connection String

```bash
# Log in to Azure with platform Owner/Contributor credentials
az login
az account set --subscription <DEV_SUBSCRIPTION_ID>

# Retrieve the connection string
az keyvault secret show \
  --vault-name kv-rac-dev \
  --name eh-listener-connstring \
  --query value \
  --output tsv
```

The connection string format is: `Endpoint=sb://<namespace>.servicebus.windows.net/;SharedAccessKeyName=Listen;SharedAccessKey=<key>;EntityPath=<hub-name>` (the EntityPath varies per hub).

## Connecting a Test Consumer

### Install Event Hubs Extension

```bash
az extension add --name eventhubs
```

### Peek at Access Logs (Shim Events)

```bash
az eventhubs eventhub message receive \
  --namespace-name evhns-rac-dev \
  --eventhub-name eh-rac-access-logs \
  --resource-group rg-rac-dev \
  --count 5
```

This command retrieves the 5 most recent messages from the access logs hub without advancing the consumer group position.

### Peek at Approval Events (Control Plane Events)

```bash
az eventhubs eventhub message receive \
  --namespace-name evhns-rac-dev \
  --eventhub-name eh-rac-approval-events \
  --resource-group rg-rac-dev \
  --count 5
```

### Create a Persistent Consumer Group (for SIEM)

To set up a persistent consumer group (e.g., for a SIEM polling system):

```bash
az eventhubs eventhub consumer-group create \
  --namespace-name evhns-rac-dev \
  --eventhub-name eh-rac-access-logs \
  --resource-group rg-rac-dev \
  --name siem-consumer
```

Then use the connection string and consumer group name in your SIEM ingestion tool (Splunk, DataDog, Sumo Logic, etc.).

## Event Schema

Both hubs emit JSON Lines format. Each line is a complete JSON object.

**Common fields (both hubs):**
- `timestamp` (ISO 8601 UTC): When the event occurred
- `correlation_id` (string, UUID): Trace ID linking related events
- `event_type` (string): Category (e.g., `access`, `approval_requested`, `approval_granted`)
- `app_slug` (string): Application identifier (null for platform events)
- `submission_id` (string, UUID): Submission reference (null for non-submission events)
- `actor_principal_id` (string, UUID): Entra principal ID of the actor

**Access log fields (additional):**
- `http_method` (string): GET, POST, PUT, etc.
- `request_path` (string): URI path
- `status_code` (int): HTTP status
- `response_time_ms` (int): Request latency
- `request_size_bytes` (int): Request body size
- `response_size_bytes` (int): Response body size

**Approval event fields (additional):**
- `old_state` (string): Previous approval state
- `new_state` (string): New approval state (e.g., submitted, approved, rejected)
- `reviewer_principal_id` (string, UUID): Principal ID of reviewer (null if actor is not a reviewer)

### Example Event

```json
{
  "timestamp": "2026-04-23T14:30:45.123Z",
  "correlation_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "event_type": "access",
  "app_slug": "myapp-slug",
  "submission_id": "550e8400-e29b-41d4-a716-446655440000",
  "actor_principal_id": "00000000-0000-0000-0000-000000000001",
  "http_method": "POST",
  "request_path": "/api/v1/submissions",
  "status_code": 201,
  "response_time_ms": 234,
  "request_size_bytes": 1024,
  "response_size_bytes": 512
}
```

**Schema subject to extension in Phase 5 (approval workflows) and Phase 6 (Shim) but the core fields above are stable.**

## Quota and Retention

**Standard SKU (default):**
- 1 throughput unit = 1 MB/sec ingress, 2 MB/sec egress
- 24-hour message retention by default
- Scales to 20 TU on a single namespace

To increase throughput units or retention:

```bash
# Increase to 2 throughput units
az eventhubs namespace update \
  --resource-group rg-rac-dev \
  --name evhns-rac-dev \
  --capacity 2

# Increase retention (requires no message data loss)
az eventhubs eventhub update \
  --namespace-name evhns-rac-dev \
  --name eh-rac-access-logs \
  --resource-group rg-rac-dev \
  --message-retention 72  # 72 hours
```

**No Bicep changes required** — these are operational configuration changes.

## Acceptance Test for AC10.5

After Phase 2 is deployed to dev:

1. Trigger a submission in the Control Plane (Phase 2 provides a test endpoint or UI).

2. Wait up to 5 minutes for Log Analytics to ingest the event into `RAC_ApprovalEvent_CL` (there is inherent latency).

3. Run the peek command:
   ```bash
   az eventhubs eventhub message receive \
     --namespace-name evhns-rac-dev \
     --eventhub-name eh-rac-approval-events \
     --resource-group rg-rac-dev \
     --count 5
   ```

4. Confirm that at least one JSON event appears in the output with the `submission_id` matching your test submission.

5. Record the event JSON in the Phase 1 acceptance report.

If no events arrive after 10 minutes:

- Check that Log Analytics diagnostic settings are configured:
  ```bash
  az monitor diagnostic-settings list \
    --resource <LOG_ANALYTICS_WORKSPACE_RESOURCE_ID>
  ```
- Confirm the Control Plane container app is running and emitting structured logs (Phase 2 documents logging setup).
- Check Event Hub capacity hasn't been throttled (inspect metrics in Portal).

## Troubleshooting

**Connection refused to Event Hub**
- Verify the namespace exists and is in a healthy state.
- Confirm network firewall rules allow access (Event Hub uses port 5671 for AMQP, 443 for HTTPS).
- Ensure the connection string is not expired.

**No messages in the hub**
- Check that the source application (Shim/Control Plane) is running.
- Verify diagnostic settings on the Log Analytics workspace are routing to the Event Hub.
- Ensure the custom tables (`RAC_AccessLog_CL`, `RAC_ApprovalEvent_CL`) are receiving data.

**Consumer group offset tracking fails**
- Some SIEM tools require blob storage for consumer group offset checkpointing. This is operator-configured outside Bicep.
- Ensure the SIEM tool has access to the blob storage account designated for checkpoints.

## Further Reading

- **Azure Event Hubs documentation:** https://docs.microsoft.com/en-us/azure/event-hubs/
- **Consumer group offsets:** https://docs.microsoft.com/en-us/azure/event-hubs/event-hubs-features#consumer-groups
- **Diagnostic settings:** https://docs.microsoft.com/en-us/azure/monitor/essentials/diagnostic-settings
