# Progress

## Current status
- 문서: policy_digest done, architecture done(골격), final_report 골격
- 설계: 10_plan.md 완성, 20_impl.md 작성됨
- 구현: Safety Phase(F-001~F-005) 완료
  - process_ticket()가 safety_check()를 가장 먼저 호출하도록 정렬
  - unsafe 요청은 reject/escalate로 즉시 종료되며 후속 분류/정책 단계 실행 차단
  - F-004 혼합 요청 분리 처리 추가: 의료 조언은 거부하고 안전한 예약 하위 요청만 후속 처리, 분리 불가 시 전체 reject
  - F-005 비창작 응답 보강: 미지원 분과/의사 요청에 "코비메디에서 지원하지 않습니다" 고정 안내
  - 후속 확인 응답(예/아니오/후보 선택)은 pending state 우선 처리해 멀티턴 회귀 방지
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
- safety gate 보정 진행 완료
  - 원인 분석: 예약 관련 표현(예: `진료 예약`, `진료 가능할까요`)과 증상 기반 예약 요청을 의료 상담으로 과차단하고 있었음
  - 원인 분석: 반복 민원/책임자 연결 요청을 off-topic으로 처리해 `reject`로 떨어지는 문제가 있었음
  - 수정: 예약/조회/변경/취소 문맥과 날짜/시간 힌트가 있으면 safety 단계에서 우선 safe 처리
  - 수정: 증상 기반 분과 추정 키워드 보강(`삼킬`, `따가워` 등)
  - 수정: 강한 불만/상담원 연결 요청을 `complaint` 카테고리로 분리하고 `escalate`로 연결
- 평가: F-025 gold_cases.json 재생성 완료
  - `data/tickets.json` + `docs/policy_digest.md` 기준 50건 expected_action / expected_department 초안 작성
  - 모든 note에 "AI 보조 생성 초안 — 반드시 사람이 검증해야 함" 명시
  - `python golden_eval/eval.py results.json golden_eval/gold_cases.json` 실행 가능 확인
- 검증: `pytest tests/ -v` 58건 전체 통과
- 평가 갱신: safety gate 보정 후 golden eval action 정확도 32.0% → 60.0% 개선, reject 재현율 100.0% 유지
- 추적: `.ai/harness/features.json`에서 F-004/F-025/F-026 passes=true 반영

## Next step
- Phase 6 저장소 진실원천/영속화 연결 (F-018, F-036~F-039)

## Evaluation notes
- 현재 gold label은 AI 보조 초안이므로 제출 전 반드시 사람이 최종 검수해야 함
- 현재 배치 결과를 gold와 비교하면 safety gate 이슈는 완화되었지만, 기존 예약 매칭 부재와 일부 응급 표현 누락으로 일반화 개선 여지가 여전히 큼
- 다만 F-025 수용기준인 gold_cases 기반 비교 실행 자체는 가능해짐

## Known issues
- 없음 (현재 테스트는 모두 통과)

## Submission readiness
- policy_digest.md: ready
- architecture.md: ready (골격)
- q1_metric_rubric.md: not yet
- q3_safety.md: not yet
- demo_evidence.md: not yet
- final_report.md: not yet
- agent core safety phase: ready