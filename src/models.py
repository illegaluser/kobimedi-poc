from __future__ import annotations

from enum import Enum


class Action(str, Enum):
    BOOK_APPOINTMENT = "book_appointment"
    MODIFY_APPOINTMENT = "modify_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"
    CHECK_APPOINTMENT = "check_appointment"
    CLARIFY = "clarify"
    ESCALATE = "escalate"
    REJECT = "reject"


VALID_ACTION_VALUES = {action.value for action in Action}
