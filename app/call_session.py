"""
Per-call session manager.
Tracks conversation history and detected context (crop, location, soil) per call.
"""

import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CallSession:
    """Represents a single phone call session."""
    call_sid: str
    phone_number: str = ""
    crop: str = "Unknown"
    soil: str = "Unknown"
    location: str = "Karnataka"
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    pending_action: Optional[Dict[str, str]] = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    def add_exchange(self, farmer_query: str, ai_response: str) -> None:
        """Add a conversation exchange to the session."""
        self.conversation_history.append({
            "farmer": farmer_query,
            "assistant": ai_response,
            "timestamp": time.time(),
        })
        self.last_activity = time.time()

    def get_context_summary(self) -> str:
        """Get a short summary of previous conversation for context."""
        if not self.conversation_history:
            return ""
        # Include last 3 exchanges for context
        recent = self.conversation_history[-3:]
        parts = []
        for ex in recent:
            parts.append(f"Farmer: {ex['farmer']}")
            parts.append(f"Expert: {ex['assistant']}")
        return "\n".join(parts)

    def is_expired(self, timeout_seconds: int = 1800) -> bool:
        """Check if session has expired (default: 30 minutes)."""
        return (time.time() - self.last_activity) > timeout_seconds


# Global session store: CallSid → CallSession
_sessions: Dict[str, CallSession] = {}


def get_or_create_session(call_sid: str, phone_number: str = "") -> CallSession:
    """
    Get an existing session or create a new one.
    """
    if call_sid in _sessions:
        session = _sessions[call_sid]
        if phone_number and phone_number != session.phone_number:
            session.phone_number = phone_number
        session.last_activity = time.time()
        return session

    session = CallSession(call_sid=call_sid, phone_number=phone_number)
    _sessions[call_sid] = session
    logger.info(f"Created new session for call {call_sid}")
    return session


def get_session(call_sid: str) -> Optional[CallSession]:
    """Get an existing session, or None if not found."""
    return _sessions.get(call_sid)


def remove_session(call_sid: str) -> None:
    """Remove a session when the call ends."""
    if call_sid in _sessions:
        del _sessions[call_sid]
        logger.info(f"Removed session for call {call_sid}")


def cleanup_expired_sessions(timeout_seconds: int = 1800) -> int:
    """Remove all expired sessions. Returns count of removed sessions."""
    expired = [
        sid for sid, session in _sessions.items()
        if session.is_expired(timeout_seconds)
    ]
    for sid in expired:
        del _sessions[sid]
    if expired:
        logger.info(f"Cleaned up {len(expired)} expired sessions")
    return len(expired)
