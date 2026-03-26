# 구현 프롬프트 원문

> 과제 요구사항: *"코딩 에이전트를 사용한 경우, 실행에 사용한 플랜/프롬프트/harness를 별첨으로 공유해주세요."*

아래는 각 구현 Phase에서 Claude Code (claude-sonnet-4-6)에 전달한 프롬프트 원문입니다. 모든 프롬프트는 동일한 4부 구성(역할 부여 → 참조 문서 → 구현 상세 → 공통 규칙)을 따릅니다.

---

## Phase 1: 영속 저장소 및 환자 식별 진실원천 구축 (Storage & Identity)

```
당신의 역할은 코비메디 PoC의 코어 데이터베이스 엔지니어이다.

[필수 참조 문서]
- docs/architecture.md (Section 8. 예약 저장소)
- docs/policy_digest.md (Section 7. 저장소 정책)
- .ai/handoff/10_plan.md (Section 6. storage.py 변경 사양)
- features.json (F-034~F-039, F-061~F-067)

[작업 목표]
data/bookings.json을 진실원천으로 삼는 `src/storage.py`를 구현하라.

[구현 상세 및 예외 방어 (Critical)]
1. 진실원천 확립: 모든 상태 검증은 bookings.json 기준이다 (F-061). ticket.context는 보조 힌트로만 써라 (F-067).
2. 환자 식별 1순위: `find_bookings()`에 `patient_contact` 필터를 추가하라 (F-062). `resolve_customer_type_from_history()`도 이름이 아닌 전화번호 우선으로 확장하라 (F-034, F-035, F-063).
3. 필수 필드: `create_booking()` 시 id(uuid/순차생성), patient_name, patient_contact, is_proxy_booking, booking_time, department, customer_type, status(active/cancelled)를 반드시 레코드에 포함하라 (F-066, F-038).
4. 예외 폴백 (Robustness): `load_bookings()` 시 JSONDecodeError가 발생하거나 파일이 없으면 빈 배열 `[]`을 반환하라. `save_bookings()` 시 디스크 용량 부족이나 권한 에러가 발생하면 `False`를 반환하여 상위에서 거짓 성공을 뱉지 않게 방어하라 (F-065).
5. 동시성 방어: 예약 최종 확정 직전에 저장소를 다시 읽어(recheck) 그 사이 정원이 차지 않았는지 검증하라 (F-064).

=== 공통 규칙 및 사후 작업 (절대 준수) ===
1. [Action Enum] book_appointment, modify_appointment, cancel_appointment, check_appointment, clarify, escalate, reject 외의 값은 절대 반환하지 마라.
2. [저장소 진실원천] ticket.context는 힌트일 뿐이며, 모든 최종 판단은 data/bookings.json을 기준으로 하라.
3. [테스트 작성 필수] 구현한 모듈에 대한 pytest 기반 단위 테스트(tests/test_storage.py)를 반드시 작성하고 통과시켜라.
4. 작업 완료 후 반드시 아래 명령어를 통해 형상 관리를 수행하라:
   - features.json에서 방금 구현한 F-ID들의 "passes"를 true로 변경.
   - progress.md에 Current status 및 Next step 갱신.
   - git add -A && git commit -m "feat: Phase 1 - 영속 저장소 및 환자 식별 진실원천 구축"
```

---

## Phase 2: Safety-First 게이트웨이 완결 (Safety)

