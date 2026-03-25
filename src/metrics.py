from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class KpiEvent(str, Enum):
    AGENT_SUCCESS = "agent_success"
    SAFE_REJECT = "safe_reject"
    AGENT_SOFT_FAIL_CLARIFY = "agent_soft_fail_clarify"
    AGENT_HARD_FAIL = "agent_hard_fail"


@dataclass
class KpiMetrics:
    agent_success: int = 0
    safe_reject: int = 0
    agent_soft_fail_clarify: int = 0
    agent_hard_fail: int = 0

    def increment(self, event: KpiEvent):
        if event == KpiEvent.AGENT_SUCCESS:
            self.agent_success += 1
        elif event == KpiEvent.SAFE_REJECT:
            self.safe_reject += 1
        elif event == KpiEvent.AGENT_SOFT_FAIL_CLARIFY:
            self.agent_soft_fail_clarify += 1
        else:
            self.agent_hard_fail += 1

    def as_dict(self) -> dict:
        return {
            "agent_success": self.agent_success,
            "safe_reject": self.safe_reject,
            "agent_soft_fail_clarify": self.agent_soft_fail_clarify,
            "agent_hard_fail": self.agent_hard_fail,
        }


METRICS_INSTANCE = KpiMetrics()


def get_metrics() -> KpiMetrics:
    return METRICS_INSTANCE


def record_kpi_event(event: KpiEvent):
    METRICS_INSTANCE.increment(event)
