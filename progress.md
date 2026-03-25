# Project Progress

## Current status
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
- **Proceed to Phase 3**: Dialogue State Machine & Policy Engine 완성.
  - `src/policy.py` 결정론적 정책 검사 (24시간 규칙, 정원, 운영시간) 완성 검증 및 테스트.
  - 멀티턴 대화 흐름: 본인/대리인 확인(F-031), 누락 정보 수집 큐(F-041), clarify_turn_count 상한(F-042) 테스트 보강.
  - `golden_eval/eval.py` 실행 및 Safe Resolution Rate >= 70% 검증.
