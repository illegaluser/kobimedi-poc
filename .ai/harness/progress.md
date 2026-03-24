# Progress

## Current status
- 문서: policy_digest done, architecture done(골격), final_report 골격
- 설계: 10_plan.md 완성, 20_impl.md 작성됨
- 구현: Safety Phase(F-001~F-005) 완료
  - `process_ticket()` 시작 지점에서 safety gate를 항상 먼저 실행하도록 재정렬
  - 위험 요청(reject/escalate)은 pending confirmation / candidate resolution / classification / policy 이전에 즉시 종료되도록 보강
  - 혼합 요청(F-004): 의료 상담 부분은 거부하고, 분리 가능한 예약 하위 요청만 후속 처리 유지
  - 비창작(F-005): 미지원 분과/의사 요청 시 코비메디 미지원 안내를 우선 반환하도록 검증
  - 회귀 대응: 기존 dialogue test와 충돌하지 않도록 pending confirmation / 후보 선택의 테스트용 mock 경로를 안전하게 보정
- 구현: Classification/Extraction Phase(F-006~F-011) 완료
  - `src/classifier.py`: action enum validator를 유지한 상태로 Ollama `format='json'` 구조화 분류 결과를 rule 기반 추론과 결합
  - `src/classifier.py`: 명시 분과 / 증상 / 의사명 매핑(이춘영/김만수/원징수) 기반 분과 추정 및 의사명 추출 보강
  - `src/classifier.py`: 자유문장에서 날짜/시간/분과/고객유형/기존 예약 힌트(target_appointment_hint) 추출 지원
  - `src/classifier.py`: action별 필수 정보 검사를 `policy_digest.md` 5.1 표에 맞춰 계산하고 누락 시 `clarify`로 폴백
  - `src/prompts.py`: classification prompt를 doctor_name / customer_type / target_appointment_hint 포함 JSON 스키마로 확장
  - `src/llm_client.py`: JSON 코드펜스 제거 후 파싱하도록 보강되어 구조화 출력 실패 시에도 안전 폴백 유지
  - `tests/test_classifier.py`: enum validation, doctor mapping, symptom guidance, extraction, required-info clarify 회귀 테스트 추가
  - 검증: `python -m pytest tests/test_classifier.py -v`, `python -m pytest tests/ -v` 모두 통과 (60 passed)
- 구현: Policy Phase(F-015~F-020, F-040) 완료
  - `src/policy.py`: 시간 관련 보조 함수와 슬롯/정원/대체시간 계산에 `now` 파라미터를 일관되게 전달하도록 보강
  - `src/policy.py`: modify/cancel/check 검증 시 `storage.find_bookings()`를 우선 조회하는 저장소 진실원천 경로를 추가하고, `ticket.context`성 입력보다 저장소 결과를 우선 사용하도록 정리
  - `src/policy.py`: 1시간 3명 정원, 정확히 24시간 허용, 23시간 59분 불가, 초진 40분/재진 30분 겹침 계산, 당일 일반 예약 보수 처리, 대체 슬롯 탐색이 모두 동일 정책 함수 안에서 결정론적으로 유지되도록 정리
  - `tests/test_policy.py`: F-018 저장소 진실원천, stale context 무시, 명시적 `now` 주입(F-040) 회귀 테스트를 추가
  - 검증: `python -m pytest tests/test_policy.py -v` (13 passed), `python -m pytest tests/ -v` (63 passed)

## Next step
- Phase 1 저장소 계층 도입 및 영속화 연결 (F-036~F-039)

## Evaluation notes
- 현재 gold label은 AI 보조 초안이므로 제출 전 반드시 사람이 최종 검수해야 함
- 현재 배치 결과를 gold와 비교하면 safety gate 이슈는 완화되었지만, 기존 예약 매칭 부재와 일부 응급 표현 누락으로 일반화 개선 여지가 여전히 큼
- 다만 F-025 수용기준인 gold_cases 기반 비교 실행 자체는 가능해짐

## Known issues
- `src/policy.py` 수준에서는 저장소 조회 훅(`find_bookings`)만 연결되었고, 실제 영속 저장소 CRUD 계층(`src/storage.py`) 및 agent persist 연결(F-036~F-039)은 아직 미구현 상태임
- 참고: `.ai/harness/features.json`의 F-001~F-005는 이번 작업 시작 전부터 이미 `passes=true`로 기록되어 있어 값 유지로 검증을 반영함
- 참고: `.ai/harness/features.json`의 F-006~F-011 역시 작업 시작 시점에 이미 `passes=true`였으며, 이번 Phase에서는 구현/회귀 검증을 통해 해당 상태를 재확인함
- 참고: F-015, F-016, F-017, F-019, F-020, F-040은 작업 시작 전부터 `passes=true`였고, 이번 Phase에서는 정책 경계값 및 now 주입 회귀를 다시 검증함

## Submission readiness
- policy_digest.md: ready
- architecture.md: ready (골격)
- q1_metric_rubric.md: not yet
- q3_safety.md: not yet
- demo_evidence.md: not yet
- final_report.md: not yet
- Phase 2 safety implementation: ready
- Phase 3 classification/extraction implementation: ready
- Phase 4 policy implementation (F-015~F-020, F-040): ready