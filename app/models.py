"""
Example domain models. Implement or replace as needed.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class Shift(BaseModel):
    id: str
    organization_id: str
    role_required: str
    start_time: datetime
    end_time: datetime


class Caregiver(BaseModel):
    id: str
    name: str
    role: str
    phone: str


class ShiftFanoutStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    ESCALATED = "escalated"


class ShiftFanout(BaseModel):
    """Tracks the state of a shift fanout operation."""

    shift_id: str
    status: ShiftFanoutStatus
    created_at: datetime
    claimed_by: str | None = None
    sms_sent_at: datetime | None = None
    phone_call_sent_at: datetime | None = None
    contacted_caregiver_ids: list[str] = []


class InboundMessage(BaseModel):
    """Represents an incoming SMS or phone message."""

    from_phone: str
    message: str
    timestamp: datetime | None = None
