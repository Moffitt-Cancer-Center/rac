# pattern: Functional Core
"""Tag builder for Tier 3 Azure resources.

Pure function that produces the required AC11.1 tags for all provisioned resources.
"""

from __future__ import annotations

from uuid import UUID


def build_tier3_tags(
    slug: str,
    pi_principal_id: UUID,
    submission_id: UUID,
    env: str,
) -> dict[str, str]:
    """Build AC11.1-required tags for every Tier 3 Azure resource.

    Args:
        slug: Application slug (unique identifier for the app).
        pi_principal_id: UUID of the PI who owns the submission.
        submission_id: UUID of the approved submission.
        env: Deployment environment ('dev', 'staging', 'prod').

    Returns:
        Dict of tag key/value pairs. All values are strings.
    """
    return {
        "rac_env": env,
        "rac_app_slug": slug,
        "rac_pi_principal_id": str(pi_principal_id),
        "rac_submission_id": str(submission_id),
        "rac_managed_by": "control-plane",
    }
