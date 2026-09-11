from fastapi import APIRouter, HTTPException, Query

from app.services import auction_service

router = APIRouter(tags=["seller-dashboard"])


@router.get("/api/seller/{phone}/dashboard")
async def api_seller_dashboard(phone: str):
    try:
        return {"success": True, **auction_service.get_seller_dashboard(phone)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/seller/{phone}/listings")
async def api_seller_listings(phone: str, status: str | None = Query(default=None)):
    try:
        listings = auction_service.get_seller_listings(phone=phone, status=status)
        return {"success": True, "count": len(listings), "listings": listings}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/seller/{phone}/revenue")
async def api_seller_revenue(phone: str):
    try:
        return {"success": True, **auction_service.get_seller_revenue(phone)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
