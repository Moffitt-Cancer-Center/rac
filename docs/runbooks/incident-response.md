# Incident Response Runbook

This runbook provides procedures for responding to operational incidents in RAC. As of Phase 1, core alerting is configured. Subsequent phases will refine these procedures with specific queries and remediation steps.

## Alert Categories

RAC monitors five critical areas. This runbook documents the response skeleton for each.

---

## Shim: 5xx Error Rate Spike

**Alert:** Shim returns 5xx status code > 1% of requests over 5-minute window.

**Severity:** 1 (page on-call)

### Triage

1. Confirm the alert in the Azure Portal:
   - Monitor → Alerts → Alert history
   - Filter by resource = `Shim ACA app` (Phase 6)
   - Note the timestamp and error rate percentage

2. Access Shim logs:
   ```bash
   # Logs are streamed to Log Analytics workspace
   # Phase 6 documents specific log table names
   az monitor log-analytics query \
     --workspace <WORKSPACE_ID> \
     --analytics-query "RAC_AccessLog_CL | where statusCode_d >= 500 | summarize count() by timestamp"
   ```

3. Check Shim deployment status:
   ```bash
   az containerapp replica list \
     --name shim-rac-<env> \
     --resource-group rg-rac-<env>
   ```

### Containment

- **Immediate:** If a recent deployment caused the spike, consider rolling back (documented in Phase 6).
- Confirm upstream dependencies (Key Vault, Postgres, Control Plane) are healthy.

### Recovery

- TODO: Phase 6 will provide specific recovery steps (e.g., auto-restart, traffic reroute).

### Post-Mortem

- Capture logs and incident timeline.
- Identify root cause: code bug, resource exhaustion, dependency failure, or transient infrastructure fault.
- Update future runbook sections with findings.

---

## Control Plane: 5xx Error Rate Spike

**Alert:** Control Plane returns 5xx status code > 1% of requests over 5-minute window.

**Severity:** 1 (page on-call)

### Triage

1. Confirm the alert in the Azure Portal.

2. Access Control Plane logs:
   ```bash
   # Phase 2 documents Control Plane logging
   az monitor log-analytics query \
     --workspace <WORKSPACE_ID> \
     --analytics-query "RAC_ControlPlaneLog_CL | where statusCode_d >= 500"
   ```

3. Check Control Plane deployment status:
   ```bash
   az containerapp replica list \
     --name control-plane-rac-<env> \
     --resource-group rg-rac-<env>
   ```

### Containment

- Verify Postgres, Key Vault, and storage are accessible.
- Check Control Plane managed identity permissions.

### Recovery

- TODO: Phase 2 will document Control Plane failure modes and recovery.

### Post-Mortem

- Document the root cause and update runbook.

---

## Postgres: Connection Failures

**Alert:** Postgres reports > 0 failed connection attempts over 5-minute window.

**Severity:** 1 (page on-call)

### Triage

1. Confirm the alert.

2. Test Postgres connectivity:
   ```bash
   # From a pod or local machine with network access to private endpoint
   pg_isready \
     --host <POSTGRES_FQDN> \
     --port 5432 \
     --username rac_admin
   ```

3. Check Postgres resource status:
   ```bash
   az postgres flexible-server show \
     --name rac-<env>-pg \
     --resource-group rg-rac-<env>
   ```

4. Review Postgres connection pool status:
   ```bash
   # TODO: Phase 2 documents connection pool monitoring in application logs
   ```

### Containment

- Confirm network routing to Postgres private endpoint is intact.
- Check if the Postgres private endpoint is in a healthy state.
- Verify application credentials are correct.

### Recovery

- If Postgres is unreachable, initiate failover if configured (prod only, Phase 1 uses single zone).
- TODO: Auto-failover and recovery procedures in Phase 5.

### Post-Mortem

- Identify whether it was a transient network issue or persistent failure.

---

## Key Vault: Access Denied

**Alert:** Key Vault reports > 0 access denied responses over 5-minute window.

**Severity:** 1 (page on-call)

### Triage

1. Confirm the alert.

2. Query Key Vault logs:
   ```bash
   az monitor log-analytics query \
     --workspace <WORKSPACE_ID> \
     --analytics-query "AzureDiagnostics | where ResourceType == 'VAULTS' and ResultSignature == 'Forbidden' | summarize count() by CallerIPAddress, OperationName"
   ```