```
당신의 역할은 코비메디 PoC의 보안 및 안전 제어 엔지니어이다.

[필수 참조 문서]
- docs/architecture.md (Section 4. Safety-First 설계)
- docs/policy_digest.md (Section 3 & 4. Safety Gate 정책, Section 10. 에스컬레이션)
- .ai/handoff/10_plan.md (전체 파이프라인 순서)
- features.json (F-001~F-010)

[작업 목표]
모든 분류 작업 전단에 위치하는 `src/classifier.py` 내 `safety_check()` 로직을 구현하라.

[구현 상세 및 예외 방어 (Critical)]
1. 파이프라인 최선행: agent.py의 process_ticket에서 가장 먼저 실행되어야 하며, 여기서 걸리면 LLM 호출을 생략하라 (F-001).
2. 즉시 Reject: 의료 상담(진단/약물), 잡담, 타 서비스 문의, 프롬프트 인젝션, 타 환자 정보 요구는 즉시 reject하라 (F-002~F-005). 반환 형식은 `{"status": "reject", "reason": "..."}` 형태의 딕셔너리를 권장한다.
3. 즉시 Escalate: 급성 통증/응급 상황 (F-008), 화난 고객/상담원 요청 (F-009), 의사 개인정보/보험비용 문의 (F-010)는 즉시 escalate하라.
4. 혼합 요청 분리 로직: "이 약 먹어도 되나요? 그리고 내과 예약할게요" 같은 문장 처리 시, 정규식이나 룰베이스를 통해 예약 관련 하위 문자열만 추출하여 후단으로 넘겨라. 분리가 모호하면 전체를 reject하라 (F-006).
5. 증상 안내 한계: 증상을 말하면 분과를 '안내'할 뿐, "XX염이 의심됩니다" 같은 진단 텍스트가 응답에 섞이지 않도록 하라 (F-007).

=== 공통 규칙 및 사후 작업 (절대 준수) ===
1. [Action Enum] book_appointment, modify_appointment, cancel_appointment, check_appointment, clarify, escalate, reject 외의 값은 절대 반환하지 마라.
2. [저장소 진실원천] ticket.context는 힌트일 뿐이며, 모든 최종 판단은 data/bookings.json을 기준으로 하라.
3. [테스트 작성 필수] 구현한 모듈에 대한 pytest 기반 단위 테스트(tests/test_safety.py)를 반드시 작성하고 통과시켜라.
4. 작업 완료 후 반드시 아래 명령어를 통해 형상 관리를 수행하라:
   - features.json에서 방금 구현한 F-ID들의 "passes"를 true로 변경.
   - progress.md에 Current status 및 Next step 갱신.
   - git add -A && git commit -m "feat: Phase 2 - Safety-First 게이트웨이 및 예외 방어 구현"
```

---

## Phase 3: 의도 분류 및 정보 추출 (Classification & Extraction)

```
당신의 역할은 코비메디 PoC의 LLM 연동 및 자연어 처리 엔지니어이다.

[필수 참조 문서]
- docs/architecture.md (Section 6. LLM 계층)
- docs/policy_digest.md (Section 5 & 6. 추출 및 대리예약)
- .ai/handoff/10_plan.md (Extraction 확장 계획)
- features.json (F-011~F-014, F-021~F-030, F-083~F-084)

[작업 목표]
`src/classifier.py`와 `src/llm_client.py`를 통해 안전한 추출 로직을 구현하라.

[구현 상세 및 예외 방어 (Critical)]
1. LLM 출력 통제: Ollama 호출 시 반드시 `format='json'`을 사용하라 (F-083). 프롬프트에 "Return ONLY valid JSON. Do not use markdown code blocks like ```json" 이라고 강제하라.
2. JSON 파싱 폴백: LLM 응답 파싱 시 `try-except json.JSONDecodeError` 블록을 반드시 씌우고, 파싱 실패나 Timeout 시 시스템 크래시 대신 `clarify` action과 빈 추출 딕셔너리를 반환하도록 폴백을 짜라 (F-084).
3. 엔티티 추출: 날짜/시간(F-021), 분과(F-022, F-029), 성명(F-023), 전화번호(정규식 권장, 010-XXXX-XXXX)(F-024), 생년월일(F-025)을 추출하라.
4. 대리 예약 선제 감지: 문장에 "엄마", "대신", "가족" 이 포함되어 있으면 추출 단계에서 즉시 `proxy_booking=true` 플래그를 올려라 (F-026).
5. 로직 분리: `classified_intent`는 사용자 의도(예: book_appointment)를 담고, `action`은 시스템이 취할 행동(예: 정보 부족 시 clarify)을 담도록 분리하라 (F-014).

