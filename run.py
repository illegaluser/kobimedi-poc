"""
코비메디 예약 챗봇 — 배치 처리 실행 스크립트.

입력 JSON 파일에 담긴 티켓 목록을 순차적으로 process_ticket()에 전달하고,
결과를 출력 JSON 파일에 저장한다.

사용법:
    python run.py --input tickets.json --output results.json
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from src.agent import process_ticket
from src.metrics import get_metrics, KpiMetrics


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI 인자(--input, --output)를 파싱하여 Namespace로 반환한다."""
    parser = argparse.ArgumentParser(description="Run Kobimedi batch ticket processing")
    parser.add_argument("--input", required=True, help="Path to input tickets JSON")
    parser.add_argument("--output", required=True, help="Path to output results JSON")
    return parser.parse_args(argv)


def _parse_now(timestamp: str | None) -> datetime:
    """ISO 형식 타임스탬프 문자열을 datetime으로 변환한다. None이면 현재 UTC 시각을 반환."""
    if not timestamp:
        return datetime.now(timezone.utc)

    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _resolve_ticket_now(ticket: dict, now: datetime | str | None = None) -> datetime:
    """티켓의 기준 시각을 결정한다. now 인자 → 티켓 timestamp → 현재 UTC 순으로 폴백."""
    if isinstance(now, datetime):
        return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    if isinstance(now, str):
        return _parse_now(now)
    return _parse_now(ticket.get("timestamp"))


def run_batch(input_path: str, output_path: str, now: datetime | str | None = None) -> tuple[list[dict], KpiMetrics]:
    """입력 JSON의 티켓을 순차 처리하고 결과를 출력 JSON에 저장한다. 처리 후 KPI 메트릭을 반환."""
    metrics = get_metrics()
    metrics.__init__()  # Reset metrics

    tickets = json.loads(Path(input_path).read_text(encoding="utf-8"))
    results: list[dict] = []

    for ticket in tickets:
        result = process_ticket(ticket, now=_resolve_ticket_now(ticket, now=now))
        results.append(result)

    Path(output_path).write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    print("Batch processing complete. Metrics:")
    print(json.dumps(metrics.as_dict(), indent=2))
    
    return results, metrics


def main(argv: list[str] | None = None) -> None:
    """CLI 진입점: 인자를 파싱하여 run_batch()를 실행한다."""
    args = _parse_args(argv)
    run_batch(args.input, args.output)


if __name__ == "__main__":
    main()