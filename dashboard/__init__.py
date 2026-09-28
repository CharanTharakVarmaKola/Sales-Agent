"""dashboard/ — self-contained operator control room (STAGE D4).

Single entry point: `from dashboard import Dashboard, serve`.
Read-only over the ledger (parity-B6 §13 prime directive holds).
"""
from dashboard.app import Dashboard, make_handler, serve

__all__ = ["Dashboard", "make_handler", "serve"]
