"""V1.6 – Dependency graph API."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import Account, get_current_account
from app.core.dependency import add_dependency, get_dependency_graph, resolve_dependency
from app.core.ownership import get_owned_commitment
from app.db.connection import get_supabase
from app.models.dependency import AddDependencyRequest, CommitmentDependency, DependencyGraph

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/dependencies", response_model=CommitmentDependency)
async def create_dependency(
    body: AddDependencyRequest,
    account: Account = Depends(get_current_account),
) -> CommitmentDependency:
    # Both ends must belong to the caller (404 otherwise): a "blocks" edge
    # changes the target's status.
    sb = get_supabase()
    await get_owned_commitment(sb, body.source_commitment_id, account, "id")
    await get_owned_commitment(sb, body.target_commitment_id, account, "id")
    try:
        return await add_dependency(body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error("add_dependency failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create dependency")


@router.get("/commitments/{commitment_id}/dependencies", response_model=DependencyGraph)
async def get_dependencies(
    commitment_id: uuid.UUID,
    account: Account = Depends(get_current_account),
) -> DependencyGraph:
    # The caller must own the commitment whose graph is read (404 otherwise)
    await get_owned_commitment(get_supabase(), commitment_id, account, "id")
    try:
        return await get_dependency_graph(commitment_id)
    except Exception as e:
        logger.error("get_dependency_graph failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch dependency graph")


@router.delete("/dependencies/{dependency_id}")
async def delete_dependency(
    dependency_id: uuid.UUID,
    actor: str = "api",
    account: Account = Depends(get_current_account),
) -> dict:
    # Resolving can unblock the target commitment, so the caller must own the
    # target. Another account's dependency gets the same 404 as a missing one.
    not_found = HTTPException(status_code=404, detail=f"Dependency {dependency_id} not found")
    sb = get_supabase()
    dep = await sb.table("commitment_dependencies").select("target_commitment_id").eq(
        "id", str(dependency_id)
    ).execute()
    if not dep.data:
        raise not_found
    try:
        await get_owned_commitment(sb, dep.data[0]["target_commitment_id"], account, "id")
    except HTTPException:
        raise not_found from None
    try:
        await resolve_dependency(dependency_id, actor=actor)
        return {"resolved": True, "dependency_id": str(dependency_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("resolve_dependency failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to resolve dependency")
