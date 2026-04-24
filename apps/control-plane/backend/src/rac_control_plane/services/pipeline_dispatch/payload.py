# pattern: Functional Core
"""Build the client_payload dict for GitHub repository_dispatch.

Pure function — no I/O, no side effects, fully unit-testable.
The output shape must match the rac-pipeline workflow's expected fields.
"""

from typing import Any
from uuid import UUID

from rac_control_plane.data.models import Submission


def build_dispatch_payload(
    submission: Submission,
    *,
    callback_base_url: str,
    callback_secret_name: str,
) -> dict[str, Any]:
    """Build the client_payload dict for GitHub repository_dispatch.

    Args:
        submission: The created Submission ORM object (must have id, slug, etc.).
        callback_base_url: Control Plane base URL, e.g. "https://cp.rac.example.org".
        callback_secret_name: Key Vault secret name that stores the callback HMAC secret.

    Returns:
        dict matching the rac-pipeline workflow's expected fields:
        submission_id, repo_url, git_ref, dockerfile_path, slug,
        callback_url, callback_secret_name.
    """
    submission_id: UUID = submission.id
    return {
        "submission_id": str(submission_id),
        "repo_url": str(submission.github_repo_url),
        "git_ref": submission.git_ref,
        "dockerfile_path": submission.dockerfile_path,
        "slug": submission.slug,
        "callback_url": (
            f"{callback_base_url.rstrip('/')}"
            f"/webhooks/pipeline-callback/{submission_id}"
        ),
        "callback_secret_name": callback_secret_name,
    }
