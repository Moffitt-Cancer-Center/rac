# pattern: Imperative Shell
"""Agent repository: database access for Agent records."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.data.db import get_session
from rac_control_plane.data.models import Agent


class AgentRepo:
    """Agent data access."""

    def __init__(self, session: Annotated[AsyncSession, Depends(get_session)]) -> None:
        """Initialize with a database session."""
        self.session = session

    async def get_by_entra_app_id(self, entra_app_id: str) -> Agent | None:
        """Look up agent by Entra app ID.

        Args:
            entra_app_id: UUID string of the Entra app.

        Returns:
            Agent record or None if not found.
        """
        stmt = select(Agent).where(Agent.entra_app_id == entra_app_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, agent_id: UUID) -> Agent | None:
        """Look up agent by database ID.

        Args:
            agent_id: UUID of the agent record.

        Returns:
            Agent record or None if not found.
        """
        stmt = select(Agent).where(Agent.id == agent_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_agents(self) -> list[Agent]:
        """List all agents.

        Returns:
            List of Agent records.
        """
        stmt = select(Agent).order_by(Agent.created_at)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_agent(
        self,
        name: str,
        kind: str,
        entra_app_id: str,
        service_principal_id: UUID,
        metadata: dict | None = None,
        enabled: bool = True,
    ) -> Agent:
        """Create a new agent.

        Args:
            name: Human-readable agent name.
            kind: Agent kind (e.g., 'ui', 'servicenow', 'cli', 'other').
            entra_app_id: Entra application ID.
            service_principal_id: Entra service principal ID.
            metadata: Optional JSONB metadata.
            enabled: Whether the agent is enabled.

        Returns:
            Created Agent record.
        """
        agent = Agent(
            name=name,
            kind=kind,
            entra_app_id=entra_app_id,
            service_principal_id=service_principal_id,
            metadata=metadata,
            enabled=enabled,
        )
        self.session.add(agent)
        await self.session.flush()
        return agent
