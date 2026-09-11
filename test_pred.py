import asyncio
from app.crop_predictor import predict_crops

async def main():
    try:
        res = await asyncio.wait_for(predict_crops(), timeout=45)
        print({
            "location": res.get("location"),
            "temperature": res.get("temperature"),
            "predictions_count": len(res.get("predictions", [])),
            "top_crop": (res.get("predictions", [{}])[0] or {}).get("crop"),
        })
    except asyncio.TimeoutError:
        print({"error": "predict_crops timed out"})
    except Exception as exc:
        print({"error": str(exc)})

if __name__ == "__main__":
    asyncio.run(main())
