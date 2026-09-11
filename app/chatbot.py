"""Chatbot module — Multimodal AI chat with Gemini Vision, document & image analysis."""

import base64
import io
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# In-memory chat session storage
_chat_sessions: Dict[str, Dict[str, Any]] = {}

_SYSTEM_PROMPT = """You are Krishi AI, an expert agricultural assistant for Indian farmers.
You specialize in:
- Crop health diagnosis and treatment recommendations
- Soil management and fertilizer guidance
- Weather-based farming advice
- Market price insights and selling strategies
- Government scheme information for farmers
- Pest and disease identification from descriptions and images
- Soil report analysis and fertilizer planning
- Invoice and bill OCR and fraud detection

When a farmer uploads a document (soil report, images, PDFs), analyze the content and provide specific, actionable recommendations.
Always respond in clear, simple language. Use Hindi/Kannada terms when helpful.
Be warm, supportive, and proactive in your advice."""

_IMAGE_ANALYSIS_PROMPT = """You are an expert agricultural AI assistant analyzing a farmer's uploaded image.

Analyze this image carefully and provide a structured response:

🔍 **Analysis**
Describe what you see in the image (crop type, condition, soil, document type, etc.)

📊 **Key Findings**
List the important observations with specific details (disease name, nutrient levels, cost breakdown, etc.)

⚠️ **Issues Detected**
Highlight any problems, deficiencies, diseases, or concerns found

✅ **Recommended Actions**
Provide specific, actionable steps the farmer should take. Include:
- Treatment products with dosage
- Timeline for action
- Estimated cost if applicable
- Preventive measures for the future

If this is a soil report: extract soil type, pH, N/P/K levels, organic carbon, and recommend crops + fertilizer plan.
If this is a crop/plant image: detect diseases, assess health, suggest treatment.
If this is an invoice/bill: extract items, costs, check for overcharging.
If this is a land/field image: assess soil condition, suggest improvements.

Keep the language farmer-friendly. Use simple terms."""

_PDF_ANALYSIS_PROMPT = """You are an expert agricultural AI analyzing a farmer's document.

The following text was extracted from a PDF document uploaded by a farmer.
Analyze it thoroughly and provide a structured response:

🔍 **Analysis**
What type of document is this? (soil report, invoice, government scheme, insurance doc, etc.)

📊 **Key Findings**
Extract and list all important data points with values.

⚠️ **Issues Detected**
Flag any problems, deficiencies, discrepancies, or missing information.

✅ **Recommended Actions**
What should the farmer do next? Be specific with products, dosages, timelines.

--- DOCUMENT TEXT ---
{text}
---

Provide your analysis:"""


def _get_session(session_id: str) -> Dict[str, Any]:
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = {
            "id": session_id,
            "messages": [],
            "documents": [],
            "created_at": int(time.time()),
        }
    return _chat_sessions[session_id]


def _build_context(session: Dict[str, Any]) -> str:
    """Build context string from uploaded documents."""
    if not session.get("documents"):
        return ""
    parts = ["\n--- Uploaded Document Context ---"]
    for doc in session["documents"][-3:]:  # last 3 docs max
        parts.append(f"[{doc['filename']}]: {doc['content'][:2000]}")
    return "\n".join(parts)


def _get_gemini_model():
    """Get a Gemini model instance for chat/vision."""
    import google.generativeai as genai
    from app.config import get_settings
    settings = get_settings()
    if not settings.GEMINI_API_KEY:
        return None
    genai.configure(api_key=settings.GEMINI_API_KEY)
    return genai.GenerativeModel("gemini-2.5-flash")


async def chat(message: str, session_id: str = "", farmer_context: str = "") -> Dict[str, Any]:
    """Send a message to Gemini and get agricultural AI response."""
    if not session_id:
        session_id = f"chat-{uuid.uuid4().hex[:8]}"

    session = _get_session(session_id)

    # Record user message
    session["messages"].append({
        "role": "user",
        "content": message,
        "timestamp": int(time.time()),
    })

    # Build prompt with context
    doc_context = _build_context(session)
    history_text = ""
    for msg in session["messages"][-6:]:  # last 6 messages for context
        role_label = "Farmer" if msg["role"] == "user" else "Krishi AI"
        history_text += f"\n{role_label}: {msg['content']}"

    full_prompt = f"""{_SYSTEM_PROMPT}

{f'Farmer Context: {farmer_context}' if farmer_context else ''}
{doc_context}

Conversation:
{history_text}

Krishi AI:"""

    # Try Gemini
    try:
        model = _get_gemini_model()
        if model:
            response = model.generate_content(full_prompt)
            ai_text = response.text.strip()
        else:
            ai_text = _fallback_response(message)
    except Exception as e:
        logger.error(f"Chatbot Gemini error: {e}")
        ai_text = _fallback_response(message)

    # Record AI response
    session["messages"].append({
        "role": "assistant",
        "content": ai_text,
        "timestamp": int(time.time()),
    })

    return {
        "session_id": session_id,
        "response": ai_text,
        "message_count": len(session["messages"]),
    }


