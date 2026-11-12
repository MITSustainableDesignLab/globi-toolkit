"""Willingness to pay API: the model (use-cases/wtp.yml), its area tables, and its check."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from dependencies import get_workspace
from services.workspace import ProjectWorkspace
from services.wtp import WTPError, load_example, save_table, save_wtp, wtp_status

router = APIRouter(prefix="/api/wtp", tags=["wtp"])


@router.get("")
async def get_wtp(workspace: ProjectWorkspace = Depends(get_workspace)) -> dict[str, Any]:
    """The saved model, the columns and tables it can use, and what's still missing."""
    return wtp_status(workspace)


@router.put("")
async def put_wtp(body: dict[str, Any], workspace: ProjectWorkspace = Depends(get_workspace)) -> dict[str, Any]:
    """Save the model as it stands (incomplete is fine) and check it again."""
    return save_wtp(workspace, body)


@router.post("/tables")
async def post_table(file: UploadFile, workspace: ProjectWorkspace = Depends(get_workspace)) -> dict[str, Any]:
    """Add an area table (CSV, one row per area)."""
    try:
        return save_table(workspace, file.filename or "", await file.read())
    except WTPError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/example")
async def post_example(workspace: ProjectWorkspace = Depends(get_workspace)) -> dict[str, Any]:
    """Start from the worked example (Massachusetts, Berzolla et al. 2025)."""
    return load_example(workspace)
