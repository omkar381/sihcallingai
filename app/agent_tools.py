import logging
import re
import time
from html import unescape
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

# Mock Data / Tools for Gemini Agent

import httpx

_MARKET_CACHE_TTL_SECONDS = 300
_market_price_cache: Dict[str, Dict[str, Any]] = {}


def _normalize_crop_slug(crop: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", crop.lower()).strip("-")
    aliases = {
        "onions": "onion",
        "tomatoes": "tomato",
        "potatoes": "potato",
        "chilies": "chilli",
        "chillies": "chilli",
        "corn": "maize",
    }
    return aliases.get(slug, slug or "onion")


def _clean_html_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_latest_kalaburgi_row(html: str, crop_slug: str) -> Dict[str, Any] | None:
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL)

    for row in rows:
        cells_raw = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL)
        if len(cells_raw) < 8:
            continue

        cells = [_clean_html_text(cell) for cell in cells_raw]
        row_text = " ".join(cells).lower()
        if crop_slug not in row_text:
            continue

        date_match = next((c for c in cells if re.search(r"\b\d{2}/\d{2}/\d{4}\b", c)), "")
        prices = [c for c in cells if "rs" in c.lower() and "quintal" in c.lower()]
        if len(prices) < 3:
            continue

        min_price = re.search(r"(\d+)", prices[0])
        max_price = re.search(r"(\d+)", prices[1])
        modal_price = re.search(r"(\d+)", prices[2])

        if not (min_price and max_price and modal_price):
            continue

        return {
            "date": date_match,
            "min_price": int(min_price.group(1)),
            "max_price": int(max_price.group(1)),
            "modal_price": int(modal_price.group(1)),
            "market": "Kalaburgi APMC",
            "source": "CommodityOnline mandi feed",
        }

    return None


def _fetch_kalaburgi_apmc_price(crop: str) -> Dict[str, Any] | None:
    crop_slug = _normalize_crop_slug(crop)
    url = f"https://www.commodityonline.com/mandiprices/{crop_slug}/karnataka/gulbarga"

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AIKrishiBot/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    }

    with httpx.Client(timeout=8.0, headers=headers) as client:
        resp = client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return None

        return _extract_latest_kalaburgi_row(resp.text, crop_slug)

def _legacy_scraper_market_price(crop: str, mandi_location: str) -> Dict[str, Any]:
    """
    Fetches the latest market price for a specific crop at a regional Mandi.
    Use this proactively if a farmer asks about selling or current prices.
    """
    logger.info(f"🔧 AGENT TOOL CALLED: get_market_price(crop={crop}, mandi_location={mandi_location})")

    crop_lower = crop.lower()
    crop_key = _normalize_crop_slug(crop)

    cached = _market_price_cache.get(crop_key)
    if cached and (time.time() - cached.get("cached_at", 0) < _MARKET_CACHE_TTL_SECONDS):
        return cached["payload"]

    # ----------------------------------------------------
    # LIVE KALABURGI APMC INTEGRATION
    # ----------------------------------------------------
    try:
        latest = _fetch_kalaburgi_apmc_price(crop)
        if latest:
            min_price = latest["min_price"]
            max_price = latest["max_price"]
            modal_price = latest["modal_price"]
            date = latest.get("date") or "latest available update"

            payload = {
                "mandi": "Kalaburgi APMC",
                "state": "Karnataka",
                "crop": crop,
                "current_price": f"{min_price}-{max_price} INR/Quintal (Avg {modal_price})",
                "market_trend": f"Live Kalaburgi APMC update from {date}.",
                "source": latest.get("source", "CommodityOnline mandi feed"),
                "advice": f"Tell the farmer Kalaburgi APMC price for {crop} is {min_price} to {max_price} rupees per quintal, with modal price {modal_price}."
            }
            _market_price_cache[crop_key] = {"cached_at": time.time(), "payload": payload}
            return payload
    except Exception as e:
        logger.error(f"Failed to fetch Kalaburgi APMC live prices: {e}")
        # Fallback to static estimates gracefully
    
    # ----------------------------------------------------
    # COMPREHENSIVE MOCK FALLBACK DATA
    # ----------------------------------------------------
    prices = {
        "tomato": {"price": "35-45 INR/kg", "trend": "Rising due to recent unseasonal rains."},
        "onion": {"price": "55-65 INR/kg", "trend": "Volatile. Expected to drop next week."},
        "potato": {"price": "22-28 INR/kg", "trend": "Stable supply from cold storage."},
        "ragi": {"price": "3500-3800 INR/Quintal", "trend": "High demand, stable pricing."},
        "maize": {"price": "2100-2400 INR/Quintal", "trend": "Demand increasing for poultry feed."},
        "wheat": {"price": "2800-3100 INR/Quintal", "trend": "Stable."},
        "cotton": {"price": "7200-7500 INR/Quintal", "trend": "Slight dip due to international market."},
        "sugarcane": {"price": "3150 INR/Ton", "trend": "Fixed FRP by government."},
        "chilli": {"price": "180-220 INR/kg", "trend": "Spiking due to low yield in Guntur."},
        "ginger": {"price": "120-150 INR/kg", "trend": "Stable."},
    }

    match = next((data for key, data in prices.items() if key in crop_lower), None)
    
    if match:
        price = match["price"]
        trend = match["trend"]
    else:
        price = "40-60 INR/kg (Estimated Generic)"
        trend = "Average regional stability."

    payload = {
        "mandi": "Kalaburgi APMC",
        "crop": crop,
        "current_price": price,
        "market_trend": f"{trend} (fallback estimate)",
        "source": "local fallback",
        "advice": f"Tell the farmer this is a fallback estimate for Kalaburgi APMC: {price}, trend: {trend}."
    }
    _market_price_cache[crop_key] = {"cached_at": time.time(), "payload": payload}
    return payload



