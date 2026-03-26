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
    global _tmp_dir, _original_path
    _tmp_dir = tempfile.mkdtemp()
    f = Path(_tmp_dir) / "bookings.json"
    f.write_text("[]", encoding="utf-8")
    _original_path = storage.DEFAULT_BOOKINGS_PATH
    storage.DEFAULT_BOOKINGS_PATH = f

def _teardown():
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
    r = process_message(msg, session=session, now=NOW)
    action = r.get("action", "?")
    response = r.get("response", "")
    print(f"  {D}> {X}{msg}")
    print(f"    {response}")
    print(f"    {D}[{action}]{X}")
    return r


def respond_to_clarify(session: dict, r: dict) -> dict:
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

        # ═══════════════════════════════════════════
        # Step 2: Cal.com 예약 확인
        # ═══════════════════════════════════════════
        print(f"\n{C}{'━' * 56}{X}")
        print(f"{C}  Step 2: Cal.com 예약 생성 확인{X}")
        print(f"{C}{'━' * 56}{X}")
        v1 = verify_calcom("신규 예약 생성", expect_exists=True, expect_time=book_time)
        results.append(("예약 생성", book_ok and v1))

        if not book_ok:
            print(f"\n{R}예약 실패 — 이후 단계 건너뜀{X}")
            results.append(("예약 변경", False))
            results.append(("예약 취소", False))
        else:
            # ═══════════════════════════════════════════
            # Step 3: 예약 변경
            # ═══════════════════════════════════════════
            print(f"\n{C}{'━' * 56}{X}")
            print(f"{C}  Step 3: 예약 변경 (오전 {modify_time.split(':')[0]}시로){X}")
            print(f"{C}{'━' * 56}{X}")

            r = send(session, "예약 변경할래요")
            r = respond_to_clarify(session, r)
            if r.get("action") == "clarify":
                r = send(session, f"{date_display} 오전 {modify_time.split(':')[0]}시로 변경해주세요")
            modify_ok = r.get("action") == "modify_appointment"
            print(f"\n  {'✅' if modify_ok else '❌'} 변경 {'성공' if modify_ok else '실패'}")

            # ═══════════════════════════════════════════
            # Step 4: Cal.com 예약 변경 확인
            # ═══════════════════════════════════════════
            print(f"\n{C}{'━' * 56}{X}")
            print(f"{C}  Step 4: Cal.com 예약 변경 확인{X}")
            print(f"{C}{'━' * 56}{X}")
            v2 = verify_calcom("변경 후 새 예약", expect_exists=True, expect_time=modify_time)
            results.append(("예약 변경", modify_ok and v2))

            # ═══════════════════════════════════════════
            # Step 5: 예약 취소
            # ═══════════════════════════════════════════
            print(f"\n{C}{'━' * 56}{X}")
            print(f"{C}  Step 5: 예약 취소{X}")
            print(f"{C}{'━' * 56}{X}")

            r = send(session, "예약 취소해주세요")
            r = respond_to_clarify(session, r)
            cancel_ok = r.get("action") == "cancel_appointment"
            print(f"\n  {'✅' if cancel_ok else '❌'} 취소 {'성공' if cancel_ok else '실패'}")

            # ═══════════════════════════════════════════
            # Step 6: Cal.com 예약 삭제 확인
            # ═══════════════════════════════════════════
            print(f"\n{C}{'━' * 56}{X}")
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
