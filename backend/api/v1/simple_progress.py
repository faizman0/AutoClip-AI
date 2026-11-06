"""
Simplified progress API - provides snapshot query endpoints
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
import logging

from backend.services.simple_progress import get_multiple_progress_snapshots, get_progress_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simple-progress", tags=["simple-progress"])


@router.get("/snapshot")
def get_progress_snapshots(project_ids: List[str] = Query(..., description="List of project IDs")):
    """
    Batch get project progress snapshots
    
    Args:
        project_ids: List of project IDs
        
    Returns:
        List of progress snapshots
    """
    try:
        if not project_ids:
            return []
            
        snapshots = get_multiple_progress_snapshots(project_ids)
        logger.info(f"Retrieved progress snapshots: {len(snapshots)} projects")
        return snapshots
        
    except Exception as e:
        logger.error(f"Failed to get progress snapshots: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get progress snapshots: {str(e)}")


@router.get("/snapshot/{project_id}")
def get_single_progress_snapshot(project_id: str):
    """
    Get a single project progress snapshot
    
    Args:
        project_id: Project ID
        
    Returns:
        Progress snapshot data
    """
    try:
        snapshot = get_progress_snapshot(project_id)
        if snapshot is None:
            # Return default state
            return {
                "project_id": project_id,
                "stage": "INGEST",
                "percent": 0,
                "message": "Waiting to start",
                "ts": 0
            }
            
        return snapshot
        
    except Exception as e:
        logger.error(f"Failed to get project progress snapshot: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get project progress snapshot: {str(e)}")


@router.get("/stages")
def get_available_stages():
    """
    Get available processing stage information
    
    Returns:
        Stage configuration information
    """
    from backend.services.simple_progress import STAGES, STAGE_NAMES
    
    stages_info = []
    for stage, weight in STAGES:
        stages_info.append({
            "stage": stage,
            "weight": weight,
            "display_name": STAGE_NAMES.get(stage, stage)
        })
    
    return {
        "stages": stages_info,
        "total_weight": sum(weight for _, weight in STAGES)
    }
