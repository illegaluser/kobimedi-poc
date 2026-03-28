#!/usr/bin/env python3
"""
scripts/verify_calcom_lifecycle.py — Cal.com 연동 검증 스크립트

chat.py와 동일한 process_message를 사용하여 예약→변경→취소를 수행하고,
각 단계마다 Cal.com API를 직접 조회하여 실제 반영 여부를 검증한다.

1. 신규 진료예약 → Cal.com 예약 생성 확인
2. 예약 변경     → Cal.com 기존 취소 + 신규 생성 확인
3. 예약 취소     → Cal.com 예약 삭제 확인
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from src.agent import create_session, process_message
from src import calcom_client
import src.storage as storage

# ── ANSI ──
G = "\033[1;32m"    # green
R = "\033[1;31m"    # red
C = "\033[1;36m"    # cyan
Y = "\033[1;33m"    # yellow
D = "\033[2m"       # dim
X = "\033[0m"       # reset

NOW = datetime(2026, 4, 6, 2, 0, tzinfo=timezone.utc)  # KST 4/6 11:00
PATIENT_NAME = "홍길동"
PATIENT_PHONE = "010-9876-5432"
PHONE_DIGITS = PATIENT_PHONE.replace("-", "")


# ── 격리 Storage ──
_tmp_dir = None
_original_path = None

def _setup():
    """테스트용 격리 저장소를 생성한다.

    임시 디렉터리에 빈 bookings.json을 만들고,
    storage.DEFAULT_BOOKINGS_PATH를 해당 파일로 교체한다.
    이렇게 하면 테스트가 실제 예약 데이터를 오염시키지 않는다.
    """
    global _tmp_dir, _original_path
    _tmp_dir = tempfile.mkdtemp()
    f = Path(_tmp_dir) / "bookings.json"
    f.write_text("[]", encoding="utf-8")
    _original_path = storage.DEFAULT_BOOKINGS_PATH
    storage.DEFAULT_BOOKINGS_PATH = f

def _teardown():
    """격리 저장소를 정리하고 원래 경로를 복원한다.

    _setup()에서 변경한 DEFAULT_BOOKINGS_PATH를 원래 값으로 되돌리고,
    임시 디렉터리를 삭제한다.
    """
    global _tmp_dir, _original_path
    if _original_path:
        storage.DEFAULT_BOOKINGS_PATH = _original_path
    if _tmp_dir:
        shutil.rmtree(_tmp_dir, ignore_errors=True)


# ── Cal.com 조회 ──
def find_calcom_booking() -> dict | None:
    """테스트 환자의 Cal.com 예약을 찾는다."""
    bookings = calcom_client.list_bookings() or []
    for b in bookings:
        for att in b.get("attendees", []):
            if PHONE_DIGITS in (att.get("email", "") or ""):
                return b
    return None


def cancel_all_test_bookings():
    """테스트 환자의 Cal.com 예약을 모두 취소한다."""
    bookings = calcom_client.list_bookings() or []
    for b in bookings:
        for att in b.get("attendees", []):
            if PHONE_DIGITS in (att.get("email", "") or ""):
                calcom_client.cancel_booking_remote(b.get("uid"))
                break


def format_calcom_booking(b: dict) -> str:
    """Cal.com 예약을 사람이 읽기 쉬운 형태로 포맷한다."""
    start = b.get("start", "")
    try:
        utc_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        kst_dt = utc_dt + timedelta(hours=9)
        date_str = kst_dt.strftime("%Y-%m-%d")
        time_str = kst_dt.strftime("%H:%M")
    except Exception:
        date_str = "?"
        time_str = "?"
    slug = (b.get("eventType") or {}).get("slug", "?")
    uid = b.get("uid", "?")[:12]
    status = b.get("status", "?")
    att_name = (b.get("attendees", [{}])[0]).get("name", "?")
    return f"{date_str} {time_str} KST | {slug} | {att_name} | uid={uid}.. | status={status}"


# ── 챗봇 대화 ──
def send(session: dict, msg: str) -> dict:
    """챗봇에 메시지를 전송하고 결과를 콘솔에 출력한다.

    process_message()를 호출하여 응답을 받은 뒤,
    사용자 입력 · 챗봇 응답 · action 태그를 포맷하여 출력한다.

    Args:
        session: 대화 세션 딕셔너리 (create_session()으로 생성).
        msg: 사용자 메시지 문자열.

    Returns:
        process_message()의 반환값 딕셔너리 (action, response 등 포함).
    """
    r = process_message(msg, session=session, now=NOW)
    action = r.get("action", "?")
    response = r.get("response", "")
    print(f"  {D}> {X}{msg}")
    print(f"    {response}")
    print(f"    {D}[{action}]{X}")
    return r


def respond_to_clarify(session: dict, r: dict) -> dict:
    """clarify 응답이 반복될 때 자동으로 정보를 제공하는 루프.

    챗봇이 clarify(추가 정보 요청)를 반환하면, 응답 내용에 따라
    본인 여부 → 환자 정보 → 확인("네") 순서로 자동 응답한다.
    최대 8회까지 반복하며, 슬롯 마감 등 예외 상황에서는 중단한다.

    Args:
        session: 대화 세션 딕셔너리.
        r: 직전 process_message()의 반환값.

    Returns:
        마지막으로 받은 챗봇 응답 딕셔너리.
    """
    for _ in range(8):
        if r.get("action") != "clarify":
            break
        resp = r.get("response", "")
        if "본인이신가요" in resp:
            r = send(session, "본인이에요")
        elif "연락처" in resp or "성함" in resp:
            r = send(session, f"{PATIENT_NAME} {PATIENT_PHONE}")
        elif "예약할까요" in resp:
            r = send(session, "네")
        elif "마감" in resp:
            print(f"  {R}⚠ 슬롯 마감됨{X}")
            break
        else:
            break
    return r


# ── 검증 함수 ──
def verify_calcom(label: str, expect_exists: bool, expect_time: str | None = None) -> bool:
    """Cal.com에서 테스트 환자의 예약 존재 여부와 시간을 검증한다.

    find_calcom_booking()으로 예약을 조회한 뒤,
    expect_exists=True이면 예약이 존재하는지, False이면 삭제되었는지 확인한다.
    expect_time이 주어지면 예약 시작 시간(KST)이 일치하는지도 검증한다.

    Args:
        label: 검증 단계 설명 (콘솔 출력용).
        expect_exists: True면 예약이 존재해야 통과, False면 없어야 통과.
        expect_time: 기대하는 시작 시간 "HH:MM" (KST). None이면 시간 검증 생략.

    Returns:
        검증 통과 여부 (True/False).
    """
    print(f"\n  {C}── Cal.com 검증: {label} ──{X}")
    b = find_calcom_booking()

    if expect_exists:
        if b is None:
            print(f"  {R}✗ Cal.com에 예약 없음 (기대: 존재){X}")
            return False
        info = format_calcom_booking(b)
        print(f"  {G}✓ Cal.com 예약 확인: {info}{X}")
        if expect_time:
            try:
                utc_dt = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
                kst_dt = utc_dt + timedelta(hours=9)
                actual_time = kst_dt.strftime("%H:%M")
                if actual_time == expect_time:
                    print(f"  {G}✓ 시간 일치: {actual_time}{X}")
                else:
                    print(f"  {R}✗ 시간 불일치: 기대={expect_time} 실제={actual_time}{X}")
                    return False
            except Exception:
                pass
        return True
    else:
        if b is None:
            print(f"  {G}✓ Cal.com에 예약 없음 (기대: 삭제됨){X}")
            return True
        info = format_calcom_booking(b)
        print(f"  {R}✗ Cal.com에 예약 잔존: {info}{X}")
        return False


# ── 메인 ──
def main():
    """Cal.com 연동 검증의 전체 흐름을 실행한다.

    6단계로 구성된 대화형 검증을 수행한다:
      Step 1: 신규 진료예약 (챗봇 대화)
      Step 2: Cal.com 예약 생성 확인 (API 직접 조회)
      Step 3: 예약 변경 (챗봇 대화)
      Step 4: Cal.com 예약 변경 확인 (API 직접 조회)
      Step 5: 예약 취소 (챗봇 대화)
      Step 6: Cal.com 예약 삭제 확인 (API 직접 조회)

    각 단계 사이에 Enter 키 입력을 대기하여 수동으로 진행 속도를 제어할 수 있다.
    테스트 전후로 Cal.com 잔여 예약을 정리하고, 격리 저장소를 사용한다.

    Returns:
        0이면 전체 통과, 1이면 일부 실패.
    """
    # 날짜: NOW 기준 8일 후 평일
    target = NOW.replace(tzinfo=None) + timedelta(days=8)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    date_display = target.strftime("%-m월 %-d일")
    book_time = "10:00"
    modify_time = "11:00"

    print(f"{Y}╔════════════════════════════════════════════════════════╗{X}")
    print(f"{Y}║   Cal.com 연동 검증: 예약 → 변경 → 취소              ║{X}")
    print(f"{Y}╚════════════════════════════════════════════════════════╝{X}")
    print()
    print(f"  환자: {PATIENT_NAME} ({PATIENT_PHONE})")
    print(f"  예약: {date_display} {book_time} 내과")
    print(f"  변경: → {modify_time}")
    print()

    # 환경 체크
    if not os.environ.get("CALCOM_API_KEY"):
        print(f"{R}✗ CALCOM_API_KEY 미설정{X}")
        return 1

    results = []
    _setup()
    try:
        # 사전 정리
        cancel_all_test_bookings()

        session = create_session(customer_name="테스트환자", customer_type="재진", all_appointments=[])

        def wait_key(next_label: str):
            """다음 단계로 넘어가기 전 Enter 키 입력을 대기한다.

            Args:
                next_label: 다음 단계 이름 (안내 메시지에 표시됨).
            """
            print(f"\n  {D}[Enter] 다음 단계로 → {next_label}{X}")
            input()

        # ═══════════════════════════════════════════
        # Step 1: 신규 진료예약
        # ═══════════════════════════════════════════
        print(f"\n{C}{'━' * 56}{X}")
        print(f"{C}  Step 1: 신규 진료예약 ({date_display} 오전 {book_time.split(':')[0]}시){X}")
        print(f"{C}{'━' * 56}{X}")

        r = send(session, f"{date_display} 오전 10시에 내과 진료 예약하고 싶어요")
        r = respond_to_clarify(session, r)
        book_ok = r.get("action") == "book_appointment"
        print(f"\n  {'✅' if book_ok else '❌'} 예약 {'성공' if book_ok else '실패'}")

        wait_key("Cal.com 예약 생성 확인")

        # ═══════════════════════════════════════════
        # Step 2: Cal.com 예약 확인
        # ═══════════════════════════════════════════
        print(f"{C}{'━' * 56}{X}")
        print(f"{C}  Step 2: Cal.com 예약 생성 확인{X}")
        print(f"{C}{'━' * 56}{X}")
        v1 = verify_calcom("신규 예약 생성", expect_exists=True, expect_time=book_time)
        results.append(("예약 생성", book_ok and v1))

        if not book_ok:
            print(f"\n{R}예약 실패 — 이후 단계 건너뜀{X}")
            results.append(("예약 변경", False))
            results.append(("예약 취소", False))
        else:
            wait_key("예약 변경")

            # ═══════════════════════════════════════════
            # Step 3: 예약 변경
            # ═══════════════════════════════════════════
            print(f"{C}{'━' * 56}{X}")
            print(f"{C}  Step 3: 예약 변경 (오전 {modify_time.split(':')[0]}시로){X}")
            print(f"{C}{'━' * 56}{X}")

            r = send(session, "예약 변경할래요")
            r = respond_to_clarify(session, r)
            if r.get("action") == "clarify":
                r = send(session, f"{date_display} 오전 {modify_time.split(':')[0]}시로 변경해주세요")
            modify_ok = r.get("action") == "modify_appointment"
            print(f"\n  {'✅' if modify_ok else '❌'} 변경 {'성공' if modify_ok else '실패'}")

            wait_key("Cal.com 예약 변경 확인")

            # ═══════════════════════════════════════════
            # Step 4: Cal.com 예약 변경 확인
            # ═══════════════════════════════════════════
            print(f"{C}{'━' * 56}{X}")
            print(f"{C}  Step 4: Cal.com 예약 변경 확인{X}")
            print(f"{C}{'━' * 56}{X}")
            v2 = verify_calcom("변경 후 새 예약", expect_exists=True, expect_time=modify_time)
            results.append(("예약 변경", modify_ok and v2))

            wait_key("예약 취소")

            # ═══════════════════════════════════════════
            # Step 5: 예약 취소
            # ═══════════════════════════════════════════
            print(f"{C}{'━' * 56}{X}")
            print(f"{C}  Step 5: 예약 취소{X}")
            print(f"{C}{'━' * 56}{X}")

            r = send(session, "예약 취소해주세요")
            r = respond_to_clarify(session, r)
            cancel_ok = r.get("action") == "cancel_appointment"
            print(f"\n  {'✅' if cancel_ok else '❌'} 취소 {'성공' if cancel_ok else '실패'}")

            wait_key("Cal.com 예약 삭제 확인")

            # ═══════════════════════════════════════════
            # Step 6: Cal.com 예약 삭제 확인
            # ═══════════════════════════════════════════
            print(f"{C}{'━' * 56}{X}")
            print(f"{C}  Step 6: Cal.com 예약 삭제 확인{X}")
            print(f"{C}{'━' * 56}{X}")
            v3 = verify_calcom("취소 후 예약 삭제", expect_exists=False)
            results.append(("예약 취소", cancel_ok and v3))

    finally:
        cancel_all_test_bookings()
        _teardown()

    # ═══════════════════════════════════════════
    # 최종 결과
    # ═══════════════════════════════════════════
    print(f"\n{Y}{'═' * 56}{X}")
    print(f"{Y}  최종 결과{X}")
    print(f"{Y}{'═' * 56}{X}")
    all_passed = True
    for label, ok in results:
        icon = f"{G}✅{X}" if ok else f"{R}❌{X}"
        print(f"  {icon} {label}: {'PASS' if ok else 'FAIL'}")
        if not ok:
            all_passed = False

    print()
    if all_passed:
        print(f"  {G}✅ ALL PASSED — Cal.com 연동 완전 검증 완료{X}")
    else:
        print(f"  {R}❌ SOME FAILED{X}")
    print(f"{Y}{'═' * 56}{X}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
