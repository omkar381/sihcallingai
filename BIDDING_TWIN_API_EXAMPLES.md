# Auction + Seller + Twin API Examples

## Create Listing
POST /api/listings/create

```json
{
  "farmer_phone": "+919876543210",
  "crop_name": "Onion",
  "quantity": 50,
  "base_price_per_unit": 28,
  "expires_in_minutes": 60
}
```

```json
{
  "success": true,
  "listing": {
    "id": "f3fb6d8c-7ca8-42e8-a9ef-8f98372f7b8e",
    "status": "OPEN",
    "highest_bid": null
  }
}
```

## Place Bid
POST /api/listings/{id}/bid

```json
{
  "buyer_id": "+919900001111",
  "bid_price_per_unit": 30,
  "quantity": 50
}
```

## Accept Bid
POST /api/listings/{id}/accept-bid

```json
{
  "bid_id": "e2a0da58-0337-4ef1-9f08-2a3194a81c72",
  "farmer_phone": "+919876543210"
}
```

## Complete Order Lifecycle
POST /api/listings/{id}/complete-order

```json
{
  "farmer_phone": "+919876543210"
}
```

## Seller Dashboard
GET /api/seller/{phone}/dashboard

```json
{
  "success": true,
  "kpis": {
    "active_listings": 2,
    "expired_listings": 1,
    "sold_listings": 4,
    "total_earnings": 18750,
    "pending_orders": 4,
    "completed_sales": 0
  }
}
```

## Digital Twin Read/Update
GET /api/farmer/{phone}/twin
PATCH /api/farmer/{phone}/twin

```json
{
  "soil_type": "Black soil",
  "water_source": "Borewell",
  "land_size": 4.5,
  "preferred_crops": ["Onion", "Tomato"]
}
```

## Recommendations
GET /api/farmer/{phone}/recommendations

```json
{
  "success": true,
  "farmer_phone": "+919876543210",
  "recommendations": [
    "Your risk score is moderate...",
    "Your yield is below average...",
    "Based on your profile, tomato is a strong candidate..."
  ],
  "ai_summary": "...Gemini personalized summary..."
}
```

## SSE Events
Stream endpoint: GET /api/activity-stream

Auction/twin events emitted:
- listing_created
- new_bid
- bid_placed
- bid_accepted
- twin_updated