def get_market_price(crop: str, mandi_location: str = "Kalaburagi") -> Dict[str, Any]:
    """
    Backwards-compatible price lookup for existing internal callers.

    Keeps the old keyword signature and the old `current_price` string shape
    that the dashboards and Twilio handlers already read, but sources the
    numbers from the market intelligence engine instead of the single-mandi
    scraper.

    When no data is available it says so. It deliberately does NOT fall back
    to the old hardcoded price table: that fallback is what allowed a total
    data outage to go unnoticed while farmers were quoted invented figures.
    """
    from app.market import service as _market_service

    payload = _market_service.get_price(crop, mandi_location)

    if not payload.get("available"):
        return {
            "mandi": mandi_location,
            "crop": crop,
            "current_price": "unavailable",
            "market_trend": "Market data could not be retrieved.",
            "source": "none",
            "data_quality": "UNAVAILABLE",
            "available": False,
            "advice": (
                "Tell the farmer the market data is not available right now and to "
                "check with their local mandi. Do not quote a price."
            ),
        }

    modal = payload["modal_price"]
    low = payload.get("min_price") or modal
    high = payload.get("max_price") or modal
    quality = payload["data_quality"]
    caveat = payload.get("caveat") or ""

    return {
        "mandi": payload["market"],
        "state": payload["state"],
        "crop": payload["commodity"],
        "current_price": f"{low:.0f}-{high:.0f} INR/Quintal (Avg {modal:.0f})",
        "modal_price": modal,
        "price_per_kg": payload["price_per_kg"],
        "arrival_date": payload["arrival_date"],
        "market_trend": payload["spoken"],
        "source": payload["provenance"]["source"],
        "data_quality": quality,
        "is_current": payload["is_current"],
        "available": True,
        "advice": payload["spoken"],
        "caveat": caveat,
    }


def warm_market_price_cache() -> None:
    """Preload live prices at startup so first dashboard/call request is fast."""
    crops = ["Onion", "Tomato", "Potato", "Chilli", "Maize"]
    for crop in crops:
        try:
            _legacy_scraper_market_price(crop=crop, mandi_location="Kalaburgi")
        except Exception as e:
            logger.warning(f"Market cache warmup failed for {crop}: {e}")

def find_dealers(product: str, location: str) -> List[Dict[str, str]]:
    """
    Finds verified dealers for fertilizers, seeds, pesticides, or machinery in the area.
    """
    logger.info(f"🔧 AGENT TOOL CALLED: find_dealers(product={product}, location={location})")
    
    return [
        {
            "dealer_id": "DLR-101",
            "name": "Kisan Agro Kendra",
            "location": f"APMC Yard, {location}",
            "status": "Verified Partner 🟢",
            "inventory": "Urea, DAP, Neem Oil",
            "phone": "+91-9876543210"
        },
        {
            "dealer_id": "DLR-102",
            "name": "Green Earth Seeds & Fertilizers",
            "location": f"Main Market, {location} District",
            "status": "Verified Partner 🟢",
            "inventory": "Hybrid Seeds, Pesticides, Tractors",
            "phone": "+91-9988776655"
        },
        {
            "dealer_id": "DLR-103",
            "name": "Sri Lakshmi Agri Tech",
            "location": f"Highway Road, {location}",
            "status": "Verified Partner 🟢",
            "inventory": "Drip Irrigation, Water Pumps, Urea",
            "phone": "+91-8877665544"
        }
    ]

