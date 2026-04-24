# pattern: Imperative Shell
"""Agent management API routes (admin-only).

All endpoints require admin role (settings.approver_role_it stand-in for v1).

Endpoints:
- POST /agents: Create an agent
- GET /agents: List all agents
- GET /agents/{id}: Get agent details
- PATCH /agents/{id}: Update an agent
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.agents import (
    AgentCreateRequest,
    AgentResponse,
    AgentUpdateRequest,
)
from rac_control_plane.auth.dependencies import require_admin
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.agent_repo import AgentRepo
from rac_control_plane.data.db import get_session

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", status_code=201, response_model=AgentResponse)
async def create_agent(
    request: AgentCreateRequest,
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    """Create a new agent (admin-only).

    Args:
        request: Agent creation request
        principal: Current admin principal
        session: Database session

    Returns:
        Created agent with 201 status

    Raises:
        403: Not an admin
    """
    repo = AgentRepo(session)

    agent = await repo.create_agent(
        name=request.name,
        kind=request.kind,
        entra_app_id=str(request.entra_app_id),
        service_principal_id=request.entra_app_id,  # Map from Entra
        metadata=request.metadata,
        enabled=request.enabled,
    )

    await session.commit()

    return AgentResponse(
        id=agent.id,
        name=agent.name,
        kind=agent.kind,
        entra_app_id=agent.entra_app_id,
        service_principal_id=agent.service_principal_id,
        metadata=agent.agent_metadata,
        enabled=agent.enabled,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> list[AgentResponse]:
    """List all agents (admin-only).

    Args:
        principal: Current admin principal
        session: Database session

    Returns:
        List of agents

    Raises:
        403: Not an admin
    """
    repo = AgentRepo(session)
    agents = await repo.list_agents()

    return [
        AgentResponse(
            id=agent.id,
            name=agent.name,
            kind=agent.kind,
            entra_app_id=agent.entra_app_id,
            service_principal_id=agent.service_principal_id,
            metadata=agent.agent_metadata,
            enabled=agent.enabled,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )
        for agent in agents
    ]


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: UUID,
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    """Get agent details (admin-only).

    Args:
        agent_id: UUID of the agent
        principal: Current admin principal
        session: Database session

    Returns:
        Agent details

    Raises:
        404: Agent not found
        403: Not an admin
    """
    repo = AgentRepo(session)
    agent = await repo.get_by_id(agent_id)

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return AgentResponse(
        id=agent.id,
        name=agent.name,
        kind=agent.kind,
        entra_app_id=agent.entra_app_id,
        service_principal_id=agent.service_principal_id,
        metadata=agent.agent_metadata,
        enabled=agent.enabled,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: UUID,
    request: AgentUpdateRequest,
    principal: Annotated[Principal, Depends(require_admin)],
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    """Update an agent (admin-only).

    Args:
        agent_id: UUID of the agent
        request: Update request
        principal: Current admin principal
        session: Database session

    Returns:
        Updated agent

    Raises:
        404: Agent not found
        403: Not an admin
    """
    repo = AgentRepo(session)
    agent = await repo.update_agent(
        agent_id,
        name=request.name,
        enabled=request.enabled,
        metadata=request.metadata,
    )

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    await session.commit()

    return AgentResponse(
        id=agent.id,
        name=agent.name,
        kind=agent.kind,
        entra_app_id=agent.entra_app_id,
        service_principal_id=agent.service_principal_id,
        metadata=agent.agent_metadata,
        enabled=agent.enabled,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )
