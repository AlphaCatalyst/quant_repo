from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntrySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    entry_kind: Literal["trade", "no_action", "note", "review"]
    thesis_id: str | None = None
    holdings_snapshot_id: str | None = None
    reason: str = Field(min_length=1)
    forecast_ids: list[str] = Field(default_factory=list)
    code: str | None = None
    side: Literal["buy", "sell"] | None = None
    quantity: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, ge=0)
    amount: float | None = Field(default=None, ge=0)
    falsifiers: list[str] = Field(default_factory=list)
    expected_holding_period: str | None = None

    @model_validator(mode="after")
    def validate_trade(self) -> "EntrySpec":
        fields = ("code", "side", "quantity", "price", "amount")
        if self.entry_kind == "trade" and any(getattr(self, field) is None for field in fields):
            raise ValueError("trade requires code, side, quantity, price, and amount")
        if self.entry_kind != "trade" and any(getattr(self, field) is not None for field in fields):
            raise ValueError("trade fields require entry_kind=trade")
        return self
