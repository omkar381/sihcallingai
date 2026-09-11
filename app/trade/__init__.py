"""
Transaction infrastructure: buyers, FPOs, lots, offers, logistics, payments,
disputes, and the audit trail that ties them together.

Layering, strictly one direction:

    schema  ->  audit  ->  buyers / fpo / lots  ->  matching
                                  |
                                  v
                    logistics -> payments -> disputes
                                  |
                                  v
                        service  ->  REST API / voice tools

`app.trade` may read from `app.market` (prices inform offers and matching).
`app.market` never imports from `app.trade`: market intelligence must stay
usable without any transaction having taken place.
"""

from app.trade.schema import init_trade_tables

__all__ = ["init_trade_tables"]
