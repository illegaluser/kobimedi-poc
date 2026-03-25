"""골든 eval: 배치(단일 ticket) + 대화(멀티턴) 평가를 통합 수행합니다.

사용법:
  python golden_eval/eval.py golden_eval/gold_cases.json [--timestamp 2025-03-18T10:00:00+09:00]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path so we can import run / src
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _parse_now(timestamp: str | None) -> datetime:
    if not timestamp:
        from zoneinfo import ZoneInfo
        return datetime(2025, 3, 18, 10, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        from zoneinfo import ZoneInfo
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    return parsed


def _run_batch_case(case: dict) -> dict:
    from src.agent import process_ticket

    now = _parse_now(case.get("timestamp"))
    result = process_ticket(case, all_appointments=[], now=now)
    return result


def _run_dialogue_case(case: dict, verbose: bool = False) -> dict:
    from unittest.mock import patch
    from src.agent import create_session, process_message

    now = _parse_now(case.get("timestamp"))
    session = create_session(all_appointments=[])
    result = {}

    # Mock storage write to avoid file I/O errors in eval
    def _mock_create_booking(appointment_data):
        return {"id": "eval-mock", "status": "active", **(appointment_data or {})}

    with patch("src.agent.create_booking", side_effect=_mock_create_booking):
        for i, turn in enumerate(case["turns"]):
            result = process_message(turn["message"], session, now=now)
            if verbose:
                print(f"    턴 {i+1}: \"{turn['message']}\" → {result.get('action')} | {(result.get('response') or '')[:60]}")

    return result


def evaluate(labels_path: str, verbose: bool = False) -> dict:
    labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))

    batch_cases = [c for c in labels if c.get("eval_type") == "batch"]
    dialogue_cases = [c for c in labels if c.get("eval_type") == "dialogue"]

    stats = {
        "batch_total": len(batch_cases),
        "batch_correct_action": 0,
        "batch_correct_reject": 0,
        "batch_total_reject": 0,
        "batch_correct_escalate": 0,
        "batch_total_escalate": 0,
        "batch_correct_department": 0,
        "batch_total_department": 0,
        "dialogue_total": len(dialogue_cases),
        "dialogue_correct_action": 0,
        "dialogue_correct_department": 0,
        "dialogue_total_department": 0,
    }

    # ── Batch eval ──
    if batch_cases:
        print("=" * 60)
        print(f"  BATCH EVAL ({len(batch_cases)} cases)")
        print("=" * 60)

        for case in batch_cases:
            tid = case["ticket_id"]
            result = _run_batch_case(case)
            actual_action = result.get("action")
            expected_action = case["expected_action"]

            if actual_action == expected_action:
                stats["batch_correct_action"] += 1
            else:
                print(f"  ✗ {tid}: 예상 {expected_action} → 실제 {actual_action}")

            if case.get("expected_reject"):
                stats["batch_total_reject"] += 1
                if actual_action == "reject":
                    stats["batch_correct_reject"] += 1

            if case.get("expected_escalate"):
                stats["batch_total_escalate"] += 1
                if actual_action == "escalate":
                    stats["batch_correct_escalate"] += 1

            if case.get("expected_department"):
                stats["batch_total_department"] += 1
                if result.get("department") == case["expected_department"]:
                    stats["batch_correct_department"] += 1

    # ── Dialogue eval ──
    if dialogue_cases:
        print()
        print("=" * 60)
        print(f"  DIALOGUE EVAL ({len(dialogue_cases)} cases)")
        print("=" * 60)

        for case in dialogue_cases:
            tid = case["ticket_id"]
            if verbose:
                print(f"\n  [{tid}] {case.get('note', '')}")

            try:
                result = _run_dialogue_case(case, verbose=verbose)
            except Exception as e:
                print(f"  ✗ {tid}: 실행 오류 — {type(e).__name__}: {e}")
                continue

            actual_action = result.get("action")
            expected_action = case.get("expected_final_action")

            if actual_action == expected_action:
                stats["dialogue_correct_action"] += 1
            else:
                print(f"  ✗ {tid}: 예상 {expected_action} → 실제 {actual_action}"
                      f"  ({(result.get('response') or '')[:50]})")

            if case.get("expected_department"):
                stats["dialogue_total_department"] += 1
                if result.get("department") == case["expected_department"]:
                    stats["dialogue_correct_department"] += 1

    # ── Summary ──
    print()
    print("=" * 60)
    print("  평가 결과 요약")
    print("=" * 60)

    bt = stats["batch_total"]
    if bt > 0:
        bc = stats["batch_correct_action"]
        print(f"  Batch Action 정확도:     {bc}/{bt} ({bc/bt*100:.1f}%)")
        if stats["batch_total_reject"] > 0:
            br = stats["batch_correct_reject"]
            btr = stats["batch_total_reject"]
            print(f"  Batch Reject 재현율:     {br}/{btr} ({br/btr*100:.1f}%)")
        if stats["batch_total_escalate"] > 0:
            be = stats["batch_correct_escalate"]
            bte = stats["batch_total_escalate"]
            print(f"  Batch Escalate 재현율:   {be}/{bte} ({be/bte*100:.1f}%)")
        if stats["batch_total_department"] > 0:
            bd = stats["batch_correct_department"]
            btd = stats["batch_total_department"]
            print(f"  Batch Department 정확도: {bd}/{btd} ({bd/btd*100:.1f}%)")

    dt = stats["dialogue_total"]
    if dt > 0:
        dc = stats["dialogue_correct_action"]
        print(f"  Dialogue Action 정확도:  {dc}/{dt} ({dc/dt*100:.1f}%)")
        if stats["dialogue_total_department"] > 0:
            dd = stats["dialogue_correct_department"]
            dtd = stats["dialogue_total_department"]
            print(f"  Dialogue Dept 정확도:    {dd}/{dtd} ({dd/dtd*100:.1f}%)")

    total = bt + dt
    total_correct = stats["batch_correct_action"] + stats["dialogue_correct_action"]
    if total > 0:
        print(f"  ─────────────────────────────────")
        print(f"  전체 Action 정확도:      {total_correct}/{total} ({total_correct/total*100:.1f}%)")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Golden eval runner")
    parser.add_argument("labels", help="Path to gold_cases.json")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-turn dialogue details")
    parser.add_argument("--timestamp", help="Override reference timestamp (ISO format)")
    args = parser.parse_args()

    evaluate(args.labels, verbose=args.verbose)


if __name__ == "__main__":
    main()
