from typing import List, Optional
from pydantic import BaseModel, Field


class TwinPatchRequest(BaseModel):
    crops_grown: Optional[List[str]] = None
    land_size: Optional[float] = Field(default=None, gt=0)
    soil_type: Optional[str] = None
    water_source: Optional[str] = None
    avg_yield: Optional[float] = Field(default=None, ge=0)
    preferred_crops: Optional[List[str]] = None
    risk_score: Optional[float] = Field(default=None, ge=0, le=100)


class TwinView(BaseModel):
    farmer_phone: str
    crops_grown: List[str]
    land_size: Optional[float]
    soil_type: str
    water_source: str
    avg_yield: float
    preferred_crops: List[str]
    risk_score: float
    last_updated: int


class TwinRecommendations(BaseModel):
    farmer_phone: str
    generated_at: int
    recommendations: List[str]
    ai_summary: str
