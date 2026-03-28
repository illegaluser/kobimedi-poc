"""
결정론적 정책 엔진 (Deterministic Policy Engine)

이 모듈은 예약 챗봇의 **비즈니스 규칙을 순수 Python 로직으로 강제**하는 정책 계층이다.
LLM(대규모 언어모델)은 이 단계에 전혀 개입하지 않으며, 모든 판단은 if/else 분기와
수학적 비교만으로 이루어진다. 이를 통해 의료 예약에서 절대 위반되어서는 안 되는
규칙들을 100 % 재현 가능한 방식으로 보장한다.

적용되는 핵심 비즈니스 규칙:
  - 운영시간 (F-052): 월-금 09:00-18:00, 토 09:00-13:00, 일 휴진,
                       점심시간 12:30-13:30
  - 진료 소요시간 (F-054): 초진 40분, 재진 30분
  - 24시간 규칙 (F-055, F-057): 예약 변경·취소는 방문 24시간 이전에만 허용
  - 정원 제한 (F-053): 동일 시간대 최대 3명

진입점:
  apply_policy(ticket, bookings, now) -> PolicyResult
  - agent.py에서 호출되며, Ticket(사용자 의도)과 현재 예약 목록을 받아
    어떤 action을 수행할지 결정한 뒤 PolicyResult를 반환한다.
  - PolicyResult에는 action(수행할 동작), message(사용자 안내 메시지),
    suggested_slots(대안 시간 목록)이 담긴다.
"""
from __future__ import annotations
from datetime import datetime, timedelta, time

from src.models import Ticket, Booking, PolicyResult, Action

# ── 운영시간 상수 (F-052) ──
_WEEKDAY_OPEN = time(9, 0)
_WEEKDAY_CLOSE = time(18, 0)
_SATURDAY_OPEN = time(9, 0)
_SATURDAY_CLOSE = time(13, 0)
_LUNCH_START = time(12, 30)
_LUNCH_END = time(13, 30)


def _ensure_datetime(timestamp: str | datetime) -> datetime:
    """
    문자열 또는 datetime 입력을 datetime 객체로 통일한다.

    목적:
      외부에서 전달되는 시간 정보는 ISO 8601 문자열("2024-06-01T09:00:00Z")이거나
      이미 datetime 객체일 수 있다. 이 함수는 두 형태를 모두 받아서 항상
      datetime을 반환함으로써 이후 비교·연산 로직을 단순화한다.

    동작 흐름:
      1. 이미 datetime이면 그대로 반환
      2. 문자열이면 끝의 "Z"를 "+00:00"으로 치환(파이썬 fromisoformat 호환)
      3. fromisoformat()으로 파싱하여 반환

    시스템 내 역할:
      정책 엔진 전체에서 시간 비교의 **전처리 유틸리티**로 사용된다.
      Cal.com, 프론트엔드, 테스트 등 다양한 소스의 시간 형식 차이를 흡수한다.
    """
    if isinstance(timestamp, datetime):
        return timestamp
    raw = str(timestamp)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def apply_policy(ticket: Ticket, bookings: list, now: datetime) -> PolicyResult:
    """
    정책 엔진의 메인 진입점 — 티켓의 의도(intent)에 따라 적절한 핸들러로 분기한다.

    목적:
      agent.py가 LLM으로부터 추출한 사용자 의도(Ticket)를 받아,
      비즈니스 규칙에 따라 허용/거절/추가 정보 요청을 결정한다.
      이 함수 자체는 라우터 역할이며, 실제 규칙 검증은 각 _handle_* 함수가 수행한다.

    동작 흐름:
      1. bookings 리스트를 Booking 객체로 정규화 (dict → Booking 변환 포함)
         - 테스트에서는 Booking 객체를, 실제 앱에서는 dict를 전달하므로 두 형태 모두 처리
         - dict인 경우: booking_time, is_first_visit/customer_type 등의 키로 Booking 생성
      2. intent에 따라 분기:
         - book_appointment → _handle_booking
         - modify_appointment → _handle_modification
         - cancel_appointment → _handle_cancellation
         - check_appointment / clarify / escalate / reject → 해당 Action을 그대로 반환
         - 그 외 → REJECT

    시스템 내 역할:
      agent.py의 파이프라인에서 "safety gate 이후, 실행(execute) 이전" 단계에 위치한다.
      LLM이 결정한 의도를 검증하고, 규칙 위반 시 실행을 차단하는 **게이트키퍼** 역할이다.

    Args:
        ticket: 사용자 의도와 컨텍스트가 담긴 Ticket 객체
        bookings: 기존 예약 목록 (list[Booking] 또는 list[dict])
        now: 현재 시각 (테스트 시 고정 가능)

    Returns:
        PolicyResult: 수행할 action, 안내 메시지, 대안 시간 등
    """
    intent = ticket.intent

    # ── bookings 정규화: dict와 Booking 객체를 모두 Booking으로 통일 ──
    booking_objects: list[Booking] = []
    for b in bookings:
        if isinstance(b, Booking):
            booking_objects.append(b)
        elif isinstance(b, dict):
            booking_time_str = b.get("booking_time")
            if not booking_time_str:
                continue
            is_first = bool(
                b.get("is_first_visit", False)
                or b.get("customer_type") in {"초진", "new"}
            )
            try:
                start_time = _ensure_datetime(booking_time_str)
            except (ValueError, TypeError):
                continue
            duration = get_appointment_duration(is_first)
            end_time = start_time + duration
            booking_objects.append(
                Booking(
                    booking_id=str(b.get("id") or b.get("booking_id") or ""),
                    patient_id=str(b.get("patient_id") or b.get("customer_id") or "unknown"),
                    patient_name=str(b.get("patient_name") or b.get("customer_name") or ""),
                    start_time=start_time,
                    end_time=end_time,
                    is_first_visit=is_first,
                )
            )

    # ── intent별 핸들러 분기 ──
    if intent == "book_appointment":
        return _handle_booking(ticket, booking_objects, now)
    elif intent == "modify_appointment":
        return _handle_modification(ticket, booking_objects, now)
    elif intent == "cancel_appointment":
        return _handle_cancellation(ticket, booking_objects, now)
    elif intent in ["check_appointment", "clarify", "escalate", "reject"]:
        return PolicyResult(action=Action(intent))
    else:
        # Fallback for unknown intents
        return PolicyResult(action=Action.REJECT, message="알 수 없는 요청입니다.")


