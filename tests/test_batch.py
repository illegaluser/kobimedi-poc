from __future__ import annotations
import json
from pathlib import Path

from freezegun import freeze_time

from run import run_batch


@freeze_time("2024-01-22 10:00:00")
def test_run_batch_generates_correct_output_and_metrics(tmp_path: Path):
    input_tickets = [
        {
            "ticket_id": "T001",
            "message": "안녕하세요, 내일 오전 10시에 내과 예약하고 싶어요.",
            "customer_name": "김민준",
            "customer_type": "new",
        },
        {
            "ticket_id": "T002",
            "message": "제 예약 좀 확인해주세요.",
            "customer_name": "박서연",
            "customer_type": "revisit",
            "context": {"has_existing_appointment": True},
        },
        {
            "ticket_id": "T003",
            "message": "진료 예약을 취소하고 싶습니다.",
            "customer_name": "이도윤",
            "customer_type": "revisit",
            "context": {"has_existing_appointment": True},
        },
        {"ticket_id": "T004", "message": "피부과 예약 되나요?", "customer_name": "최아름"},
    ]
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(json.dumps(input_tickets, ensure_ascii=False))

    results, metrics = run_batch(str(input_path), str(output_path))

    assert output_path.exists()
    assert len(results) == 4

    # F-081: Schema validation
    for result in results:
        assert "ticket_id" in result
        assert "classified_intent" in result
        assert "department" in result
        assert "action" in result
        assert "response" in result
        assert "confidence" in result
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0
        assert "reasoning" in result
        assert isinstance(result["reasoning"], str)

    # F-082: Dynamic reasoning check
    assert "T001" == results[0]["ticket_id"]
    assert "정책 통과" in results[0]["reasoning"]
    assert "신규" in results[0]["reasoning"]

    assert "T004" == results[3]["ticket_id"]
    assert "지원불가 분과(피부과)" in results[3]["reasoning"]

    # F-091, F-092, F-093: KPI metrics check
    # 배치 모드에서 book_appointment는 확인 없이 즉시 성공 처리됨
    assert metrics.agent_success >= 1
    assert metrics.safe_reject >= 1