=== 공통 규칙 및 사후 작업 (절대 준수) ===
1. [Action Enum] book_appointment, modify_appointment, cancel_appointment, check_appointment, clarify, escalate, reject 외의 값은 절대 반환하지 마라.
2. [저장소 진실원천] ticket.context는 힌트일 뿐이며, 모든 최종 판단은 data/bookings.json을 기준으로 하라.
3. [테스트 작성 필수] 구현한 모듈에 대한 pytest 기반 단위 테스트(tests/test_classifier.py)를 반드시 작성하고 통과시켜라.
4. 작업 완료 후 반드시 아래 명령어를 통해 형상 관리를 수행하라:
   - features.json에서 방금 구현한 F-ID들의 "passes"를 true로 변경.
   - progress.md에 Current status 및 Next step 갱신.
   - git add -A && git commit -m "feat: Phase 3 - 의도 분류 및 정보 추출 로직 구현"
```

---

## Phase 4: 멀티턴 대화 상태 및 본인/대리인 검증 (Dialogue & Proxy)

```
당신의 역할은 코비메디 PoC의 대화 상태(Dialogue State) 관리자이다.

[필수 참조 문서]
- docs/architecture.md (Section 7. Dialogue/Session)
- docs/policy_digest.md (Section 5. Clarify / 대리인)
- .ai/handoff/10_plan.md (Section 4. 상태머신)
- features.json (F-031~F-033, F-041~F-048)

[작업 목표]
`src/agent.py`에 멀티턴 상태머신과 본인/대리인 필수 검증 로직을 구현하라.

[구현 상세 및 예외 방어 (Critical)]
1. 본인/대리인 최우선 분기: 예약 의도 확정 시 세션의 `is_proxy_booking`이 None이면, 다른 슬롯(날짜/시간) 수집보다 우선하여 "본인이신가요, 대리이신가요?"를 묻는 clarify를 던져라 (F-031).
2. 정보 수집 타겟 스위칭: 본인이면 본인 성명+연락처를 (F-032), 대리인이면 **환자 본인**의 성명+연락처를 묻도록 응답 생성 로직을 분기하라 (F-033).
3. 큐 및 턴 관리: `pending_missing_info_queue`를 리스트 형태로 관리하되, 이미 채워진 슬롯은 다시 묻지 마라 (F-043). 턴 카운터(`clarify_turn_count`)가 4를 초과하면 무한 루프 탈출을 위해 `escalate`로 강제 전환하라 (F-042).
4. 2단계 확정 (Two-step commit): 정책 검사를 통과했다고 바로 `create_booking`을 호출하지 마라. `pending_confirmation=True` 상태로 두고 "예약할까요?" 물은 뒤, "네" 응답 시 영속화하라 (F-046).

=== 공통 규칙 및 사후 작업 (절대 준수) ===
1. [Action Enum] book_appointment, modify_appointment, cancel_appointment, check_appointment, clarify, escalate, reject 외의 값은 절대 반환하지 마라.
2. [저장소 진실원천] ticket.context는 힌트일 뿐이며, 모든 최종 판단은 data/bookings.json을 기준으로 하라.
3. [테스트 작성 필수] 구현한 모듈에 대한 pytest 기반 단위 테스트(tests/test_dialogue.py)를 반드시 작성하고 통과시켜라.
4. 작업 완료 후 반드시 아래 명령어를 통해 형상 관리를 수행하라:
   - features.json에서 방금 구현한 F-ID들의 "passes"를 true로 변경.
   - progress.md에 Current status 및 Next step 갱신.
   - git add -A && git commit -m "feat: Phase 4 - 멀티턴 대화 상태 및 본인 대리인 검증 구현"
```

---

## Phase 5: 결정론적 예약 정책 엔진 (Policy)

```
당신의 역할은 코비메디 PoC의 비즈니스 정책 엔진 설계자이다.

