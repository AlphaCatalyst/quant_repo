"""Job kind registry.  Kinds keep execution details out of the job ledger."""

from .local_command import KIND as LOCAL_COMMAND
from .risk_report import KIND as RISK_REPORT
from .sw_sensitivity import KIND as SW_SENSITIVITY
from .train import KIND as TRAIN

KINDS = {kind.name: kind for kind in (TRAIN, RISK_REPORT, SW_SENSITIVITY, LOCAL_COMMAND)}


def get_kind(name: str):
    try:
        return KINDS[name]
    except KeyError as exc:
        raise ValueError(f"unknown job kind: {name}") from exc


__all__ = ["KINDS", "get_kind"]
