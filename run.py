from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from src.agent import process_ticket


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Kobimedi batch ticket processing")
    parser.add_argument("--input", required=True, help="Path to input tickets JSON")
    parser.add_argument("--output", required=True, help="Path to output results JSON")
    return parser.parse_args(argv)


def _parse_now(timestamp: str | None) -> datetime:
    if not timestamp:
        return datetime.now(timezone.utc)

    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def run_batch(input_path: str, output_path: str) -> list[dict]:
    tickets = json.loads(Path(input_path).read_text(encoding="utf-8"))
    results: list[dict] = []

    for ticket in tickets:
        result = process_ticket(ticket, now=_parse_now(ticket.get("timestamp")))
        results.append(
            {
                "ticket_id": result.get("ticket_id") or ticket.get("ticket_id"),
                "classified_intent": result.get("classified_intent"),
                "department": result.get("department"),
                "action": result.get("action"),
                "response": result.get("response"),
                "confidence": result.get("confidence"),
                "reasoning": result.get("reasoning"),
            }
        )

    Path(output_path).write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return results


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    run_batch(args.input, args.output)


if __name__ == "__main__":
    main()