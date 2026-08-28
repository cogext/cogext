"""V1.6 – Commitment dependency graph with cycle detection."""
import logging
import uuid
from typing import Any

from app.db.connection import get_supabase
from app.models.dependency import AddDependencyRequest, CommitmentDependency, DependencyGraph

logger = logging.getLogger(__name__)


async def add_dependency(req: AddDependencyRequest) -> CommitmentDependency:
    """Add a dependency edge, rejecting if it would create a cycle."""
    sb = get_supabase()

    if req.source_commitment_id == req.target_commitment_id:
        raise ValueError("A commitment cannot depend on itself")

    # Cycle detection: would target_commitment_id become an ancestor of source_commitment_id?
    if await _would_create_cycle(
        sb, str(req.source_commitment_id), str(req.target_commitment_id)
    ):
        raise ValueError(
            f"Adding dependency {req.source_commitment_id} → {req.target_commitment_id} "
            "would create a circular dependency"
        )

    dep_id = uuid.uuid4()
    await sb.table("commitment_dependencies").insert({
        "id": str(dep_id),
        "source_commitment_id": str(req.source_commitment_id),
        "target_commitment_id": str(req.target_commitment_id),
        "dependency_type": req.dependency_type,
    }).execute()

    # If type is "blocks", transition target to "blocked"
    if req.dependency_type == "blocks":
        try:
            from app.core.state_machine import transition_commitment
            await transition_commitment(
                req.target_commitment_id,
                "blocked",
                actor="dependency_engine",
                data={"blocked_by": str(req.source_commitment_id)},
            )
        except Exception as e:
            logger.warning("Could not block commitment %s: %s", req.target_commitment_id, e)

    resp = await sb.table("commitment_dependencies").select("*").eq(
        "id", str(dep_id)
    ).execute()
    return CommitmentDependency.model_validate(resp.data[0])


async def resolve_dependency(dependency_id: uuid.UUID, actor: str = "system") -> None:
    """Mark a dependency resolved; unblock target if all blockers are gone."""
    sb = get_supabase()

    dep_resp = await sb.table("commitment_dependencies").select("*").eq(
        "id", str(dependency_id)
    ).execute()
    if not dep_resp.data:
        raise ValueError(f"Dependency {dependency_id} not found")

    dep = dep_resp.data[0]
    from datetime import datetime, timezone
    await sb.table("commitment_dependencies").update({
        "resolved_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", str(dependency_id)).execute()

    # If this was a "blocks" dependency, check if target is now unblocked
    if dep["dependency_type"] == "blocks":
        target_id = dep["target_commitment_id"]
        remaining = await sb.table("commitment_dependencies").select("id").eq(
            "target_commitment_id", target_id
        ).eq("dependency_type", "blocks").is_("resolved_at", "null").execute()

        if not remaining.data:
            try:
                from app.core.state_machine import transition_commitment
                await transition_commitment(
                    uuid.UUID(target_id),
                    "open",
                    actor=actor,
                    data={"unblocked_by": str(dependency_id)},
                )
            except Exception as e:
                logger.warning("Could not unblock commitment %s: %s", target_id, e)


async def get_dependency_graph(commitment_id: uuid.UUID) -> DependencyGraph:
    sb = get_supabase()
    cid = str(commitment_id)

    blockers_resp = await sb.table("commitment_dependencies").select("*").eq(
        "target_commitment_id", cid
    ).execute()
    blocking_resp = await sb.table("commitment_dependencies").select("*").eq(
        "source_commitment_id", cid
    ).execute()

    return DependencyGraph(
        commitment_id=commitment_id,
        blockers=[CommitmentDependency.model_validate(r) for r in blockers_resp.data],
        blocking=[CommitmentDependency.model_validate(r) for r in blocking_resp.data],
    )


async def _would_create_cycle(sb: Any, source_id: str, target_id: str) -> bool:
    """BFS from target — if we can reach source, adding source→target creates a cycle."""
    visited: set[str] = set()
    frontier = [target_id]

    while frontier:
        node = frontier.pop()
        if node == source_id:
            return True
        if node in visited:
            continue
        visited.add(node)

        # Nodes that 'node' points to (outbound edges from node)
        resp = await sb.table("commitment_dependencies").select(
            "target_commitment_id"
        ).eq("source_commitment_id", node).is_("resolved_at", "null").execute()
        frontier.extend(r["target_commitment_id"] for r in (resp.data or []))

    return False
