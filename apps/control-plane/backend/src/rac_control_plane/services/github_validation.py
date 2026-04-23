# pattern: Imperative Shell
"""GitHub repository validation service.

Validates that a GitHub repository exists and contains the specified Dockerfile.
"""


import httpx
from pydantic import HttpUrl

from rac_control_plane.errors import ValidationApiError


async def validate_repo(
    url: HttpUrl,
    ref: str,
    dockerfile_path: str,
    github_token: str | None = None,
) -> None:
    """Validate GitHub repository and Dockerfile existence.

    Args:
        url: GitHub HTTPS URL (e.g., https://github.com/owner/repo)
        ref: Git reference (branch/tag/commit)
        dockerfile_path: Path to Dockerfile in the repo
        github_token: Optional GitHub PAT for higher rate limits

    Raises:
        ValidationApiError: If repository not found or Dockerfile missing
    """
    # Parse GitHub URL to extract owner/repo
    url_str = str(url).rstrip("/")
    if not url_str.startswith("https://github.com/"):
        raise ValidationApiError(
            code="invalid_github_url",
            public_message=f"GitHub URL must start with https://github.com/: {url_str}",
        )

    # Extract owner/repo from URL
    parts = url_str.replace("https://github.com/", "").split("/")
    if len(parts) < 2:
        raise ValidationApiError(
            code="invalid_github_url",
            public_message=f"Invalid GitHub URL format: {url_str}",
        )

    owner = parts[0]
    repo = parts[1].rstrip(".git")  # Handle .git suffix

    # Prepare API headers
    headers = {}
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    # Check if repository exists (HEAD request)
    api_url = f"https://api.github.com/repos/{owner}/{repo}"

    timeout = httpx.Timeout(5.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        # Check repo existence
        try:
            repo_response = await client.head(api_url, headers=headers)
        except httpx.TimeoutException as e:
            raise ValidationApiError(
                code="github_timeout",
                public_message=f"Timeout validating GitHub repository: {url}",
            ) from e
        except httpx.RequestError as e:
            raise ValidationApiError(
                code="github_error",
                public_message=f"Error validating GitHub repository: {url}",
            ) from e

        if repo_response.status_code == 404:
            raise ValidationApiError(
                code="github_not_found",
                public_message=f"Repository not found at {url} or reference '{ref}' does not exist",
            )

        if repo_response.status_code >= 400:
            raise ValidationApiError(
                code="github_error",
                public_message=f"Error accessing repository: {url}",
            )

        # Check if Dockerfile exists at the specified path
        dockerfile_api_url = f"{api_url}/contents/{dockerfile_path}"
        params = {"ref": ref}

        try:
            dockerfile_response = await client.get(
                dockerfile_api_url,
                headers=headers,
                params=params,
            )
        except httpx.TimeoutException as e:
            raise ValidationApiError(
                code="github_timeout",
                public_message=f"Timeout validating Dockerfile: {dockerfile_path}@{ref}",
            ) from e
        except httpx.RequestError as e:
            raise ValidationApiError(
                code="github_error",
                public_message=f"Error validating Dockerfile: {dockerfile_path}@{ref}",
            ) from e

        if dockerfile_response.status_code == 404:
            raise ValidationApiError(
                code="dockerfile_not_found",
                public_message=f"Dockerfile not found at {dockerfile_path} in {url}@{ref}",
            )

        if dockerfile_response.status_code >= 400:
            raise ValidationApiError(
                code="github_error",
                public_message=f"Error accessing Dockerfile: {dockerfile_path}",
            )
