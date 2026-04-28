# Opportunities for Improvement: RAC Vulnerability & Lifecycle Management

Based on a review of the RAC architecture and design documents, the platform has a strong foundation for point-in-time checks (at submission) but has significant opportunities to improve its posture regarding **continuous** vulnerability management and **long-term** resource lifecycle.

Here are the key areas for improvement:

## 1. Continuous Vulnerability Scanning (Post-Deployment)
**Current State:** Vulnerability scanning (Grype + Defender for Containers) runs strictly as a point-in-time check during the initial `rac-pipeline` build process. The `SCAN_SEVERITY_GATE` is only evaluated before the application is deployed.
**The Gap:** If a new CVE is published months after an application is deployed, the platform does not automatically detect or act upon it. The deployed container remains available.
**Opportunity:** 
- **Event-Driven Rescanning:** Subscribe to Azure Event Grid notifications from Defender for Containers for continuous scan alerts on images residing in Azure Container Registry (ACR).
- **Automated Quarantine:** If a newly discovered CVE on a running app violates the `SCAN_SEVERITY_GATE`, automatically transition the app's status to a quarantined state (e.g., scale to zero, revoke tokens, and alert the IT approver and researcher).

## 2. Application Lifespan & Expiry Policies (TTL)
**Current State:** Applications scale to zero when idle (`min-replicas=0`) and reviewer JWT tokens expire. A nightly Graph sweep checks for deactivated Principal Investigators (PIs) and flags their apps.
**The Gap:** There is no maximum lifespan or TTL (Time-to-Live) for the applications themselves. Scientific demo apps may only be relevant for a conference or peer review window, but will currently sit in the environment indefinitely, consuming ACR storage, database records, and internal IP/DNS space.
**Opportunity:**
- **Configurable App TTLs:** Introduce a policy where apps expire after a set period (e.g., 1 or 2 years) unless explicitly renewed by the PI.
- **Idle Archival:** Implement a policy to automatically archive applications that haven't received any HTTP traffic (zero `access_log` entries) over a configurable threshold (e.g., 6 months). 

## 3. Base Image & Dependency Auto-Remediation
**Current State:** Researchers must manually submit a new Dockerfile/GitHub repository to fix vulnerabilities flagged by the pre-submission rules or the pipeline.
**The Gap:** Once deployed, there is no mechanism to patch or rebuild the container when new base image security patches are released.
**Opportunity:**
- **Automated Rebuilds:** Periodically trigger the `rac-pipeline` to rebuild the container from the original source if the underlying base image has been updated, ensuring the app benefits from OS-level security patches without researcher intervention.

## 4. Runtime Threat Detection & Drift
**Current State:** Security relies heavily on static image scanning prior to deployment and perimeter WAF (Azure Front Door/App Gateway).
**The Gap:** There is no explicit monitoring of the container's behavior at runtime (e.g., executing a newly downloaded binary, modifying sensitive files, or making unexpected outbound connections).
**Opportunity:**
- **Defender Runtime Protection:** Leverage Defender for Cloud's runtime protections (or an equivalent eBPF-based tool) to monitor the Azure Container Apps for anomalous behavior. Alerts could trigger automatic app suspension.

## 5. Container Image Artifact Lifecycle
**Current State:** Blob storage artifacts (SBOMs, build logs) are intelligently moved to Cool storage after 60 days and Archive after 365 days via lifecycle policies in Bicep.
**The Gap:** Azure Container Registry (ACR) images do not appear to have an expiration or retention policy. Over time, as researchers submit new versions or apps are abandoned, ACR storage costs and the platform's attack surface will grow unboundedly.
**Opportunity:**
- **ACR Retention Policies:** Implement an untagged manifest retention policy in ACR, and build a control plane job to automatically delete images belonging to applications that have been permanently archived or rejected.