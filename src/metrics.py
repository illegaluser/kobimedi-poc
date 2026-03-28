"""
src/metrics.py — KPI(핵심 성과 지표) 이벤트 기록 모듈

챗봇의 모든 응답 결과를 4가지 KPI 이벤트로 분류하여 카운팅한다.
이 모듈은 싱글턴 패턴으로, 프로세스 수명 동안 하나의 인스턴스(METRICS_INSTANCE)만 유지한다.

KPI 이벤트 정의:
  - AGENT_SUCCESS          : 상담원 없이 예약/변경/취소를 완료 (건당 +$10 절감)
  - SAFE_REJECT            : 의료 상담 등 위험 요청을 안전하게 거절 (방어 성공)
  - AGENT_SOFT_FAIL_CLARIFY: 정보 부족으로 추가 질문(clarify) 중인 상태 (건당 -$20 위험)
  - AGENT_HARD_FAIL        : 복구 불가능한 실패 — 저장소 오류, 거짓 성공 등 (건당 -$500 손실)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class KpiEvent(str, Enum):
    """챗봇 응답 결과를 4가지로 분류하는 열거형.
    agent.py의 각 분기점에서 record_kpi_event()를 호출하여 기록한다."""
    AGENT_SUCCESS = "agent_success"
    SAFE_REJECT = "safe_reject"
    AGENT_SOFT_FAIL_CLARIFY = "agent_soft_fail_clarify"
    AGENT_HARD_FAIL = "agent_hard_fail"


@dataclass
class KpiMetrics:
    """4개 KPI 카운터를 보관하는 데이터 클래스.
    increment()로 이벤트별 카운터를 1씩 증가시킨다."""
    agent_success: int = 0
    safe_reject: int = 0
    agent_soft_fail_clarify: int = 0
    agent_hard_fail: int = 0

    def increment(self, event: KpiEvent):
        """주어진 이벤트에 해당하는 카운터를 1 증가시킨다."""
        if event == KpiEvent.AGENT_SUCCESS:
            self.agent_success += 1
        elif event == KpiEvent.SAFE_REJECT:
            self.safe_reject += 1
        elif event == KpiEvent.AGENT_SOFT_FAIL_CLARIFY:
            self.agent_soft_fail_clarify += 1
        else:
            self.agent_hard_fail += 1

    def as_dict(self) -> dict:
        """현재 카운터 상태를 딕셔너리로 반환한다. 외부 리포트나 로깅에 사용."""
        return {
            "agent_success": self.agent_success,
            "safe_reject": self.safe_reject,
            "agent_soft_fail_clarify": self.agent_soft_fail_clarify,
            "agent_hard_fail": self.agent_hard_fail,
        }


# 프로세스 전역 싱글턴 — 모든 모듈이 이 인스턴스를 공유한다.
METRICS_INSTANCE = KpiMetrics()


def get_metrics() -> KpiMetrics:
    """싱글턴 KpiMetrics 인스턴스를 반환한다. 테스트나 리포트에서 현재 값을 조회할 때 사용."""
    return METRICS_INSTANCE


def record_kpi_event(event: KpiEvent):
    """KPI 이벤트를 기록한다.
    agent.py의 각 처리 분기 끝에서 호출되어, 해당 응답이 어떤 결과로 귀결되었는지 추적한다.
    예: record_kpi_event(KpiEvent.AGENT_SUCCESS)  → 성공 카운터 +1"""
    METRICS_INSTANCE.increment(event)