[필수 참조 문서]
- docs/architecture.md (Section 5. Deterministic Policy)
- docs/policy_digest.md (Section 8. 신규, Section 9. 변경/취소)
- .ai/handoff/10_plan.md (Policy 확장 계획)
- features.json (F-040, F-051~F-057)

[작업 목표]
`src/policy.py` 구현. **LLM 호출 절대 금지.** 순수 Python 로직으로 작성하라.

[구현 상세 및 예외 방어 (Critical)]
1. 시간 Mocking 호환: 모든 정책 함수는 `now: datetime` 파라미터를 명시적으로 받아야 한다. 내부에서 `datetime.now()`를 하드코딩하지 마라 (F-040).
2. 24시간 정확도: `is_change_allowed`는 `(appointment_time - now).total_seconds() >= 86400` 로직을 통해 정확히 24시간을 판별하라. 당일 신규 예약은 이 규칙을 타지 않게 예외 처리하라 (F-055, F-057).
3. 정원 및 슬롯 겹침: `get_appointment_duration`으로 초진 40분/재진 30분을 받아, 기존 예약의 `start ~ end` 범위와 요청 시간이 1분이라도 겹치면 차단하라 (F-054). 같은 시간대 예약 수가 3명이면 4번째 요청은 차단하라 (F-053).
4. 대체안 로직: 슬롯 불가 시 에러만 뱉지 말고 `suggest_alternative_slots`를 통해 해당일의 다른 가용 시간대 1~3개를 리스트로 반환하여 clarify 시 제안되도록 하라 (F-056).

=== 공통 규칙 및 사후 작업 (절대 준수) ===
1. [Action Enum] book_appointment, modify_appointment, cancel_appointment, check_appointment, clarify, escalate, reject 외의 값은 절대 반환하지 마라.
2. [저장소 진실원천] ticket.context는 힌트일 뿐이며, 모든 최종 판단은 data/bookings.json을 기준으로 하라.
3. [테스트 작성 필수] 구현한 모듈에 대한 pytest 기반 단위 테스트(tests/test_policy.py)를 반드시 작성하고 통과시켜라.
4. 작업 완료 후 반드시 아래 명령어를 통해 형상 관리를 수행하라:
   - features.json에서 방금 구현한 F-ID들의 "passes"를 true로 변경.
   - progress.md에 Current status 및 Next step 갱신.
   - git add -A && git commit -m "feat: Phase 5 - 결정론적 예약 정책 엔진 구현"
```

---

## Phase 6: 배치 런타임, 출력 계약 및 지표 (Output & Metrics)

```
당신의 역할은 시스템 오케스트레이터이자 데이터 분석가이다.

[필수 참조 문서]
- docs/architecture.md (Section 12. 데이터 계약)
- docs/policy_digest.md (Section 1. PoC 지표, Section 12. 출력 계약)
- .ai/handoff/10_plan.md (Section 8. 배치 모드 처리 원칙)
- features.json (F-081~F-082, F-091~F-094)

[작업 목표]
`run.py`의 배치 결과 출력 형식 강제 및 런타임 이벤트 계측 로직 삽입.

[구현 상세 및 예외 방어 (Critical)]
1. 배치 스키마 검증: `results.json`에 기록되는 각 객체는 `ticket_id`, `classified_intent`, `department`, `action`, `response`, `confidence`(float 0.0~1.0), `reasoning`(string) 키를 무조건 포함해야 한다 (F-081).
2. 동적 로깅: `reasoning` 필드는 "분류됨" 같은 하드코딩이 아니라, "Safety 통과, 저장소 이력 확인(초진), 24시간 정책 위반으로 취소 차단" 등 파이프라인 판단 이력을 동적으로 연결해 작성하라 (F-082).
3. KPI 집계: `agent.py` 내에 단순 print가 아닌, `agent_success`, `safe_reject`, `agent_soft_fail_clarify` 등을 카운팅하는 로깅/메트릭 객체를 연동하여 PoC 지표를 계측할 수 있는 훅(hook)을 마련하라 (F-091~F-094).

