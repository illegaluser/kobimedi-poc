#!/usr/bin/env python3
"""
scripts/test_booking_lifecycle.py — 예약 생명주기 통합 테스트

1명의 환자가 진료예약 → 예약변경 → 예약취소를 순서대로 수행하는
전체 플로우를 실제 Ollama LLM + Cal.com API로 검증한다.

사용법:
  python scripts/test_booking_lifecycle.py                          # 기본 (4/14 오전 10시)
  python scripts/test_booking_lifecycle.py --date 4월15일 --time 오후3시  # 날짜/시간 지정
  python scripts/test_booking_lifecycle.py --new-time 오후4시        # 변경 목표 시간 지정
  python scripts/test_booking_lifecycle.py --dept 이비인후과          # 분과 지정

필수 환경:
  - Ollama 구동 중 (qwen3-coder:30b)
  - .env에 CALCOM_API_KEY + 분과별 Event Type ID 설정
"""
from __future__ import annotations

import argparse
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


# ── 환경 체크 ──
def _check_ollama() -> bool:
    """Ollama LLM 서버가 구동 중인지 확인한다.

    ollama.list()를 호출하여 정상 응답이 오면 True를 반환한다.
    연결 실패 등 예외 발생 시 False를 반환한다.

    Returns:
        Ollama 사용 가능 여부 (True/False).
    """
    try:
        import ollama
        ollama.list()
        return True
    except Exception:
        return False


def _check_calcom() -> bool:
    """Cal.com API 키가 환경변수에 설정되어 있는지 확인한다.

    CALCOM_API_KEY 환경변수의 존재 여부만 검사하며,
    실제 API 호출 유효성은 검증하지 않는다.

    Returns:
        API 키 설정 여부 (True/False).
    """
    import os
    return bool(os.environ.get("CALCOM_API_KEY"))


# ── 격리 Storage ──
_tmp_dir = None
_original_path = None


def _setup_storage():
    """테스트용 격리 저장소를 생성한다.

    임시 디렉터리에 빈 bookings.json 파일을 만들고,
    storage.DEFAULT_BOOKINGS_PATH를 해당 파일로 교체한다.
    이렇게 하면 테스트가 실제 운영 데이터를 오염시키지 않는다.
    """
    global _tmp_dir, _original_path
    _tmp_dir = tempfile.mkdtemp()
    test_file = Path(_tmp_dir) / "bookings.json"
    test_file.write_text("[]", encoding="utf-8")
    _original_path = storage.DEFAULT_BOOKINGS_PATH
    storage.DEFAULT_BOOKINGS_PATH = test_file


def _teardown_storage():
    """격리 저장소를 정리하고 원래 경로를 복원한다.

    _setup_storage()에서 변경한 DEFAULT_BOOKINGS_PATH를 원래 값으로 되돌리고,
    임시 디렉터리를 삭제한다.
    """
    global _tmp_dir, _original_path
    if _original_path:
        storage.DEFAULT_BOOKINGS_PATH = _original_path
    if _tmp_dir:
        shutil.rmtree(_tmp_dir, ignore_errors=True)


# ── Cal.com 정리 ──
def _cleanup_calcom(patient_phone: str):
    """테스트 환자의 Cal.com 잔여 예약을 취소한다."""
    phone_digits = patient_phone.replace("-", "")
    try:
        bookings = calcom_client.list_bookings() or []
        cleaned = 0
        for b in bookings:
            for att in b.get("attendees", []):
                if phone_digits in (att.get("email", "") or ""):
                    uid = b.get("uid")
                    if uid:
                        calcom_client.cancel_booking_remote(uid)
                        cleaned += 1
                    break
        if cleaned:
            print(f"  [정리] Cal.com 잔여 예약 {cleaned}건 취소")
    except Exception as e:
        print(f"  [경고] Cal.com 정리 실패: {e}")


# ── 테스트 실행 ──
NOW = datetime(2026, 4, 6, 2, 0, tzinfo=timezone.utc)  # KST 4/6 11:00 월요일

PATIENT_NAME = "홍길동"
PATIENT_PHONE = "010-9876-5432"


