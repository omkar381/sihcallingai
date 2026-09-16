"""Crop advisor API: photo diagnosis and the combined decision engine."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app import crop_doctor
from app.decision_engine import decide
from app.security import rate_limit_ai, rate_limit_queries

router = APIRouter(prefix="/api/advisor", tags=["advisor"])

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _image_mime(data: bytes) -> str | None:
    """Mime type from the file's own bytes; the browser-declared type is not trusted."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@router.post("/diagnose", dependencies=[Depends(rate_limit_ai)])
async def diagnose_photo(
    file: UploadFile = File(...),
    crop: str = Form(""),
    location: str = Form(""),
):
    """Diagnose disease, pests or deficiency from one leaf or crop photo."""
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The photo is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="The photo is larger than 8 MB.")
    mime = _image_mime(data)
    if mime is None:
        raise HTTPException(status_code=415, detail="Upload a JPG, PNG or WebP photo.")

    try:
        return await asyncio.to_thread(crop_doctor.diagnose, data, mime, crop.strip()[:60], location.strip()[:80])
    except crop_doctor.DiagnosisUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/diagnosis/{diagnosis_id}", dependencies=[Depends(rate_limit_queries)])
async def get_photo_diagnosis(diagnosis_id: str):
    result = crop_doctor.get_diagnosis(diagnosis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="That photo check has expired. Upload the photo again.")
    return result


@router.get("/decision", dependencies=[Depends(rate_limit_queries)])
async def get_decision(
    commodity: str = Query(..., min_length=1, max_length=60),
    location: str = Query("", max_length=80),
    quantity: float = Query(10.0, gt=0, le=100_000),
    storage_days: int = Query(0, ge=0, le=365),
    needs_cash: bool = False,
    cold_storage: bool = False,
    diagnosis_id: str = "",
):
    """One recommendation combining crop health, weather, soil, market and the knowledge base."""
    diagnosis = None
    if diagnosis_id:
        diagnosis = crop_doctor.get_diagnosis(diagnosis_id)
        if diagnosis is None:
            raise HTTPException(status_code=404, detail="That photo check has expired. Upload the photo again.")
    return await decide(
        commodity, location, quantity_qtl=quantity, storage_days=storage_days,
        needs_cash=needs_cash, cold_storage=cold_storage, diagnosis=diagnosis,
    )
