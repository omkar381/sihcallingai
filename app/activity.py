"""Shared in-memory activity feed for dashboard SSE and API consumers."""

from collections import deque
import time
from typing import Optional, Dict, Any, List

_activity_events = deque(maxlen=400)


def publish_activity_event(event_type: str, title: str, details: str, meta: Optional[Dict[str, Any]] = None) -> None:
    event = {
        "timestamp": int(time.time()),
        "event_type": event_type,
        "title": title,
        "details": details,
        "meta": meta or {},
    }
    _activity_events.appendleft(event)


def get_recent_activity_events(limit: int = 50) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(limit, 200))
    return list(_activity_events)[:safe_limit]
