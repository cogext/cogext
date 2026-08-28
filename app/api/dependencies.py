"""V1.6 – Dependency graph API."""
import logging
import uuid

from fastapi import APIRouter, HTTPException

from app.core.dependency import add_dependency, get_dependency_graph, resolve_dependency
from app.models.dependency import AddDependencyRequest, CommitmentDependency, DependencyGraph

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/dependencies", response_model=CommitmentDependency)
async def create_dependency(body: AddDependencyRequest) -> CommitmentDependency:
    try:
        return await add_dependency(body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error("add_dependency failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create dependency")


@router.get("/commitments/{commitment_id}/dependencies", response_model=DependencyGraph)
async def get_dependencies(commitment_id: uuid.UUID) -> DependencyGraph:
    try:
        return await get_dependency_graph(commitment_id)
    except Exception as e:
        logger.error("get_dependency_graph failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch dependency graph")


@router.delete("/dependencies/{dependency_id}")
async def delete_dependency(dependency_id: uuid.UUID, actor: str = "api") -> dict:
    try:
        await resolve_dependency(dependency_id, actor=actor)
        return {"resolved": True, "dependency_id": str(dependency_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("resolve_dependency failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to resolve dependency")