3. Identify the actor:
   - Caller IP address from logs
   - Principal attempting access
   - Operation (secret read, key decrypt, certificate import, etc.)

### Containment

- If the caller is malicious, investigate unauthorized access attempt.
- If the caller is a service (Shim, Control Plane), check RBAC assignment.

### Recovery

- Verify the principal has the correct RBAC role:
  ```bash
  # Shim: Key Vault Crypto User
  # Control Plane: Key Vault Crypto Officer
  az role assignment list \
    --scope <KEY_VAULT_RESOURCE_ID> \
    --query "[?principalName == '<MANAGED_IDENTITY>']"
  ```
- If role is missing, add it (documented in Task 12B).

### Post-Mortem

- Log the unauthorized attempt details.
- If legitimate, add the missing RBAC assignment; if suspicious, escalate to security.

---

## Pipeline: Workflow Stuck

**Alert:** No pipeline verdict callback received for > 2× configured timeout (default 240 minutes).

**Severity:** 2 (on-call review, not page)

### Triage

1. Check submission in the Control Plane:
   ```bash
   # Phase 4 documents pipeline status monitoring
   # Query the submission approval_events table
   az monitor log-analytics query \
     --workspace <WORKSPACE_ID> \
     --analytics-query "RAC_ApprovalEvent_CL | where submission_id == '<SUBMISSION_ID>'"
   ```

2. Inspect the Build/Scan Pipeline (rac-pipeline repo):
   - GitHub Actions workflow runs
   - Logs for stuck steps (hung process, network timeout, resource exhaustion)

3. Check the webhook callback:
   ```bash
   # The pipeline posts results to Control Plane via webhook
   # Check Control Plane logs for callback receipt/failure
   az monitor log-analytics query \
     --workspace <WORKSPACE_ID> \
     --analytics-query "RAC_ControlPlaneLog_CL | where event_type == 'pipeline_callback'"
   ```

### Containment

- Investigate whether the pipeline is hung or the callback failed.
- If pipeline is running but callback didn't arrive, the network path is broken.
- If pipeline is hung, trigger a manual cancellation.

### Recovery

- TODO: Phase 3 documents pipeline failure recovery.
- Manual re-trigger if necessary.

### Post-Mortem

- Identify bottleneck: build step, scan step, network, or controller logic.
- Update pipeline timeout if routine submissions exceed current limit.

---

## Suspicious Token Activity

**Alert:** Detect unusual token issuance or validation patterns (Phase 7).

**Severity:** 1 (page on-call; security event)

### Triage

1. Query token activity in Entra logs:
   ```bash
   # Query Microsoft Entra activity in Azure AD logs
   # Phase 7 documents token monitoring
   az monitor log-analytics query \
     --workspace <WORKSPACE_ID> \
     --analytics-query "SigninLogs | where AppDisplayName == 'RAC Control Plane (OIDC)' and ConditionalAccessStatus == 'failure'"
   ```

2. Identify the actor:
   - User principal
   - IP address
   - Device
   - Grant flow (delegated vs client-credentials)

### Containment

- If a principal is compromised, revoke all tokens immediately (Phase 7 documents token revocation).
- If it's a bot/client credential abuse, disable the app registration.
- Escalate to security team.

### Recovery

- TODO: Phase 7 documents token management and revocation procedures.
- Reset compromised credentials.
- Review access logs for lateral movement.

### Post-Mortem

- Root cause: phishing, credential leak, or insider threat?
- Update security policies accordingly.

---

## Escalation

If you cannot resolve an incident within 30 minutes:

1. Page the on-call lead (distinct from the responder).
2. Involve the relevant team: DevOps (infrastructure), Backend (application), Security (authentication/authorization).
3. Document actions taken and escalation details.

## More Information

- **Alerts dashboard:** Azure Portal → Monitor → Alerts
- **Logs:** Log Analytics Workspace
- **Event Hub SIEM export:** See `docs/runbooks/siem-export.md`
- **Cost visibility:** See `docs/runbooks/cost-control.md`
- **Bootstrap steps:** See `docs/runbooks/bootstrap.md`