def _handle_booking(ticket: Ticket, bookings: list[Booking], now: datetime) -> PolicyResult:
    """
    신규 예약 요청을 처리한다.

    목적:
      사용자가 새 예약을 원할 때, 요청 시간이 유효한지(운영시간, 정원, 중복)
      검증하고 가능 여부를 반환한다.

    동작 흐름:
      1. ticket.context에서 요청 시간(appointment_time) 추출
         - 없으면 CLARIFY(추가 정보 요청) 반환
      2. 초진/재진에 따른 소요시간 계산
      3. is_slot_available()로 슬롯 가용성 검사
         - 가용 → BOOK_APPOINTMENT 반환
         - 불가 → 사유 메시지와 함께 대안 시간 제안 (과거 시간이면 대안 제안 생략)

    시스템 내 역할:
      "예약하고 싶어요" 의도에 대한 정책 판단을 담당한다.
      신규 예약에는 24시간 규칙이 적용되지 않으므로, 슬롯 가용성만 확인한다.
    """
    request_time = ticket.context.get("appointment_time")
    if not request_time:
        return PolicyResult(action=Action.CLARIFY, message="예약 시간이 명시되지 않았습니다.")

    # For new bookings on the same day, the 24-hour rule is not applicable
    # This check is implicitly handled by just checking slot availability.

    duration = get_appointment_duration(ticket.user.is_first_visit)
    is_available, reason = is_slot_available(_ensure_datetime(request_time), duration, bookings, now)

    if is_available:
        return PolicyResult(action=Action.BOOK_APPOINTMENT)
    else:
        # Do not suggest alternatives if the request is for a time in the past
        if "과거 시간" in reason:
            return PolicyResult(action=Action.CLARIFY, message=reason, suggested_slots=[])

        alternatives = suggest_alternative_slots(_ensure_datetime(request_time), duration, bookings, now)
        return PolicyResult(
            action=Action.CLARIFY,
            message=reason,
            suggested_slots=alternatives,
        )


