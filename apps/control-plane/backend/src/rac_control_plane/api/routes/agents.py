# pattern: Imperative Shell
"""Agent management API routes (admin-only).

All endpoints require admin role (settings.approver_role_it stand-in for v1).

Endpoints:
- POST /agents: Create an agent
- GET /agents: List all agents
- GET /agents/{id}: Get agent details
- PATCH /agents/{id}: Update an agent
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from rac_control_plane.api.schemas.agents import (
    AgentCreateRequest,
    AgentResponse,
    AgentUpdateRequest,
)
from rac_control_plane.auth.principal import Principal
from rac_control_plane.data.agent_repo import AgentRepo
from rac_control_plane.data.db import get_session

router = APIRouter(prefix="/agents", tags=["agents"])


async def require_admin(principal: Principal) -> Principal:
    """Dependency: require admin role.

    TODO: Once auth is wired, this will check principal.roles for admin.
    For now, it's a stub.
    """
    # TODO: Check principal.roles includes admin role
    # raise ForbiddenError if not
    return principal


@router.post("", status_code=201, response_model=AgentResponse)
async def create_agent(
    request: AgentCreateRequest,
    # TODO: current_principal() dependency from Task 5
    # _: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    """Create a new agent (admin-only).

    Args:
        request: Agent creation request
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
        service_principal_id=request.entra_app_id,  # TODO: Map from Entra
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
        metadata=agent.metadata,
        enabled=agent.enabled,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    # TODO: current_principal() dependency from Task 5
    # _: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AgentResponse]:
    """List all agents (admin-only).

    Args:
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
            metadata=agent.metadata,
            enabled=agent.enabled,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )
        for agent in agents
    ]


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: UUID,
    # TODO: current_principal() dependency from Task 5
    # _: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    """Get agent details (admin-only).

    Args:
        agent_id: UUID of the agent
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
        metadata=agent.metadata,
        enabled=agent.enabled,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: UUID,
    request: AgentUpdateRequest,
    # TODO: current_principal() dependency from Task 5
    # _: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AgentResponse:
    """Update an agent (admin-only).

    Args:
        agent_id: UUID of the agent
        request: Update request
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
        metadata=agent.metadata,
        enabled=agent.enabled,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )
