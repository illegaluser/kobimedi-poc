from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, List, Dict


class Action(str, Enum):
    BOOK_APPOINTMENT = "book_appointment"
    MODIFY_APPOINTMENT = "modify_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"
    CHECK_APPOINTMENT = "check_appointment"
    CLARIFY = "clarify"
    ESCALATE = "escalate"
    REJECT = "reject"


VALID_ACTION_VALUES = {action.value for action in Action}


@dataclass
class User:
    patient_id: str
    name: str
    is_first_visit: bool


@dataclass
class AppointmentRequest:
    patient_id: str
    name: str
    appointment_time: datetime
    is_first_visit: bool


@dataclass
class Ticket:
    intent: str
    user: User
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Booking:
    booking_id: str
    patient_id: str
    patient_name: str
    start_time: datetime
    end_time: datetime
    is_first_visit: bool


@dataclass
class PolicyResult:
    action: Action
    message: Optional[str] = None
    suggested_slots: List[datetime] = field(default_factory=list)
