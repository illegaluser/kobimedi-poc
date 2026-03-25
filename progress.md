# Project Progress

## Current status
- **Phase 6 Complete**: 배치 런타임 및 출력 데이터 계약 구현 완료.
  - `run.py` — 배치 모드 실행기(`run_batch`) 및 결과 출력 로직.
  - `src/agent.py` — `_build_runtime_fields` 및 `_build_reasoning`을 통한 동적 결과 생성.
  - `src/metrics.py` — KPI 집계용 `KpiMetrics` 및 `record_kpi_event` 구현.
    - **F-081**: `results.json`에 `ticket_id`, `classified_intent`, `department`, `action`, `response`, `confidence`, `reasoning` 키 포함 스키마 준수.
    - **F-082**: `reasoning` 필드에 Safety/Storage/Policy 판정 이력 동적 문자열로 반영.
    - **F-091**: `agent_success` 이벤트 계측.
    - **F-092**: `safe_reject`, `agent_soft_fail_clarify`, `agent_hard_fail` 이벤트 계측.
    - **F-093**: Unsafe Medical Answer Rate = 0% 테스트 통과 (`test_safety.py`).
    - **F-094**: Safe Resolution Rate 지표 계측 준비 완료.
  - `pytest tests/test_batch.py` 1/1 통과.
  - `features.json`에서 F-081, F-082, F-091, F-092, F-093, F-094의 `"passes": true` 반영.

- **Phase 5 Complete**: 결정론적 예약 정책 엔진 구현 완료.
  - `src/policy.py` — 순수 Python으로 정책 로직 구현 (LLM 호출 없음).
    - **F-040**: 모든 시간 기반 함수가 `now: datetime` 파라미터를 명시적으로 받아 Mocking 테스트 호환.
    - **F-053**: 동일 시작 시간 기준 예약 정원(3명) 초과 시 차단.
    - **F-054**: 초진 40분/재진 30분 차등 슬롯 적용 및 겹침 방지.
    - **F-055**: `(appointment_time - now).total_seconds() >= 86400` 로직으로 24시간 변경/취소 경계 정확히 판별.
    - **F-056**: 슬롯 불가 시 `suggest_alternative_slots` 통해 해당일 다른 가용 시간 1~3개 제안.
    - **F-057**: 당일 신규 예약은 24시간 규칙을 적용받지 않고, 일반 슬롯/정원 규칙만으로 처리.
  - `src/models.py` — `Ticket`, `Booking`, `PolicyResult` 등 데이터 클래스 정의.
  - `pytest tests/test_policy.py` 14/14 통과.
  - `features.json`에서 F-040, F-051~F-057의 `"passes": true` 반영.

- **Phase 4 Complete**: 멀티턴 대화 상태머신 및 본인/대리인 검증 구현 완료.
  - `src/agent.py` — 멀티턴 대화 상태머신 완성:
    - **F-031**: 예약 의도 확정 시 `is_proxy_booking=None`이면 "본인이신가요?" clarify 우선 발행. 슬롯(날짜/시간) 수집보다 선행.
    - **F-032**: 본인(`is_proxy_booking=False`) 확인 후 본인 성명+연락처 수집, ticket.customer_name은 힌트로만 사용.
    - **F-033**: 대리인(`is_proxy_booking=True`) 확인 후 **환자 본인**의 성명+연락처를 별도 수집; 확보 전 예약 진행 금지.
    - **F-041**: `pending_missing_info_queue` 리스트로 누락 필드 관리; 이미 채워진 슬롯 재질문 금지.
    - **F-042**: `clarify_turn_count` 4 초과 시 핵심 정보 미확보 → `escalate` 강제 전환.
    - **F-043**: `accumulated_slots`(date/time/dept) + `is_proxy_booking`, `patient_contact` 세션 내 유지.
    - **F-044**: 매 턴 전체 누적 상태 기반 action 재평가.
    - **F-045**: 슬롯 불가 시 `pending_alternative_slots` 상태로 전환, 선택 대기.
    - **F-046**: `pending_confirmation=True` 상태로 "예약할까요?" 물은 뒤 "네" 응답 시에만 `create_booking()` 호출(영속화).
    - **F-047**: clarify-first 원칙 — 복구 가능한 불확실성은 escalate 전 clarify로 처리.
    - **F-048**: `chat.py`·`run.py` 모두 `src/agent.py` 동일 로직 공유 검증.
  - `pytest tests/test_dialogue.py` 9/9 통과.
  - `features.json`에서 F-031~F-033, F-041~F-048의 `"passes": true` 반영.

