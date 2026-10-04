"""Local, append-only dashboard alert evaluation."""
from .service import acknowledge, list_alerts, run

__all__ = ["acknowledge", "list_alerts", "run"]
