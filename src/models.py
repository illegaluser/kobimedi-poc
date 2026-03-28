"""
src/models.py — 도메인 모델 정의

챗봇 시스템 전체에서 공유하는 핵심 데이터 구조를 정의한다.
Action enum은 챗봇이 취할 수 있는 모든 행동(예약, 변경, 취소 등)을 열거하며,
policy.py가 반환하는 PolicyResult는 '어떤 행동을 해야 하는가'를 결정론적으로 전달한다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, List, Dict


class Action(str, Enum):
    """챗봇이 수행할 수 있는 7가지 행동 유형.
    AGENTS.md에 정의된 action enum과 1:1 대응한다.
      - BOOK_APPOINTMENT   : 신규 예약 생성
      - MODIFY_APPOINTMENT : 기존 예약 변경 (기존 취소 → 신규 생성)
      - CANCEL_APPOINTMENT : 기존 예약 취소
      - CHECK_APPOINTMENT  : 기존 예약 조회
      - CLARIFY            : 정보 부족 → 사용자에게 추가 질문
      - ESCALATE           : 챗봇이 처리 불가 → 상담원에게 인계
      - REJECT             : 의료 상담, 프라이버시 요청 등 → 안전하게 거절
    """
    BOOK_APPOINTMENT = "book_appointment"
    MODIFY_APPOINTMENT = "modify_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"
    CHECK_APPOINTMENT = "check_appointment"
    CLARIFY = "clarify"
    ESCALATE = "escalate"
    REJECT = "reject"


# Action enum의 문자열 값 집합. LLM 응답 검증 시 유효한 action인지 빠르게 체크하는 데 사용.
VALID_ACTION_VALUES = {action.value for action in Action}


@dataclass
class User:
    """환자 정보. policy.py에서 초진/재진 판별에 사용된다."""
    patient_id: str
    name: str
    is_first_visit: bool       # True면 초진(40분), False면 재진(30분) 소요


@dataclass
class AppointmentRequest:
    """예약 요청 데이터. policy.py가 슬롯 가용성을 검사할 때 사용."""
    patient_id: str
    name: str
    appointment_time: datetime  # 요청된 예약 시각 (UTC)
    is_first_visit: bool


@dataclass
class Ticket:
    """분류기(classifier)가 추출한 사용자 의도를 담는 구조체.
    context에는 대화에서 추출된 부가 정보(날짜, 시간, 분과 등)가 들어간다."""
    intent: str
    user: User
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Booking:
    """저장소(storage.py)에서 로드한 기존 예약 데이터.
    policy.py가 정원(시간당 3명), 24시간 규칙 등을 계산할 때 사용."""
    booking_id: str
    patient_id: str
    patient_name: str
    start_time: datetime       # 예약 시작 시각
    end_time: datetime         # 예약 종료 시각 (초진 +40분 / 재진 +30분)
    is_first_visit: bool


@dataclass
class PolicyResult:
    """정책 엔진(policy.py)의 판정 결과.
      - action          : 허용/거절/대안제시 등 결정된 행동
      - message         : 사용자에게 전달할 안내 문구 (거절 사유, 대안 안내 등)
      - suggested_slots : 요청 시간이 불가할 때 제안하는 대체 시간 목록
    """
    action: Action
    message: Optional[str] = None
    suggested_slots: List[datetime] = field(default_factory=list)
