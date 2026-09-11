from typing import Optional, List
from pydantic import BaseModel, Field


class ListingCreateRequest(BaseModel):
    farmer_phone: str = Field(min_length=8)
    crop_name: str = Field(min_length=2, max_length=60)
    quantity: float = Field(gt=0)
    base_price_per_unit: float = Field(gt=0)
    expires_in_minutes: int = Field(default=30, ge=1, le=10080)


class BidCreateRequest(BaseModel):
    buyer_id: str = Field(min_length=4, max_length=32)
    bid_price_per_unit: float = Field(gt=0)
    quantity: float = Field(gt=0)


class AcceptBidRequest(BaseModel):
    bid_id: str = Field(min_length=8)
    farmer_phone: Optional[str] = None


class ListingCloseRequest(BaseModel):
    farmer_phone: Optional[str] = None


class ListingCompleteOrderRequest(BaseModel):
    farmer_phone: Optional[str] = None


class BidView(BaseModel):
    id: str
    listing_id: str
    buyer_id: str
    bid_price_per_unit: float
    quantity: float
    created_at: int


class ListingView(BaseModel):
    id: str
    farmer_phone: str
    crop_name: str
    quantity: float
    base_price_per_unit: float
    status: str
    expires_at: int
    created_at: int
    accepted_bid_id: Optional[str] = None
    buyer_id: Optional[str] = None
    sold_price_per_unit: Optional[float] = None
    sold_quantity: Optional[float] = None
    order_status: Optional[str] = None
    highest_bid: Optional[BidView] = None
    bids: List[BidView] = Field(default_factory=list)
