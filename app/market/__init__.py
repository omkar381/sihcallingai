"""
Market intelligence subsystem.

Layering, strictly one direction:

    ingestion  ->  normalization  ->  validation  ->  repository (history)
                                                          |
                                                          v
                                      trends / arrivals / comparison
                                                          |
                                                          v
                                              sale_window (recommendations)
                                                          |
                                                          v
                                        voice tools / REST API / dashboard

Nothing in this package imports from app.twilio_handlers. Market intelligence
must be callable from a phone call, an HTTP request or a test with identical
behaviour.
"""

from app.market.provenance import (
    Confidence,
    DataQuality,
    Provenance,
    classify_freshness,
    parse_data_date,
)
from app.market.schema import init_market_tables

__all__ = [
    "Confidence",
    "DataQuality",
    "Provenance",
    "classify_freshness",
    "parse_data_date",
    "init_market_tables",
]
