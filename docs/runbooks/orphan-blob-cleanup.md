# Runbook: Orphan Blob Cleanup

**Applies to:** `researcher-uploads` Blob container  
**Relevant infrastructure:** `infra/modules/blob-storage.bicep`

## Why orphan blobs occur

When a researcher requests a SAS token (`POST /submissions/{id}/assets/uploads/sas`), the
Control Plane mints a short-lived write-only SAS and returns it to the browser.  The browser
then PUTs the file directly to Azure Blob Storage.  If the researcher completes the upload
but never calls `/finalize` — for example because they closed the browser tab, navigated away,
or encountered an error — the blob exists in `researcher-uploads` with no corresponding `asset`
row in the database.

These blobs are harmless (they carry no sensitive data per RAC's public/synthetic data
policy) but accumulate storage costs and add noise to diagnostic queries.

## Automatic mitigation: lifecycle policy

The `researcher-uploads` container has a lifecycle management policy defined in
`infra/modules/blob-storage.bicep` (rule name: `delete-orphan-uploads-after-7-days`).  The
policy deletes any blob under the `submissions/` prefix whose last-modified timestamp is older
than 7 days.  No application code change is needed; Azure Storage enforces this nightly.

Verify the rule is active:

```bash
az storage account management-policy show \
  --account-name <storage_account_name> \
  --resource-group <resource_group>
```

## Diagnostic: list potential orphans

List all blobs in the `researcher-uploads` container under `submissions/`:

```bash
az storage blob list \
  --container-name researcher-uploads \
  --prefix submissions/ \
  --account-name <storage_account_name> \
  --output table
```

Cross-reference with the `asset` table in Postgres to identify blobs without a matching row:

```sql
SELECT blob_path FROM asset WHERE submission_id IS NOT NULL AND status = 'ready';
```

Any blob listed by the `az` command that does not appear in the query output is an orphan.

## Manual cleanup

To remove orphans for a specific submission:

```bash
az storage blob delete-batch \
  --source researcher-uploads \
  --pattern "submissions/<submission_id>/*" \
  --account-name <storage_account_name>
```

To remove all orphans older than N days (use with caution on production):

```bash
az storage blob delete-batch \
  --source researcher-uploads \
  --if-unmodified-since "$(date -u -d '-7 days' +%Y-%m-%dT%H:%MZ)" \
  --account-name <storage_account_name>
```

## Escalation

If orphan blobs accumulate faster than expected (e.g. because of a front-end bug causing
SAS minting without uploads), file an incident referencing this runbook and check the
`researcher-uploads` lifecycle policy is correctly targeted at the `submissions/` prefix.
