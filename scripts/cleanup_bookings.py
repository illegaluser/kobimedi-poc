#!/usr/bin/env python3
"""
scripts/cleanup_bookings.py — Cal.com 예약 일괄 취소 + 로컬 동기화

사용법:
  python scripts/cleanup_bookings.py                # 전체 취소 (확인 프롬프트)
  python scripts/cleanup_bookings.py --dry-run      # 취소 대상만 조회
  python scripts/cleanup_bookings.py --local-only   # 로컬 bookings.json만 초기화
  python scripts/cleanup_bookings.py --force         # 확인 없이 즉시 취소
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from src import calcom_client
from src.storage import load_bookings, save_bookings, DEFAULT_BOOKINGS_PATH


def _print_bookings(bookings: list[dict], source: str):
    """예약 목록을 사람이 읽기 쉬운 형태로 콘솔에 출력한다.

    각 예약의 uid, 제목(분과), 시작 시간, 상태, 환자 이름을 한 줄씩 표시한다.
    예약이 없으면 '예약 없음' 메시지를 출력한다.

    Args:
        bookings: Cal.com 또는 로컬 저장소에서 가져온 예약 딕셔너리 리스트.
        source: 출처 라벨 (예: "Cal.com", "Local") — 출력 헤더에 표시된다.
    """
    if not bookings:
        print(f"  {source}: 예약 없음")
        return
    print(f"  {source}: {len(bookings)}건")
    for i, b in enumerate(bookings, 1):
        uid = b.get("uid") or b.get("id", "?")
        title = b.get("title") or b.get("department", "?")
        start = b.get("start") or b.get("booking_time", "?")
        status = b.get("status", "?")
        name = b.get("attendees", [{}])[0].get("name", "") if b.get("attendees") else b.get("patient_name", "")
        print(f"    {i}. [{status}] {title} | {start} | {name} | uid={uid}")


def cancel_remote_bookings(dry_run: bool = False) -> int:
    """Cal.com 원격 예약 일괄 취소. 취소 성공 건수를 반환."""
    print("\n== Cal.com 원격 예약 조회 ==")
    bookings = calcom_client.list_bookings()

    if bookings is None:
        print("  [ERROR] Cal.com API 호출 실패 (API 키 확인 필요)")
        return 0

    _print_bookings(bookings, "Cal.com")

    if not bookings:
        return 0

    if dry_run:
        print(f"\n  [DRY-RUN] {len(bookings)}건 취소 대상 (실제 취소하지 않음)")
        return 0

    cancelled = 0
    failed = 0
    for b in bookings:
        uid = b.get("uid") or str(b.get("id", ""))
        if not uid:
            print(f"    [SKIP] uid 없음: {b}")
            continue

        result = calcom_client.cancel_booking_remote(uid)
        if result is True:
            title = b.get("title") or b.get("department", "?")
            print(f"    [OK] 취소: {title} (uid={uid})")
            cancelled += 1
        else:
            print(f"    [FAIL] 취소 실패: uid={uid}")
            failed += 1

    print(f"\n  결과: {cancelled}건 취소, {failed}건 실패")
    return cancelled


def reset_local_bookings():
    """로컬 bookings.json을 빈 배열로 초기화."""
    print("\n== 로컬 bookings.json 초기화 ==")
    local = load_bookings()
    active = [b for b in local if b.get("status") == "active"]
    print(f"  현재: {len(local)}건 (active {len(active)}건)")

    save_bookings([])
    print("  초기화 완료: [] (0건)")


def main():
    """스크립트 진입점. CLI 인자를 파싱하여 취소/초기화를 수행한다.

    --dry-run:    취소 대상만 조회하고 실제 취소는 수행하지 않는다.
    --local-only: Cal.com은 건드리지 않고 로컬 bookings.json만 빈 배열로 초기화한다.
    --force:      확인 프롬프트 없이 즉시 원격+로컬 전체 취소를 진행한다.
    인자 없음:    원격 예약 건수를 보여주고 사용자 확인 후 취소+로컬 초기화를 수행한다.
    """
    parser = argparse.ArgumentParser(description="Cal.com 예약 일괄 취소 + 로컬 동기화")
    parser.add_argument("--dry-run", action="store_true", help="취소 대상만 조회 (실제 취소 안 함)")
    parser.add_argument("--local-only", action="store_true", help="로컬 bookings.json만 초기화")
    parser.add_argument("--force", action="store_true", help="확인 없이 즉시 취소")
    args = parser.parse_args()

    print("========================================")
    print("  코비메디 예약 정리 스크립트")
    print("========================================")

    if args.local_only:
        reset_local_bookings()
        return

    # Cal.com 원격 조회/취소
    if args.dry_run:
        cancel_remote_bookings(dry_run=True)

        # 로컬도 표시
        print("\n== 로컬 bookings.json ==")
        local = load_bookings()
        _print_bookings(local, "Local")
        return

    # 확인 프롬프트
    if not args.force:
        bookings = calcom_client.list_bookings()
        if bookings is None:
            print("  [ERROR] Cal.com API 호출 실패")
            return
        local = load_bookings()
        active_local = [b for b in local if b.get("status") == "active"]

        print(f"\n  Cal.com: {len(bookings)}건 원격 예약")
        print(f"  Local:   {len(active_local)}건 active 예약")
        answer = input(f"\n  전체 취소 + 로컬 초기화를 진행하시겠습니까? [y/N] ")
        if answer.strip().lower() != "y":
            print("  취소됨.")
            return

    # 실행
    cancelled = cancel_remote_bookings(dry_run=False)
    reset_local_bookings()

    print("\n== 완료 ==")
    print(f"  Cal.com: {cancelled}건 취소")
    print(f"  Local:   초기화 완료")


if __name__ == "__main__":
    main()