=== 공통 규칙 및 사후 작업 (절대 준수) ===
1. [Action Enum] book_appointment, modify_appointment, cancel_appointment, check_appointment, clarify, escalate, reject 외의 값은 절대 반환하지 마라.
2. [저장소 진실원천] ticket.context는 힌트일 뿐이며, 모든 최종 판단은 data/bookings.json을 기준으로 하라.
3. [테스트 작성 필수] 구현한 모듈에 대한 pytest 기반 단위 테스트(tests/test_batch.py)를 반드시 작성하고 통과시켜라.
4. 작업 완료 후 반드시 아래 명령어를 통해 형상 관리를 수행하라:
   - features.json에서 방금 구현한 F-ID들의 "passes"를 true로 변경.
   - progress.md에 Current status 및 Next step 갱신.
   - git add -A && git commit -m "feat: Phase 6 - 배치 런타임 및 출력 데이터 계약 구현"
```

---

## Phase 7: 외부 연동 (Cal.com Q4)

```
당신의 역할은 외부 API 연동(Integration) 전문가이자 백엔드 엔지니어이다.

[필수 참조 문서]
- .ai/handoff/01_q4_request.md ~ 05_q4_architecture.md (Q4 기획/정책/계획/구현/아키텍처 5종 전문)
- docs/architecture.md (Section 11. Q4)
- docs/policy_digest.md (Section 11. cal.com)
- .ai/handoff/10_plan.md (전체 파이프라인 순서)
- features.json (F-071~F-074)

[작업 목표]
`src/calcom_client.py`를 단일 진입점으로 구현하고 `src/agent.py`의 예약 파이프라인에 결합하라. 기존 로컬 정책의 안정성을 훼손하지 않으면서, 03_q4_plan.md에 정의된 5대 유저 동선을 코드 레벨에서 완벽하게 방어해야 한다.

[1. 환경 설정 및 우아한 성능 저하 (Graceful Degradation)]
- `.env`에서 `CALCOM_API_KEY` 및 분과별 Event ID(`CALCOM_ENT_ID`, `CALCOM_INTERNAL_ID`, `CALCOM_ORTHO_ID`)를 정확히 로드하라.
- `is_calcom_enabled()` 함수를 구현하여, 키가 없거나 해당 분과의 Event Type 매핑이 없을 경우 시스템 에러를 발생시키지 말고 `False`를 반환하라.
- 상위 호출자(`agent.py`)는 이 값이 `False`일 경우 외부 연동 구간만 조용히 건너뛰고 기존 로컬 로직으로만 예약을 정상 확정해야 한다.

[2. API v2 명세 및 데이터 매핑 (Strict Compliance)]
- Base URL: `https://api.cal.com/v2`
- 공통 인증 헤더: `Authorization: Bearer <CALCOM_API_KEY>`
- 버전 제어 (매우 중요):
  - `GET /slots` 호출 시에는 반드시 `cal-api-version: 2024-09-04` 헤더를 사용하라.
  - `POST /bookings` 호출 시에는 반드시 `cal-api-version: 2024-08-13` 헤더를 사용하라.
- 이메일 매핑 로직: 예약 시스템 특성상 환자 이메일이 없으므로, 환자의 전화번호를 파싱하여 더미 이메일(예: `01012345678@kobimedi.local`)을 생성해 `attendee.email`로 전송하라. 실제 전화번호는 `notes`나 `metadata`에 삽입하라.
- 타임존 로직: 예약 생성 시 `start` 시간은 반드시 KST 로컬 시간을 UTC 기준 ISO 8601 포맷으로 변환하여 전송해야 한다.

