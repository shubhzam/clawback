from typing import Literal

from pydantic import BaseModel, Field


class ReviewRequest(BaseModel):
    action: Literal["approve_dispute", "accept_deduction"]
    reviewer: str = Field(min_length=1, max_length=64)
    note: str = ""
    # only used by approve_dispute, defaults to the amount the validator found invalid
    amount_cents: int | None = Field(default=None, gt=0)


class OutcomeRequest(BaseModel):
    outcome: Literal["won", "partial", "lost"]
    recovered_cents: int = Field(default=0, ge=0)


class SyncResponse(BaseModel):
    discovered: int
    created: int
    queued: int