def _handle_modification(ticket: Ticket, bookings: list[Booking], now: datetime) -> PolicyResult:
    """
    기존 예약의 시간 변경 요청을 처리한다.

    목적:
      사용자가 이미 잡힌 예약을 다른 시간으로 옮기고 싶을 때,
      24시간 규칙과 새 시간의 가용성을 검증한다.

    동작 흐름:
      1. ticket.context에서 booking_id를 꺼내 기존 예약을 조회
         - 못 찾으면 REJECT
      2. 24시간 규칙 검사 (is_change_or_cancel_allowed)
         - 위반 시 REJECT("방문 24시간 이전에만 가능")
      3. 새 시간(new_appointment_time) 추출
         - 없으면 CLARIFY
      4. 새 시간의 슬롯 가용성 검사 (기존 예약은 제외하고 검사)
         - 가용 → MODIFY_APPOINTMENT
         - 불가 → 대안 시간 제안과 함께 CLARIFY

    시스템 내 역할:
      "예약을 변경하고 싶어요" 의도에 대한 정책 판단을 담당한다.
      기존 예약 자체를 booking_id_to_ignore로 제외하여, 자기 자신과의
      중복 충돌을 방지한다.
    """
    booking_id = ticket.context.get("booking_id")
    original_booking = next((b for b in bookings if b.booking_id == booking_id), None)

    if not original_booking:
        return PolicyResult(action=Action.REJECT, message="수정할 예약 정보를 찾을 수 없습니다.")

    # Check 24-hour rule using start_time
    if not is_change_or_cancel_allowed(original_booking.start_time, now):
        return PolicyResult(
            action=Action.REJECT,
            message="예약 변경은 방문 24시간 이전에만 가능합니다."
        )

    new_time = ticket.context.get("new_appointment_time")
    if not new_time:
        return PolicyResult(action=Action.CLARIFY, message="변경할 예약 시간이 명시되지 않았습니다.")

    # Check availability of the new slot
    duration = get_appointment_duration(original_booking.is_first_visit)
    is_available, reason = is_slot_available(
        _ensure_datetime(new_time), duration, bookings, now, booking_id_to_ignore=booking_id
    )

    if is_available:
        return PolicyResult(action=Action.MODIFY_APPOINTMENT)
    else:
        alternatives = suggest_alternative_slots(
            _ensure_datetime(new_time), duration, bookings, now, booking_id_to_ignore=booking_id
        )
        return PolicyResult(
            action=Action.CLARIFY,
            message=reason,
            suggested_slots=alternatives,
        )


def _handle_cancellation(ticket: Ticket, bookings: list[Booking], now: datetime) -> PolicyResult:
    """
    예약 취소 요청을 처리한다.

    목적:
      사용자가 기존 예약을 취소하고 싶을 때, 24시간 규칙만 검증하면 된다.
      (슬롯 가용성은 취소에는 무관)

    동작 흐름:
      1. ticket.context에서 booking_id를 꺼내 취소 대상 예약 조회
         - 못 찾으면 REJECT
      2. 24시간 규칙 검사 (is_change_or_cancel_allowed)
         - 위반 시 REJECT("방문 24시간 이전에만 가능")
      3. 통과 시 CANCEL_APPOINTMENT 반환

    시스템 내 역할:
      "예약을 취소하고 싶어요" 의도에 대한 정책 판단을 담당한다.
      변경과 달리 새 시간 검증이 불필요하므로 로직이 가장 단순하다.
    """
    booking_id = ticket.context.get("booking_id")
    booking_to_cancel = next((b for b in bookings if b.booking_id == booking_id), None)

    if not booking_to_cancel:
        return PolicyResult(action=Action.REJECT, message="취소할 예약 정보를 찾을 수 없습니다.")

    # Check 24-hour rule using start_time
    if not is_change_or_cancel_allowed(booking_to_cancel.start_time, now):
        return PolicyResult(
            action=Action.REJECT,
            message="예약 취소는 방문 24시간 이전에만 가능합니다."
        )

    return PolicyResult(action=Action.CANCEL_APPOINTMENT)


def get_appointment_duration(is_first_visit: bool) -> timedelta:
    """
    환자 유형에 따른 진료 소요시간을 반환한다. (F-054)

    목적:
      초진(첫 방문)과 재진(재방문)의 진료 시간이 다르므로,
      예약 슬롯의 길이를 결정하는 데 사용된다.

    동작 흐름:
      - 초진(is_first_visit=True) → 40분
      - 재진(is_first_visit=False) → 30분

    시스템 내 역할:
      슬롯 가용성 검사(is_slot_available)와 대안 제안(suggest_alternative_slots)에서
      예약이 차지하는 시간 범위를 계산할 때 호출된다.
      초진은 상담·문진이 추가되므로 10분 더 긴 시간이 배정된다.

    Args:
        is_first_visit: 초진이면 True, 재진이면 False

    Returns:
        timedelta: 진료 소요시간 (40분 또는 30분)
    """
    return timedelta(minutes=40) if is_first_visit else timedelta(minutes=30)


