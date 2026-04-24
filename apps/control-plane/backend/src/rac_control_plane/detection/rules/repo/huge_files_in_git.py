# pattern: Functional Core
"""Rule: repo/huge_files_in_git — flags files >= threshold committed to the repo.

Large binary files in git increase clone time, consume GitHub LFS quota, and
bloat every developer's working copy. Data files should be referenced as
assets in rac.yaml and served via the asset pipeline instead.

Threshold is controlled by settings.detection_huge_file_threshold_bytes
(default 50 MiB).
"""

from __future__ import annotations

from collections.abc import Iterator

from rac_control_plane.detection.contracts import Finding, RepoContext, Rule


_DEFAULT_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50 MiB


def _evaluate(ctx: RepoContext, *, threshold_bytes: int | None = None) -> Iterator[Finding]:
    """Emit one Finding per file that exceeds the configured size threshold.

    Args:
        ctx: Immutable repository snapshot.
        threshold_bytes: Override the threshold (bytes). If None, reads from
            settings.detection_huge_file_threshold_bytes (default 50 MiB).
    """
    if threshold_bytes is None:
        try:
            from rac_control_plane.settings import get_settings
            threshold = get_settings().detection_huge_file_threshold_bytes
        except Exception:  # noqa: BLE001  # settings not configured in pure tests
            threshold = _DEFAULT_THRESHOLD_BYTES
    else:
        threshold = threshold_bytes

    for repo_file in ctx.files:
        if repo_file.size_bytes >= threshold:
            size_mb = repo_file.size_bytes / (1024 * 1024)
            yield Finding(
                rule_id="repo/huge_files_in_git",
                rule_version=1,
                severity="warn",
                title="Large file committed to git",
                detail=(
                    f"The file `{repo_file.path}` is {size_mb:.1f} MiB, which exceeds "
                    f"the {threshold / (1024 * 1024):.0f} MiB threshold. Large files "
                    "committed to git inflate clone and checkout times for every "
                    "developer and CI run. Consider declaring this as an asset in "
                    "`rac.yaml` and serving it via the RAC asset pipeline."
                ),
                file_path=repo_file.path,
                suggested_action="override",
            )


RULE = Rule(
    rule_id="repo/huge_files_in_git",
    version=1,
    default_severity="warn",
    evaluate=_evaluate,
)
