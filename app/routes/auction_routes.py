from fastapi import APIRouter, HTTPException, Query

from app.models.bidding import (
    AcceptBidRequest,
    BidCreateRequest,
    ListingCloseRequest,
    ListingCompleteOrderRequest,
    ListingCreateRequest,
)
from app.services import auction_service
from app.services.twin_service import refresh_twin_from_system

router = APIRouter(tags=["auction"])


@router.post("/api/listings/create")
async def api_create_listing(payload: ListingCreateRequest):
    try:
        listing = auction_service.create_listing(
            farmer_phone=payload.farmer_phone,
            crop_name=payload.crop_name,
            quantity=payload.quantity,
            base_price_per_unit=payload.base_price_per_unit,
            expires_in_minutes=payload.expires_in_minutes,
        )
        return {"success": True, "listing": listing}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/listings")
async def api_get_listings(
    status: str | None = Query(default=None),
    farmer_phone: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    try:
        listings = auction_service.get_listings(status=status, farmer_phone=farmer_phone, limit=limit)
        return {"success": True, "count": len(listings), "listings": listings}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/listings/{listing_id}")
async def api_get_listing(listing_id: str):
    listing = auction_service.get_listing_by_id(listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"success": True, "listing": listing}


@router.post("/api/listings/{listing_id}/bid")
async def api_place_bid(listing_id: str, payload: BidCreateRequest):
    try:
        bid = auction_service.place_bid(
            listing_id=listing_id,
            buyer_id=payload.buyer_id,
            bid_price_per_unit=payload.bid_price_per_unit,
            quantity=payload.quantity,
        )
        return {"success": True, "bid": bid}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/listings/{listing_id}/accept-bid")
async def api_accept_bid(listing_id: str, payload: AcceptBidRequest):
    try:
        listing = auction_service.accept_bid(
            listing_id=listing_id,
            bid_id=payload.bid_id,
            farmer_phone=payload.farmer_phone,
        )
        refresh_twin_from_system(listing["farmer_phone"])
        return {"success": True, "listing": listing}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/listings/{listing_id}/close")
async def api_close_listing(listing_id: str, payload: ListingCloseRequest):
    try:
        listing = auction_service.close_listing(
            listing_id=listing_id,
            farmer_phone=payload.farmer_phone,
        )
        return {"success": True, "listing": listing}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/listings/{listing_id}/complete-order")
async def api_complete_order(listing_id: str, payload: ListingCompleteOrderRequest):
    try:
        listing = auction_service.complete_order(
            listing_id=listing_id,
            farmer_phone=payload.farmer_phone,
        )
        return {"success": True, "listing": listing}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