def send(session: dict, msg: str, phase: str = "") -> dict:
    """메시지 전송 + 결과 출력."""
    r = process_message(msg, session=session, now=NOW)
    action = r.get("action", "?")
    response = r.get("response", "")
    icon = {"book_appointment": "✅", "modify_appointment": "✅",
            "cancel_appointment": "✅", "clarify": "💬", "reject": "❌",
            "escalate": "⚠️"}.get(action, "❓")
    print(f"  {icon} 사용자: {msg}")
    print(f"     챗봇: {response}")
    print(f"     [{action}]")
    print()
    return r


def respond_to_clarify(session: dict, r: dict, max_turns: int = 6) -> dict:
    """identity 수집 루프 (proxy → 본인 → 연락처)를 자동 응답한다.

    챗봇이 clarify(추가 정보 요청)를 반환할 때마다 응답 내용을 분석하여
    본인 여부, 이름+연락처를 자동 제공한다.
    clarify가 아닌 다른 action이 나오거나 max_turns에 도달하면 종료한다.

    Args:
        session: 대화 세션 딕셔너리.
        r: 직전 process_message()의 반환값.
        max_turns: 최대 자동 응답 횟수 (기본 6회).

    Returns:
        마지막으로 받은 챗봇 응답 딕셔너리.
    """
    for _ in range(max_turns):
        if r.get("action") != "clarify":
            break
        resp = r.get("response", "")
        if "본인이신가요" in resp:
            r = send(session, "본인이에요")
        elif "연락처" in resp or "성함" in resp:
            r = send(session, f"{PATIENT_NAME} {PATIENT_PHONE}")
        else:
            break
    return r


def run_test(dept: str, date_text: str, time_text: str, new_time_text: str) -> bool:
    """예약→변경→취소 전체 플로우를 실행하고 성공 여부를 반환한다.

    3개 Phase를 순서대로 수행한다:
      Phase 1 - 진료예약: 날짜/시간/분과를 지정하여 새 예약을 생성한다.
                슬롯 마감 시 응답에서 대안 슬롯을 추출하여 재시도한다.
      Phase 2 - 예약변경: 기존 예약의 시간을 new_time_text로 변경한다.
      Phase 3 - 예약취소: 예약을 취소한다.

    Args:
        dept: 진료과 이름 (예: "내과", "이비인후과").
        date_text: 예약 날짜 문자열 (예: "4월14일").
        time_text: 예약 시간 문자열 (예: "오전 10시").
        new_time_text: 변경 목표 시간 문자열 (예: "오전 11시").

    Returns:
        3개 Phase 모두 성공하면 True, 하나라도 실패하면 False.
    """
    session = create_session(customer_name="테스트환자", customer_type="재진", all_appointments=[])
    results = {"book": False, "modify": False, "cancel": False}

    # ─── Phase 1: 진료예약 ───
    print("━" * 60)
    print(f"  Phase 1: 진료예약 ({date_text} {time_text} {dept})")
    print("━" * 60)
    r = send(session, f"{date_text} {time_text}에 {dept} 진료 예약하고 싶어요")
    r = respond_to_clarify(session, r)

    # 확인 질문에 "네"
    if r.get("action") == "clarify" and "예약할까요" in r.get("response", ""):
        r = send(session, "네")

    # 슬롯 마감 시 대안 선택
    if r.get("action") == "clarify" and "마감" in r.get("response", ""):
        print("  ⚠️ 요청 슬롯 마감 — 대안 슬롯으로 재시도")
        # 가용 슬롯에서 첫 번째 선택
        resp = r.get("response", "")
        import re
        slot_match = re.search(r"(\d{2}:\d{2})", resp)
        if slot_match:
            alt_time = slot_match.group(1)
            hour = int(alt_time.split(":")[0])
            minute = alt_time.split(":")[1]
            ampm = "오전" if hour < 12 else "오후"
            h12 = hour if hour <= 12 else hour - 12
            alt_text = f"{date_text} {ampm} {h12}시" + (f" {minute}분" if minute != "00" else "")
            r = send(session, f"{alt_text}로 예약해주세요")
            r = respond_to_clarify(session, r)
            if r.get("action") == "clarify" and "예약할까요" in r.get("response", ""):
                r = send(session, "네")

    results["book"] = r.get("action") == "book_appointment"
    print(f"  {'✅ 예약 성공' if results['book'] else '❌ 예약 실패'}")
    print()

    if not results["book"]:
        return False

    # ─── Phase 2: 예약변경 ───
    print("━" * 60)
    print(f"  Phase 2: 예약변경 ({new_time_text}로)")
    print("━" * 60)
    r = send(session, "예약 변경할래요")
    r = respond_to_clarify(session, r)

    # 새 날짜/시간 제시
    if r.get("action") == "clarify":
        r = send(session, f"{date_text} {new_time_text}로 변경해주세요")

    results["modify"] = r.get("action") == "modify_appointment"
    print(f"  {'✅ 변경 성공' if results['modify'] else '❌ 변경 실패'}")
    print()

    # ─── Phase 3: 예약취소 ───
    print("━" * 60)
    print("  Phase 3: 예약취소")
    print("━" * 60)
    r = send(session, "예약 취소해주세요")
    r = respond_to_clarify(session, r)

    results["cancel"] = r.get("action") == "cancel_appointment"
    print(f"  {'✅ 취소 성공' if results['cancel'] else '❌ 취소 실패'}")
    print()

    return all(results.values())