def find_crop_buyers(crop: str, location: str) -> List[Dict[str, Any]]:
    """
    Finds verified APMC Mandi agents, private food processors, or bulk buyers looking to buy the farmer's crops.
    Use this if the farmer wants to sell large quantities (e.g., '10 quintals of Onion').
    """
    logger.info(f"🔧 AGENT TOOL CALLED: find_crop_buyers(crop={crop}, location={location})")
    
    return [
        {
            "buyer_id": "BUY-301",
            "name": f"Sri Ram Traders ({location} APMC)",
            "buyer_type": "Mandi Commission Agent",
            "buying_price": "Matches daily APMC market rate minus commission",
            "contact": "+91-8899001122"
        },
        {
            "buyer_id": "BUY-302",
            "name": f"Reliance Fresh Regional Hub",
            "buyer_type": "Corporate Buyer",
            "buying_price": "Premium rate for A-grade quality",
            "contact": "+91-7788990011"
        }
    ]

def contact_dealer(dealer_id: str, message: str) -> Dict[str, str]:
    """
    Sends an automated WhatsApp/SMS message to a specific dealer on behalf of the farmer.
    Use this proactively when farmer intent is clear.
    
    Args:
        dealer_id: The ID of the dealer (e.g., 'DLR-101')
        message: The exact question or request to send to the dealer
    """
    logger.info(f"🔧 AGENT TOOL CALLED: contact_dealer(dealer_id={dealer_id}, message={message})")
    
    return {
        "status": "success",
        "action": "Message delivered to dealer via WhatsApp.",
        "dealer_id": dealer_id,
        "note": "Let the farmer know the dealer will call them back in 5-10 minutes."
    }

def place_order(product: str, quantity: str, dealer_id: str) -> Dict[str, str]:
    """
    Places an official purchase order for an agricultural product with a dealer.
    Use this autonomously when user intent is clear and enough details are available.
    
    Args:
        product: The product to order (e.g., '20kg Bag of Urea')
        quantity: How many units (e.g., '2', '5 bottles')
        dealer_id: The target dealer ID
    """
    logger.info(f"🔧 AGENT TOOL CALLED: place_order(product={product}, quantity={quantity}, dealer_id={dealer_id})")
    
    return {
        "status": "success",
        "order_id": f"ORD-2026-{hash(product+quantity) % 9999}",
        "delivery_estimate": "Tomorrow morning",
        "action_taken": f"Ordered {quantity} of {product} from {dealer_id}."
    }


def negotiate_market_deal(crop: str, quantity: str, location: str, target_price: str = "") -> Dict[str, Any]:
    """
    Negotiates a likely selling deal for the farmer based on mandi trend and buyer type.
    Use this when farmer asks for better price, bargaining help, or selling strategy.
    """
    logger.info(
        f"🔧 AGENT TOOL CALLED: negotiate_market_deal(crop={crop}, quantity={quantity}, location={location}, target_price={target_price})"
    )

    market = get_market_price(crop=crop, mandi_location=location)
    buyers = find_crop_buyers(crop=crop, location=location)

    current_price = str(market.get("current_price", "N/A"))
    preferred_buyer = buyers[1] if len(buyers) > 1 else buyers[0]

    return {
        "status": "success",
        "crop": crop,
        "quantity": quantity,
        "location": location,
        "current_mandi_price": current_price,
        "target_price": target_price or "Market-linked premium",
        "negotiation_result": "Buyer agreed to review lot quality and offer a premium over modal mandi rate for A-grade produce.",
        "recommended_buyer": preferred_buyer,
        "next_action": "Proceed to connect buyer and schedule pickup window."
    }


def sell_crop_autonomously(crop: str, quantity: str, location: str, farmer_phone: str = "") -> Dict[str, Any]:
    """
    End-to-end autonomous selling action:
    finds buyer, negotiates expected outcome, and creates a sale lead record.
    Use when farmer explicitly asks to sell produce quickly or at best available rate.
    """
    logger.info(
        f"🔧 AGENT TOOL CALLED: sell_crop_autonomously(crop={crop}, quantity={quantity}, location={location}, farmer_phone={farmer_phone})"
    )

    deal = negotiate_market_deal(crop=crop, quantity=quantity, location=location)
    buyer = deal.get("recommended_buyer", {})

    lead_id = f"SALE-2026-{abs(hash(crop + quantity + location)) % 100000}"
    return {
        "status": "success",
        "lead_id": lead_id,
        "crop": crop,
        "quantity": quantity,
        "location": location,
        "assigned_buyer": buyer,
        "negotiated_summary": deal.get("negotiation_result", "Negotiation initiated"),
        "farmer_phone": farmer_phone or "Not provided",
        "next_action": "Buyer callback and pickup coordination initiated."
    }