[3. 파이프라인 결합 위치 및 유저 동선 커버 (Proactive UX)]
- 위치 1 (선제적 안내 - 동선 1.3): 사용자가 분과와 날짜는 입력했으나 시간(time)이 누락되어 `clarify` 질문을 생성해야 할 때, 먼저 `get_available_slots`을 호출하여 "예약 가능한 시간은 [가용 시간]입니다. 언제가 좋으신가요?" 형태로 선제적으로 제안하라.
- 위치 2 (확정 전 조회 - 동선 1 & 2): 사용자에게 "예약할까요?"(Confirmation)를 묻기 직전에 `get_available_slots`을 호출하라. 외부에서 자리가 마감되었다면 `clarify`로 대안을 제시하며 롤백하라.
- 위치 3 (예약 생성 - Race Condition 방어): 사용자가 최종 확정("네")하여 로컬 저장소에 `create_booking`을 호출하기 직전(1 millisecond 전)에 cal.com `create_booking`을 호출하라. 충돌(Conflict/409) 발생 시 로컬 저장을 중단하고 "방금 전 마감되었습니다" 안내와 함께 대안을 묻는 `clarify` 상태로 롤백하라.

[4. 배치 모드(Batch Mode) 특화 처리 원칙 (즉각 Drop)]
- 배치 모드(`!is_chat`)에서는 사용자와의 대화 턴(멀티턴)이 불가능하다.
- 따라서 가용 슬롯을 조회했을 때 자리가 없다면, 상태를 보류하지 말고 **즉시 예약을 중단(Drop)하고 `action: "clarify"`를 반환**하라.
- 단, 단순히 실패로 처리하지 말고 `response`에 "요청하신 시간이 마감되었습니다. 대안 시간은 [API로 조회한 가용 리스트]입니다."를 명시하여 CS 직원이 결과를 보고 즉각 대응할 수 있도록 Soft Fail 처리하라.
- 자리가 있다면 사용자 동의 절차를 생략하고 즉시 `create_booking`을 호출해 예약을 확정하라.

[5. 네트워크 장애 방어 및 거짓 성공 원천 차단 (Hard Fail Defense)]
- API 호출 시 반드시 타임아웃을 명시하고, `try-except requests.RequestException`으로 모든 네트워크/HTTP 예외를 감싸라.
- 에러 발생 시 시스템이 죽거나(`Crash`), `None` 오류를 뱉으며 사용자에게 "예약이 완료되었습니다"라고 거짓 응답(False Success)을 하는 일은 절대 없어야 한다.
- 에러를 캐치하면 로컬 DB 기록을 즉시 중단하고, `AGENT_HARD_FAIL` 이벤트를 로깅한 뒤, "현재 외부 예약 시스템 응답 지연으로 처리가 불가합니다. 잠시 후 다시 시도해주세요."라는 문구와 함께 `clarify` 액션으로 안전하게 폴백하라.

=== 공통 규칙 및 사후 작업 (절대 준수) ===
1. [Action Enum] book_appointment, modify_appointment, cancel_appointment, check_appointment, clarify, escalate, reject 외의 값은 절대 반환하지 마라.
2. [저장소 진실원천] ticket.context는 힌트일 뿐이며, 모든 최종 판단은 data/bookings.json을 기준으로 하라.
3. [테스트 작성 필수 - Mocking 강제] 구현한 모듈에 대한 pytest 기반 단위 테스트(tests/test_calcom.py)를 반드시 작성하라. 이때 `responses` 라이브러리나 `unittest.mock.patch`를 사용하여 실제 네트워크 통신이 발생하지 않도록 API 응답(성공, 타임아웃, 409 Conflict 등)을 완벽히 Mocking(모킹)하여 테스트의 독립성을 보장하라.
4. 작업 완료 후 반드시 아래 명령어를 통해 형상 관리를 수행하라:
   - features.json에서 방금 구현한 F-ID들의 "passes"를 true로 변경.
   - progress.md에 Current status 및 Next step 갱신.
   - git add -A && git commit -m "feat: Phase 7 - Q4 외부 API 연동, 선제적 슬롯 안내 및 Mocking 테스트 도입"
```
