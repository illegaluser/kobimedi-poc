# Progress

## Current status
- 문서: policy_digest, architecture 골격, final_report 골격
- 설계: 10_plan.md 완성
- 구현: Safety Phase(F-001~F-005) 완료
- 구현: Classification/Extraction Phase(F-006~F-011) 완료
- 구현: Policy Phase(F-015~F-020) 완료
- 구현: Dialogue Phase(F-012~F-014) 완료
- 구현: Runtime Phase(F-021~F-024) 완료
  - chat.py: create_session + process_message 호출, 멀티턴 세션 공유
  - run.py: argparse --input/--output, process_ticket 호출, 배치 JSON 출력
  - process_ticket: ticket_id/classified_intent/department/action/response/confidence/reasoning 키 보장
  - confidence/reasoning: 파이프라인 근거 기반 동적 산출 (하드코딩 아님)
- 안정성: Ollama 폴백(F-026) 강화 완료
  - ConnectionRefusedError(미실행), timeout, JSONDecodeError 대응 추가
  - `format='json'` 파싱 실패 시 1회 재시도 후 안전 폴백(`clarify`) 적용
  - 연결 실패/타임아웃 시 `clarify` + "일시적 오류" 메시지 반환
  - 어떤 경우에도 의료 상담 허용이나 거짓 예약 성공으로 이어지지 않도록 안전 우선 처리
- 평가: F-025 gold_cases.json 재생성 완료
  - `data/tickets.json` + `docs/policy_digest.md` 기준 50건 expected_action / expected_department 초안 작성
  - 모든 note에 "AI 보조 생성 초안 — 반드시 사람이 검증해야 함" 명시
  - `python golden_eval/eval.py results.json golden_eval/gold_cases.json` 실행 가능 확인
- 검증: `pytest tests/ -v` 45건 전체 통과
- 추적: `.ai/harness/features.json`에서 F-025/F-026 passes=true 반영

## Next step
- Q4 cal.com 연동(F-028~F-035) 구현
- 문서화(F-027, F-035) 완성

## Evaluation notes
- 현재 gold label은 AI 보조 초안이므로 제출 전 반드시 사람이 최종 검수해야 함
- 현재 배치 결과를 gold와 비교하면 action 정확도는 아직 낮아 일반화 개선 여지가 큼
- 다만 F-025 수용기준인 gold_cases 기반 비교 실행 자체는 가능해짐

## Known issues
- 없음 (현재 테스트는 모두 통과)

## Submission readiness
- policy_digest.md: ready
- architecture.md: not yet
- q1_metric_rubric.md: not yet
- q3_safety.md: not yet
- demo_evidence.md: not yet
- final_report.md: not yet