async def analyze_image(
    image_bytes: bytes,
    filename: str,
    mime_type: str,
    session_id: str = "",
    farmer_context: str = "",
) -> Dict[str, Any]:
    """Analyze an uploaded image using Gemini Vision API."""
    import google.generativeai as genai

    if not session_id:
        session_id = f"chat-{uuid.uuid4().hex[:8]}"

    session = _get_session(session_id)

    # Record user upload event
    session["messages"].append({
        "role": "user",
        "content": f"[Uploaded image: {filename}]",
        "timestamp": int(time.time()),
    })

    try:
        model = _get_gemini_model()
        if not model:
            raise ValueError("Gemini API key not configured")

        # Build the multimodal content
        image_part = {
            "mime_type": mime_type,
            "data": image_bytes,
        }

        context_note = ""
        if farmer_context:
            context_note = f"\n\nFarmer context: {farmer_context}"

        response = model.generate_content([
            _IMAGE_ANALYSIS_PROMPT + context_note,
            image_part,
        ])

        ai_text = response.text.strip()

    except Exception as e:
        logger.error(f"Image analysis error: {e}", exc_info=True)
        ai_text = (
            "🔍 **Analysis**\n"
            "I wasn't able to fully analyze this image at the moment.\n\n"
            "✅ **Recommended Actions**\n"
            "Please try uploading again, or describe what you see in the image "
            "and I'll provide my best guidance."
        )

    # Record AI response
    session["messages"].append({
        "role": "assistant",
        "content": ai_text,
        "timestamp": int(time.time()),
    })

    # Also store as document context for follow-up questions
    session["documents"].append({
        "filename": filename,
        "content": f"[Image analysis result]: {ai_text[:3000]}",
        "uploaded_at": int(time.time()),
    })

    return {
        "session_id": session_id,
        "response": ai_text,
        "analysis_type": "image",
        "filename": filename,
        "message_count": len(session["messages"]),
    }


