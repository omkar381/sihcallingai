from fastapi import APIRouter, HTTPException

from app.models.twin import TwinPatchRequest
from app.services.twin_service import get_recommendations, get_twin, patch_twin

router = APIRouter(tags=["digital-twin"])


@router.get("/api/farmer/{phone}/twin")
async def api_get_twin(phone: str):
    try:
        twin = get_twin(phone)
        return {"success": True, "twin": twin}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/api/farmer/{phone}/twin")
async def api_patch_twin(phone: str, payload: TwinPatchRequest):
    try:
        twin = patch_twin(phone, payload.model_dump(exclude_unset=True))
        return {"success": True, "twin": twin}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/farmer/{phone}/recommendations")
async def api_recommendations(phone: str):
    try:
        recommendations = await get_recommendations(phone)
        return {"success": True, **recommendations}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
