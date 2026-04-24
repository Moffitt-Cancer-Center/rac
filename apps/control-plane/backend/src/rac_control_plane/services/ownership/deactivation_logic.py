# pattern: Functional Core
"""Deactivation logic for nightly Graph sweep.

Pure: no I/O.  Given a list of app ownership records and a dict of
Graph lookup results, returns the apps whose PI is missing or disabled.

Verifies: rac-v1.AC9.2
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class AppOwnership:
    """Minimal app record carrying only the fields needed for sweep logic."""

    app_id: UUID
    app_slug: str
    pi_principal_id: UUID


@dataclass(frozen=True)
class GraphUserSnapshot:
    """Minimal Graph user snapshot: just what the sweep logic needs."""

    oid: UUID
    account_enabled: bool


@dataclass(frozen=True)
class FlaggedApp:
    """An app whose PI has been flagged by the sweep."""

    app_id: UUID
    app_slug: str
    pi_principal_id: UUID
    reason: Literal["not_found", "account_disabled"]


def compute_flagged_apps(
    apps: list[AppOwnership],
    graph_results: dict[UUID, GraphUserSnapshot | None],
) -> list[FlaggedApp]:
    """For each app whose PI's Graph user is missing OR has account_enabled=False,
    produce a FlaggedApp with the appropriate reason.

    Pure: no I/O.  Callers (Imperative Shell) are responsible for inserting the
    returned flags into the database.

    Args:
        apps: All AppOwnership records to check.
        graph_results: Mapping from pi_principal_id → GraphUserSnapshot or None.
                       A value of None means the user was not found in Graph.
                       Missing keys (PI not in graph_results) are treated as
                       not_found for safety — the caller should ensure all
                       pi_principal_ids in apps appear as keys in graph_results.

    Returns:
        List of FlaggedApp, one per app whose PI is inactive or absent.
        Apps with an active PI (account_enabled=True) are not included.
    """
    flagged: list[FlaggedApp] = []

    for app in apps:
        snapshot = graph_results.get(app.pi_principal_id)  # None if key absent

        if snapshot is None:
            flagged.append(
                FlaggedApp(
                    app_id=app.app_id,
                    app_slug=app.app_slug,
                    pi_principal_id=app.pi_principal_id,
                    reason="not_found",
                )
            )
        elif not snapshot.account_enabled:
            flagged.append(
                FlaggedApp(
                    app_id=app.app_id,
                    app_slug=app.app_slug,
                    pi_principal_id=app.pi_principal_id,
                    reason="account_disabled",
                )
            )

    return flagged
