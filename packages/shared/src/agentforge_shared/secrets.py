"""Resolving secret references into something a provider can mount.

Resolution stops at the reference boundary. This module decides *which* secrets
a workspace may see; the provider decides *how* to make them visible. No
function here ever returns a secret value, because no secret value is ever
stored.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Agent, SecretRef
from .permissions import AgentPermissions
from .schemas import ResolvedSecret

log = logging.getLogger(__name__)


def resolve_secrets(
    session: Session,
    *,
    project_id: str,
    agent_id: str | None = None,
    provider: str = "kubernetes",
    include_global: bool = True,
    include_project: bool = True,
    include_agent: bool = True,
) -> list[ResolvedSecret]:
    """Collect the references a workspace is entitled to.

    Scope is additive but not transitive: a workspace sees global secrets, its
    own project's secrets, and (when provisioning for a specific agent) that
    agent's secrets. It never sees another project's, which is the property that
    stops one compromised agent from reading the whole estate.
    """
    clauses = []
    if include_global:
        clauses.append(SecretRef.scope == "global")
    if include_project:
        clauses.append((SecretRef.scope == "project") & (SecretRef.project_id == project_id))
    if include_agent and agent_id:
        clauses.append((SecretRef.scope == "agent") & (SecretRef.agent_id == agent_id))

    if not clauses:
        return []

    statement = select(SecretRef).where(SecretRef.provider == provider)
    from sqlalchemy import or_

    statement = statement.where(or_(*clauses))

    refs = session.scalars(statement.order_by(SecretRef.name)).all()
    return [
        ResolvedSecret(
            env_var=ref.env_var,
            secret_name=ref.secret_name,
            key=ref.key,
            required=ref.required,
        )
        for ref in refs
    ]


def effective_permissions(agent: Agent | None) -> AgentPermissions:
    """The policy actually in force for an agent, defaults filled in."""
    if agent is None:
        return AgentPermissions()
    return AgentPermissions.from_dict(agent.policy)