def check_crop_insurance(crop: str, state: str) -> Dict[str, Any]:
    """
    Checks the active PMFBY (Pradhan Mantri Fasal Bima Yojana) insurance plans, premium rates, and deadlines for a specific crop and state.
    """
    logger.info(f"🔧 AGENT TOOL CALLED: check_crop_insurance(crop={crop}, state={state})")
    return {
        "status": "Active",
        "scheme": "PMFBY",
        "crop": crop,
        "state": state,
        "premium_percent": 2.0 if "Kharif" in crop.title() else 1.5,
        "coverage_amount_per_hectare": "₹45,000",
        "deadline": "31st July 2026",
        "required_documents": ["Aadhar", "Land Records", "Bank Passbook"],
        "action": "Advise the farmer to visit the nearest CSC center to register before the deadline."
    }

def find_government_schemes(category: str, state: str) -> List[Dict[str, Any]]:
    """
    Finds active government schemes/subsidies matching a category string (e.g. 'equipment', 'financial', 'water').
    """
    logger.info(f"🔧 AGENT TOOL CALLED: find_government_schemes(category={category}, state={state})")
    schemes = []
    
    if "equip" in category.lower() or "tractor" in category.lower() or "machine" in category.lower():
        schemes.append({
            "name": "SMAM (Equipment Subsidy)",
            "benefit": "50-80% subsidy for purchasing tractors, tillers, and machinery.",
            "status": "Open until June 2026",
            "eligibility": "Small and marginal farmers"
        })
    elif "water" in category.lower() or "irrig" in category.lower():
        schemes.append({
            "name": "PM Krishi Sinchayee Yojana",
            "benefit": "Subsidies for drip and sprinkler irrigation systems.",
            "status": "Closing soon",
            "eligibility": "All farmers"
        })
    else:
        schemes.append({
            "name": "PM-KISAN Samman Nidhi",
            "benefit": "Financial benefit of Rs 6000/- per year in three equal installments.",
            "status": "Active/Ongoing",
            "eligibility": "All landholding farmers' families"
        })
        
    return schemes

def _run_async_safely(coro) -> Any:
    import asyncio
    import concurrent.futures
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
        
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)

def predict_best_crops(soil_type: str, location: str, season: str) -> Dict[str, Any]:
    """
    Predicts the best crops to grow using advanced AI based on soil, location, and season.
    Use this if the farmer asks "What should I grow?" or wants crop recommendations.
    """
    logger.info(f"🔧 AGENT TOOL CALLED: predict_best_crops(soil_type={soil_type}, location={location}, season={season})")
    from app.crop_predictor import predict_crops
    return _run_async_safely(predict_crops(soil_type=soil_type, location=location, season=season))

def predict_expected_yield(crop: str, area_acres: float, soil_type: str, rainfall_mm: float, season: str) -> Dict[str, Any]:
    """
    Predicts the expected yield of a specific crop using advanced AI based on area, soil type, recent rainfall, and season.
    Use this if the farmer asks "How much yield can I expect?" or "What acts will be the output?"
    """
    logger.info(f"🔧 AGENT TOOL CALLED: predict_expected_yield(crop={crop}, area_acres={area_acres})")
    from app.crop_predictor import predict_yield
    return _run_async_safely(predict_yield(crop=crop, area_acres=area_acres, soil_type=soil_type, rainfall_mm=rainfall_mm, season=season))

# Export the tools as a list for the Gemini SDK
# Market intelligence tools replace the old single-mandi scraper. They read
# from the price history and the deterministic analytics engine, and every
# payload they return carries its own data_quality and provenance.
from app.market.voice_tools import MARKET_TOOLS  # noqa: E402

AGRICULTURE_TOOLS = [
    *MARKET_TOOLS,
    find_dealers,
    find_crop_buyers,
    contact_dealer,
    place_order,
    negotiate_market_deal,
    sell_crop_autonomously,
    check_crop_insurance,
    find_government_schemes,
    predict_best_crops,
    predict_expected_yield,
]
