"""Secret references.

There is no endpoint here that returns a secret value, and there is no field in
which one could be supplied. Registering a secret means pointing at one that
already exists in the workspace provider's secret store.
"""

from __future__ import annotations

from agentforge_shared.models import Agent, Project, SecretRef
from agentforge_shared.schemas import SecretRefCreate, SecretRefRead
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select

from ..deps import DbSession
from ..security import Principal, ensure_project_access, get_principal, require_write

router = APIRouter(tags=["secrets"])


@router.get("/secrets", response_model=list[SecretRefRead])
def list_global_secrets(
    session: DbSession, principal: Principal = Depends(get_principal)
) -> list[SecretRef]:
    """Global references only; project secrets are reached through their project."""
    return list(
        session.scalars(
            select(SecretRef).where(SecretRef.scope == "global").order_by(SecretRef.name)
        )
    )


@router.post("/secrets", response_model=SecretRefRead, status_code=status.HTTP_201_CREATED)
def create_global_secret(
    session: DbSession,
    payload: SecretRefCreate,
    principal: Principal = Depends(require_write),
) -> SecretRef:
    if payload.scope != "global":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "use /projects/{id}/secrets for project scope; this endpoint is global only",
        )
    return _create(session, payload)


@router.get("/projects/{project_id}/secrets", response_model=list[SecretRefRead])
def list_project_secrets(
    session: DbSession, project_id: str, principal: Principal = Depends(get_principal)
) -> list[SecretRef]:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    ensure_project_access(principal, project.id)

    return list(
        session.scalars(
            select(SecretRef)
            .where(
                or_(
                    SecretRef.scope == "global",
                    (SecretRef.scope == "project") & (SecretRef.project_id == project.id),
                )
            )
            .order_by(SecretRef.scope, SecretRef.name)
        )
    )


@router.post(
    "/projects/{project_id}/secrets",
    response_model=SecretRefRead,
    status_code=status.HTTP_201_CREATED,
)
def create_project_secret(
    session: DbSession,
    project_id: str,
    payload: SecretRefCreate,
    principal: Principal = Depends(require_write),
) -> SecretRef:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    ensure_project_access(principal, project.id)

    if payload.scope == "agent":
        if payload.agent_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "agent scope requires agent_id")
        agent = session.get(Agent, payload.agent_id)
        if agent is None or agent.project_id != project.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "agent does not belong to project")
    else:
        payload = payload.model_copy(update={"scope": "project"})

    payload = payload.model_copy(update={"project_id": project.id})
    return _create(session, payload)


@router.delete("/secrets/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_secret(
    session: DbSession, secret_id: str, principal: Principal = Depends(require_write)
) -> None:
    """Remove the reference. The underlying secret is someone else's to delete."""
    ref = session.get(SecretRef, secret_id)
    if ref is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "secret reference not found")
    if ref.project_id:
        ensure_project_access(principal, ref.project_id)
    session.delete(ref)
    session.commit()


def _create(session, payload: SecretRefCreate) -> SecretRef:
    existing = session.scalars(
        select(SecretRef).where(
            SecretRef.name == payload.name,
            SecretRef.scope == payload.scope,
            SecretRef.project_id == payload.project_id,
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"a {payload.scope} secret named {payload.name!r} already exists",
        )
    ref = SecretRef(**payload.model_dump())
    session.add(ref)
    session.commit()
    session.refresh(ref)
    return ref
