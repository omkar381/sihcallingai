"""
LRU Cache with TTL for Gemini responses.
Avoids redundant API calls for similar queries.
"""

import hashlib
import time
import logging
from typing import Optional, Any

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# Cache: max 500 entries, 30 minute TTL
_response_cache = TTLCache(maxsize=500, ttl=1800)


def _make_cache_key(query: str, crop: str, location: str) -> str:
    """Create a cache key from query parameters."""
    raw = f"{query.lower().strip()}|{crop.lower().strip()}|{location.lower().strip()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get_cached_response(query: str, crop: str = "", location: str = "") -> Optional[str]:
    """
    Look up a cached response.
    Returns the cached response string, or None if not found.
    """
    key = _make_cache_key(query, crop, location)
    result = _response_cache.get(key)
    if result:
        logger.info(f"Cache HIT for key={key[:8]}...")
    else:
        logger.debug(f"Cache MISS for key={key[:8]}...")
    return result


def set_cached_response(query: str, crop: str, location: str, response: str) -> None:
    """Cache a response for future lookups."""
    key = _make_cache_key(query, crop, location)
    _response_cache[key] = response
    logger.info(f"Cached response for key={key[:8]}... (cache size: {len(_response_cache)})")


def clear_cache() -> int:
    """Clear all cached responses. Returns number of items cleared."""
    count = len(_response_cache)
    _response_cache.clear()
    logger.info(f"Cache cleared: {count} entries removed")
    return count
