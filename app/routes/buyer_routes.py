from fastapi import APIRouter, HTTPException

from app.services import auction_service

router = APIRouter(tags=["buyer-dashboard"])


@router.get("/api/buyer/{phone}/dashboard")
async def api_buyer_dashboard(phone: str):
    try:
        return {"success": True, **auction_service.get_buyer_dashboard(phone)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/buyer/{phone}/bids")
async def api_buyer_bids(phone: str):
    try:
        bids = auction_service.get_buyer_bids(phone)
        return {"success": True, "count": len(bids), "bids": bids}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