def is_change_or_cancel_allowed(appointment_time: datetime | str, now: datetime) -> bool:
    """
    예약 변경 또는 취소가 허용되는지 24시간 규칙으로 판단한다. (F-055, F-057)

    목적:
      당일 급작스러운 취소/변경으로 인한 진료 공백을 방지하기 위해,
      예약 시각 기준 최소 24시간(86,400초) 전에만 변경·취소를 허용한다.

    동작 흐름:
      1. appointment_time을 datetime으로 변환
      2. (예약시각 - 현재시각)이 86,400초 이상인지 비교
      3. True(허용) 또는 False(거부) 반환

    시스템 내 역할:
      _handle_modification()과 _handle_cancellation()에서 공통으로 호출되는
      **24시간 가드**이다. 이 검사를 통과하지 못하면 변경/취소 자체가 차단된다.

    Args:
        appointment_time: 기존 예약 시각 (datetime 또는 ISO 문자열)
        now: 현재 시각

    Returns:
        bool: 24시간 이상 여유가 있으면 True
    """
    return (_ensure_datetime(appointment_time) - now).total_seconds() >= 86400


def is_within_operating_hours(
    request_start: datetime,
    request_end: datetime,
) -> tuple[bool, str]:
    """
    요청된 예약 시간대가 병원 운영시간 내에 있는지 검사한다. (F-052)

    목적:
      병원이 문을 닫거나 점심시간인 때에 예약이 잡히는 것을 방지한다.

    동작 흐름:
      1. 요일 판별 (월=0 ~ 일=6)
      2. 일요일 → 무조건 거부 ("일요일은 휴진")
      3. 토요일 → 09:00~13:00 범위 검사
      4. 평일(월~금) → 09:00~18:00 범위 검사
      5. 평일이면 점심시간(12:30~13:30) 겹침 추가 검사
         - 구간 겹침 공식: max(start1, start2) < min(end1, end2)

    시스템 내 역할:
      is_slot_available()에서 호출되어, 슬롯 가용성 판단의 첫 번째 관문 역할을 한다.

    Rules:
    - Mon-Fri: 09:00-18:00
    - Saturday: 09:00-13:00
    - Sunday: closed
    - Lunch break: 12:30-13:30 (no bookings)

    Args:
        request_start: 예약 시작 시각
        request_end: 예약 종료 시각

    Returns:
        tuple[bool, str]: (운영시간 내 여부, 거부 사유 메시지 — 허용 시 빈 문자열)
    """
    weekday = request_start.weekday()  # 0=Mon ... 6=Sun

    # Sunday — closed
    if weekday == 6:
        return False, "일요일은 휴진입니다."

    start_t = request_start.time()
    end_t = request_end.time()

    # Saturday — 09:00~13:00 (no lunch break to check since close < lunch_end)
    if weekday == 5:
        if start_t < _SATURDAY_OPEN:
            return False, "토요일 진료시간은 오전 9시부터입니다."
        if end_t > _SATURDAY_CLOSE or (end_t == time(0, 0) and request_end.date() > request_start.date()):
            return False, "토요일 진료시간은 오후 1시까지입니다."
        return True, ""

    # Weekday (Mon-Fri) — 09:00~18:00
    if start_t < _WEEKDAY_OPEN:
        return False, "진료시간은 오전 9시부터입니다."
    if end_t > _WEEKDAY_CLOSE or (end_t == time(0, 0) and request_end.date() > request_start.date()):
        return False, "진료시간은 오후 6시까지입니다."

    # Lunch break overlap: booking [start, end) intersects [12:30, 13:30)
    lunch_start = datetime.combine(request_start.date(), _LUNCH_START, tzinfo=request_start.tzinfo)
    lunch_end = datetime.combine(request_start.date(), _LUNCH_END, tzinfo=request_start.tzinfo)
    if max(request_start, lunch_start) < min(request_end, lunch_end):
        return False, "점심시간(12:30~13:30)에는 예약이 불가합니다."

    return True, ""