async def analyze_pdf(
    pdf_bytes: bytes,
    filename: str,
    session_id: str = "",
    farmer_context: str = "",
) -> Dict[str, Any]:
    """Extract text from PDF and analyze with Gemini."""
    if not session_id:
        session_id = f"chat-{uuid.uuid4().hex[:8]}"

    session = _get_session(session_id)

    # Record user upload
    session["messages"].append({
        "role": "user",
        "content": f"[Uploaded PDF: {filename}]",
        "timestamp": int(time.time()),
    })

    # Try to extract text from PDF
    extracted_text = ""
    has_images = False

    # Method 1: Try PyMuPDF (fitz)
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page in doc:
            extracted_text += page.get_text() + "\n"
            # Check if page has images
            if page.get_images():
                has_images = True
        doc.close()
    except ImportError:
        logger.warning("PyMuPDF not installed, trying pdfplumber")
        # Method 2: Try pdfplumber
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        extracted_text += text + "\n"
        except ImportError:
            logger.warning("Neither PyMuPDF nor pdfplumber installed")
            extracted_text = ""
        except Exception as e:
            logger.error(f"pdfplumber extraction error: {e}")
            extracted_text = ""
    except Exception as e:
        logger.error(f"PDF text extraction error: {e}")
        extracted_text = ""

    # If we got text, analyze with Gemini text
    if extracted_text.strip():
        try:
            model = _get_gemini_model()
            if not model:
                raise ValueError("Gemini API key not configured")

            prompt = _PDF_ANALYSIS_PROMPT.format(text=extracted_text[:8000])
            if farmer_context:
                prompt += f"\n\nFarmer context: {farmer_context}"

            response = model.generate_content(prompt)
            ai_text = response.text.strip()

        except Exception as e:
            logger.error(f"PDF analysis error: {e}", exc_info=True)
            ai_text = f"I extracted the document text but couldn't complete the analysis. Here's what I found:\n\n{extracted_text[:2000]}"
    elif has_images:
        # PDF has images but no extractable text — try vision on first page
        try:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = doc[0]
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            doc.close()

            # Use image analysis on the rendered page
            result = await analyze_image(
                image_bytes=img_bytes,
                filename=f"{filename}_page1.png",
                mime_type="image/png",
                session_id=session_id,
                farmer_context=farmer_context,
            )
            return result
        except Exception as e:
            logger.error(f"PDF image extraction error: {e}")
            ai_text = (
                "🔍 **Analysis**\n"
                "This PDF appears to contain scanned images. "
                "I wasn't able to extract readable text from it.\n\n"
                "✅ **Recommended Actions**\n"
                "Try taking a clear photo of the document instead, "
                "and upload that image for better analysis."
            )
    else:
        ai_text = (
            "🔍 **Analysis**\n"
            "I couldn't extract any text from this PDF.\n\n"
            "✅ **Recommended Actions**\n"
            "Please try uploading a clear photo of the document, "
            "or copy-paste the text content directly in the chat."
        )

    # Record AI response
    session["messages"].append({
        "role": "assistant",
        "content": ai_text,
        "timestamp": int(time.time()),
    })

    session["documents"].append({
        "filename": filename,
        "content": f"[PDF analysis]: {ai_text[:3000]}",
        "uploaded_at": int(time.time()),
    })

    return {
        "session_id": session_id,
        "response": ai_text,
        "analysis_type": "pdf",
        "filename": filename,
        "extracted_text_length": len(extracted_text),
        "message_count": len(session["messages"]),
    }


def upload_document(session_id: str, filename: str, content: str) -> Dict[str, Any]:
    """Store document content in session for chat context."""
    if not session_id:
        session_id = f"chat-{uuid.uuid4().hex[:8]}"

    session = _get_session(session_id)
    doc = {
        "filename": filename,
        "content": content[:5000],  # limit size
        "uploaded_at": int(time.time()),
    }
    session["documents"].append(doc)

    return {
        "success": True,
        "session_id": session_id,
        "filename": filename,
        "content_length": len(content),
        "total_documents": len(session["documents"]),
    }


def get_history(session_id: str) -> Dict[str, Any]:
    """Get chat history for a session."""
    session = _get_session(session_id)
    return {
        "session_id": session_id,
        "messages": session["messages"],
        "documents": [{"filename": d["filename"], "uploaded_at": d["uploaded_at"]} for d in session.get("documents", [])],
    }


def _fallback_response(message: str) -> str:
    """Simple keyword-based fallback when Gemini is unavailable."""
    msg = message.lower()
    if any(w in msg for w in ["price", "rate", "mandi", "market"]):
        return "For current market prices, I recommend checking the Live Market Prices section on your dashboard. Prices update every 5 minutes from Kalaburgi APMC. You can also call our AI assistant for verbal price updates."
    if any(w in msg for w in ["pest", "disease", "bug", "insect"]):
        return "For pest or disease identification, please describe the symptoms you're seeing (leaf color, spots, wilting, etc.) or upload a photo. Common treatments include neem oil spray for general pests and copper-based fungicides for leaf spot diseases."
    if any(w in msg for w in ["soil", "fertilizer", "nutrient"]):
        return "Soil health is crucial for good yields. For red laterite soils common in Karnataka, I recommend adding organic compost and DAP fertilizer. Get a soil test done at your nearest Krishi Vigyan Kendra for specific recommendations."
    if any(w in msg for w in ["weather", "rain", "temperature"]):
        return "Check the weather widget on your dashboard for current conditions. For farming decisions, consider that most kharif crops need 600-1000mm rainfall, while rabi crops can manage with 300-500mm."
    if any(w in msg for w in ["sell", "buyer", "selling"]):
        return "You can list your crops for sale in the Marketplace section. Set a fair price based on the current APMC rates shown in your dashboard. Buyers in your area will be able to find and contact you."
    return "I'm here to help with any farming questions! You can ask me about crop health, market prices, weather conditions, pest management, soil care, or government schemes. Try uploading a photo of your crop for instant disease detection!"