- **Phase 3 Complete**: 의도 분류 및 정보 추출 로직 구현 완료.
  - `src/classifier.py` — `classify_intent()` 완성:
    - **F-011**: 7개 action enum 엄수 (`_normalize_action_value` 검증, 무효값 → rule fallback).
    - **F-012**: 불확실 의도 → clarify 폴백 (missing_info 非空 시 action=clarify 자동 전환).
    - **F-013**: confidence/reasoning 하드코딩 없음 — 파이프라인 결과 기반으로 계산.
    - **F-014**: `classified_intent` 필드 추가 — 사용자 원래 의도를 보존; `action`은 시스템 최종 동작으로 구분.
    - **F-021**: 날짜/시간 추출 — 오늘/내일/모레/글피/요일/절대날짜/상대시간 지원.
    - **F-022**: 분과 추출 — 이비인후과/내과/정형외과 + 의사명 매핑.
    - **F-023**: 환자 성명 추출 — 자유문장 패턴 매칭.
    - **F-024**: 전화번호 추출 — 010-XXXX-XXXX 정규식.
    - **F-025**: 생년월일 추출 — 동명이인 해소용 보조 식별자.
    - **F-026**: 대리 예약 선제 감지 — "엄마", "대신", "가족" 포함 시 즉시 `proxy_booking=true`.
    - **F-027**: 응급/급성 통증 신호 추출 — `is_emergency` 플래그.
    - **F-028**: 기존 예약 참조 추출 — `target_appointment_hint` (변경/취소/확인용).
    - **F-029**: 증상 키워드 추출 → 분과 추천 (진단 금지).
    - **F-030**: customer_type 힌트 수용; 최종 판정은 저장소.
  - `src/llm_client.py` — `chat_json()` 완성:
    - **F-083**: Ollama 호출 시 `format='json'` 강제.
    - **F-084**: JSON 파싱 실패/Timeout → `clarify` fallback 반환, 거짓 성공 없음.
  - `src/prompts.py` — CLASSIFICATION_SYSTEM_PROMPT에 "Return ONLY valid JSON. Do not use markdown code blocks like ```json" 추가.
  - `pytest tests/test_classifier.py` 20/20 통과.
  - `features.json`에서 F-011~F-014, F-021~F-030, F-083, F-084의 `"passes": true` 반영.

- **Phase 2 Complete**: Safety-First 게이트웨이 구현 및 단위 테스트 완료.
  - `src/classifier.py` 내 `safety_check()` 로직 구현 완료:
    - **F-001**: Safety gate가 `process_ticket()` 파이프라인 최선행에서 실행됨. safety gate 차단 시 LLM 호출 생략.
    - **F-002**: 의료 상담(진단/약물/치료법) 즉시 reject — `MEDICAL_ADVICE_PATTERNS` 정규식 기반 결정론 처리.
    - **F-003**: 잡담/목적 외 사용 즉시 reject — `OFF_TOPIC_PATTERNS` 기반.
    - **F-004**: 프롬프트 인젝션 즉시 reject — `INJECTION_PATTERNS` 기반.
    - **F-005**: 타 환자 개인정보 요청 즉시 reject — `PRIVACY_REQUEST_PATTERNS` 기반.
    - **F-006**: 의료+예약 혼합 요청 분리 로직 — 예약 하위 문장 추출 후 통과, 분리 불가 시 전체 reject.
    - **F-007**: 증상 기반 분과 안내 허용, 진단 텍스트 응답 차단 — `mixed_department_guidance` 플래그 + 안내 메시지 분리.
    - **F-008**: 급성 통증/응급 상황 즉시 escalate — `EMERGENCY_PATTERNS` 기반.
    - **F-009**: 화난 고객/상담원 요청 즉시 escalate — `COMPLAINT_ESCALATION_PATTERNS` 기반.
    - **F-010**: 의사 개인정보/보험비용 문의 즉시 escalate — `OPERATIONAL_ESCALATION_PATTERNS`, `DOCTOR_CONTACT_PATTERNS` 기반.
  - LLM fallback: Ollama 오류(ConnectionRefusedError, TimeoutError) → `classification_error` → clarify 응답. 기타 예외 → reject.
  - `pytest tests/test_safety.py` 35/35 통과.
  - `features.json`에서 F-001~F-010의 `"passes": true` 반영.

- **Phase 1 Complete**: `src/storage.py` 구현 및 단위 테스트 완료.
  - `data/bookings.json`을 진실원천으로 사용하는 예약 저장소 모듈 구현.
  - 전화번호 우선 환자 식별(`find_bookings(patient_contact=...)`, `resolve_customer_type_from_history(patient_contact=...)`).
  - 필수 필드 검증: id, patient_name, patient_contact, is_proxy_booking, booking_time, department, customer_type, status.
  - 예외 폴백: JSONDecodeError/파일 없음 → `[]` 반환, 저장 실패 → `False` 반환 후 `StorageWriteError` 발생.
  - 동시성 방어: 최종 저장 직전 저장소 재읽기(recheck) 및 중복/정원 검증.
  - `pytest tests/test_storage.py` 11/11 통과.
  - `features.json`에서 F-034, F-035, F-036, F-037, F-038, F-039, F-061, F-062, F-063, F-064, F-065, F-066, F-067의 `"passes": true` 반영.

## Next step
- **Proceed to Final Phase**: 골든 평가 및 최종 문서화.
  - `golden_eval/eval.py` 실행 및 Safe Resolution Rate >= 70% 검증.
  - `docs/q1_metric_rubric.md`, `docs/q3_safety.md`, `docs/final_report.md` 최신화 (F-101~F-103).