def is_slot_available(
    request_time: datetime,
    duration: timedelta,
    bookings: list[Booking],
    now: datetime,
    booking_id_to_ignore: str | None = None
) -> tuple[bool, str]:
    """
    특정 시간에 예약이 가능한지 종합적으로 판단한다. (F-052, F-053, F-054)

    목적:
      운영시간, 정원, 시간 겹침을 모두 고려하여 해당 슬롯이 비어 있는지 확인한다.
      이 함수가 True를 반환해야만 예약/변경이 승인된다.

    동작 흐름:
      1. 과거 시간 검사 — 현재 시각보다 이전이면 즉시 거부
      2. 운영시간 검사 (is_within_operating_hours) — F-052
      3. 수정 중인 예약(booking_id_to_ignore)은 충돌 검사에서 제외
      4. 정원 검사 — 동일 시작 시각에 이미 3건 이상이면 거부 (F-053)
      5. 시간 겹침 검사 — 기존 예약과 분 단위라도 겹치면 거부
         - 구간 겹침 공식: max(start1, start2) < min(end1, end2)

    시스템 내 역할:
      _handle_booking()과 _handle_modification()에서 호출되는 **핵심 가용성 판단 함수**이다.
      suggest_alternative_slots()에서도 대안 시간의 유효성을 검증할 때 재사용된다.

    Args:
        request_time: 요청된 예약 시작 시각
        duration: 진료 소요시간 (초진 40분 / 재진 30분)
        bookings: 기존 예약 목록
        now: 현재 시각
        booking_id_to_ignore: 수정 중인 예약의 ID (자기 자신과의 충돌 방지)

    Returns:
        tuple[bool, str]: (가용 여부, 거부 사유 — 가용 시 빈 문자열)
    """
    # Rule: Cannot book appointments in the past.
    if _ensure_datetime(request_time) < now:
        return False, "과거 시간으로는 예약할 수 없습니다."

    request_start = _ensure_datetime(request_time)
    request_end = request_start + duration

    # Rule: Operating hours check (F-052)
    within_hours, hours_reason = is_within_operating_hours(request_start, request_end)
    if not within_hours:
        return False, hours_reason

    # Filter out the booking being modified
    relevant_bookings = [b for b in bookings if b.booking_id != booking_id_to_ignore]

    # 1. Check for capacity (max 3 at the same start time)
    same_time_bookings = sum(1 for b in relevant_bookings if b.start_time == request_start)
    if same_time_bookings >= 3:
        return False, "해당 시간의 예약 정원(3명)이 모두 찼습니다."

    # 2. Check for overlaps using pre-computed start_time / end_time
    for booking in relevant_bookings:
        existing_start = booking.start_time
        existing_end = booking.end_time

        # Check for overlap (any minute of overlap is a conflict)
        if max(request_start, existing_start) < min(request_end, existing_end):
            return False, "해당 시간은 다른 예약과 겹칩니다."

    return True, ""


def suggest_alternative_slots(
    original_time: datetime,
    duration: timedelta,
    bookings: list[Booking],
    now: datetime,
    booking_id_to_ignore: str | None = None
) -> list[datetime]:
    """
    요청 시간이 불가능할 때, 같은 날의 대안 시간대를 1~3개 제안한다. (F-056)

    목적:
      예약이 거부되었을 때 사용자가 바로 다른 시간을 선택할 수 있도록
      가용한 슬롯을 자동으로 탐색하여 제안한다.

    동작 흐름:
      1. 원래 요청 시간의 요일·타임존 파악
      2. 일요일이면 빈 리스트 반환 (휴진)
      3. 해당 요일의 마감 시간 결정 (토요일 13:00 / 평일 18:00)
      4. 원래 시간 이후 가장 가까운 30분 단위 시각부터 탐색 시작
      5. 30분 간격으로 day_end까지 순회하며:
         - 과거 시간이면 건너뜀
         - is_slot_available()로 가용성 확인
         - 가용하면 suggestions에 추가
      6. 최대 3개 찾으면 종료

    시스템 내 역할:
      _handle_booking()과 _handle_modification()에서 슬롯이 불가할 때 호출된다.
      사용자 경험(UX)을 위해 "안 됩니다"로 끝나지 않고 대안을 함께 제시하는 역할이다.

    Args:
        original_time: 원래 요청했던 시각 (탐색 시작점)
        duration: 진료 소요시간
        bookings: 기존 예약 목록
        now: 현재 시각
        booking_id_to_ignore: 수정 중인 예약의 ID

    Returns:
        list[datetime]: 대안 시간 목록 (0~3개)
    """
    suggestions = []

    orig_dt = _ensure_datetime(original_time)
    tz = orig_dt.tzinfo
    weekday = orig_dt.weekday()

    # Sunday — no alternatives
    if weekday == 6:
        return []

    # Determine day_end based on weekday
    close_time = _SATURDAY_CLOSE if weekday == 5 else _WEEKDAY_CLOSE
    day_end = datetime.combine(orig_dt.date(), close_time, tzinfo=tz)

    # Start checking from the next 30-minute interval after the original failed time
    orig = _ensure_datetime(original_time)
    current_time = orig + timedelta(minutes=30 - orig.minute % 30)

    while current_time < day_end and len(suggestions) < 3:
        # Ensure we don't suggest times in the past
        if current_time < now:
            current_time += timedelta(minutes=30)
            continue

        is_available, _ = is_slot_available(current_time, duration, bookings, now, booking_id_to_ignore)
        if is_available:
            suggestions.append(current_time)

        current_time += timedelta(minutes=30)  # Check every half hour

    return suggestions
