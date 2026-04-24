# RAC — Research Application Commons

RAC (Research Application Commons) is a secure, single-tenant-per-deployment platform for hosting researcher-submitted applications and data analysis workloads on Azure. The platform enforces approval workflows, cost attribution, and comprehensive security controls (network isolation, RBAC, encryption, WAF) while allowing researchers to deploy custom containerized code against their own datasets.

**Design Plan:** [RAC v1 Architecture](docs/design-plans/2026-04-23-rac-v1.md)

**Getting Started:** [Bootstrap Runbook](docs/runbooks/bootstrap.md)

## Prerequisites

- **Azure CLI** ≥ 2.86
- **Bicep CLI** ≥ 0.31
- **Python** 3.12
- **Node** 20
- **pnpm** ≥ 9

## Directory Map

- **`apps/`** — Application code (Control Plane, Shim, Pipeline)
- **`infra/`** — Bicep Infrastructure-as-Code (modules, main, parameter files)
- **`docs/`** — Design plans, runbooks, architecture diagrams
- **`.github/`** — GitHub Actions workflows (infra deploy, application CI/CD)