def main():
    """스크립트 진입점. CLI 인자를 파싱하고 통합 테스트를 실행한다.

    실행 순서:
      1. 환경 체크 (Ollama 구동 여부, CALCOM_API_KEY 설정 여부)
      2. 격리 저장소 생성 (_setup_storage)
      3. Cal.com 잔여 예약 정리 (_cleanup_calcom)
      4. run_test()로 예약→변경→취소 플로우 실행
      5. 정리 (Cal.com 잔여 취소 + 격리 저장소 해제)
      6. 결과 출력

    Returns:
        0이면 전체 통과, 1이면 실패 또는 환경 미충족.
    """
    parser = argparse.ArgumentParser(
        description="예약 생명주기 통합 테스트 (진료예약 → 예약변경 → 예약취소)")
    parser.add_argument("--date", default=None, help="예약 날짜 (예: '4월15일', 기본: 8일 후 평일)")
    parser.add_argument("--time", default="오전 10시", help="예약 시간 (예: '오후3시', 기본: 오전 10시)")
    parser.add_argument("--new-time", default="오전 11시", help="변경 목표 시간 (기본: 오전 11시)")
    parser.add_argument("--dept", default="내과", help="진료과 (기본: 내과)")
    args = parser.parse_args()

    # 날짜 기본값: NOW 기준 8일 후 평일
    if args.date is None:
        target = NOW.replace(tzinfo=None) + timedelta(days=8)
        while target.weekday() >= 5:
            target += timedelta(days=1)
        args.date = target.strftime("%-m월 %-d일")

    print("╔══════════════════════════════════════════════════════════╗")
    print("║   코비메디 예약 생명주기 통합 테스트                    ║")
    print(f"║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                              ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    # 환경 체크
    ollama_ok = _check_ollama()
    calcom_ok = _check_calcom()
    print(f"  환경: Ollama={'✅' if ollama_ok else '❌'}  Cal.com={'✅' if calcom_ok else '❌'}")
    print(f"  환자: {PATIENT_NAME} ({PATIENT_PHONE})")
    print(f"  예약: {args.date} {args.time} {args.dept}")
    print(f"  변경: → {args.new_time}")
    print()

    if not ollama_ok:
        print("❌ Ollama가 구동되지 않아 테스트를 실행할 수 없습니다.")
        return 1
    if not calcom_ok:
        print("❌ CALCOM_API_KEY가 설정되지 않아 테스트를 실행할 수 없습니다.")
        return 1

    # 테스트 실행
    _setup_storage()
    try:
        _cleanup_calcom(PATIENT_PHONE)
        passed = run_test(args.dept, args.date, args.time, args.new_time)
    finally:
        _cleanup_calcom(PATIENT_PHONE)
        _teardown_storage()

    print("═" * 60)
    if passed:
        print("  ✅ ALL PASSED — 예약→변경→취소 전체 플로우 성공")
    else:
        print("  ❌ FAILED — 일부 단계에서 실패")
    print("═" * 60)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
