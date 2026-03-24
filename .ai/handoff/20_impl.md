# 구현 갭 분석 및 Phase별 작업 계획

기준 문서: `.ai/handoff/00_request.md`, `.ai/handoff/10_plan.md`, `docs/architecture.md`, `docs/policy_digest.md`, `AGENTS.md`, `.ai/harness/features.json`, `.ai/harness/progress.md`  
분석 대상 코드: `src/*.py`, `chat.py`, `run.py`, `tests/*.py`, `data/bookings.json`

---

## 1. 목적

이 문서는 최신 정책/아키텍처/기능 문서를 기준으로, **현재 코드와 목표 상태 사이의 실제 구현 갭**을 정리한 작업 문서다.  
특히 이번 정책 추가로 중요해진 아래 3가지를 구현 관점에서 구체화한다.

1. **예약 시작 시 본인/대리인 여부 확인**
2. **전화번호 우선 환자 식별 및 초진/재진 판정**
3. **`data/bookings.json`을 진실원천으로 사용하는 저장소 기반 흐름 강화**

각 작업 항목마다 아래를 명시한다.

1. 어떤 파일을 수정해야 하는가
2. 어떤 함수/상태를 추가·변경해야 하는가
3. 어떤 테스트가 필요한가
4. 어떤 기존 동작에 회귀 위험이 있는가

우선순위는 항상 다음을 따른다.

**safety > correctness > policy compliance > demo polish > Q4**

---

## 2. 현재 코드 기준 요약

### 2.1 이미 존재하는 기반
- `src/storage.py`
  - `load_bookings()`, `save_bookings()`, `create_booking()` 구현됨
  - `find_bookings()` 구현됨
  - `resolve_customer_type_from_history()` 구현됨
- `src/agent.py`
  - storage helper를 import하여 일정 수준의 저장소 조회를 사용 중
  - `pending_confirmation` 기반의 확인 질문 흐름 존재
  - `birth_date` 기반 ambiguity 해소 흐름 일부 존재
- `src/policy.py`
  - 24시간 규칙, 1시간 3명 제한, 초진/재진 슬롯 길이 계산, 대체 슬롯 제안 기본 구조 존재

### 2.2 이번 문서 개정 기준으로 확인된 핵심 갭

#### A. 본인/대리인 확인이 아직 코드에 없다
- `src/agent.py` 검색 기준:
  - `is_proxy_booking` 없음
  - `patient_contact` 없음
  - 예약 의도 확정 직후 self/proxy를 묻는 상태머신 없음
- 즉, **정책 문서에는 반영됐지만 runtime에는 아직 없다**.

#### B. 전화번호 우선 식별이 아직 구현되지 않았다
- `src/storage.py:find_bookings()`는 현재 `customer_name`, `birth_date`, `department`, `booking_time`, `date`, `time` 필터만 지원
- `patient_contact` 필터 없음
- `resolve_customer_type_from_history()`도 현재는 `customer_name + birth_date` 기반
- 즉, **정책상 1순위 식별자인 전화번호 기반 초진/재진 판정이 아직 불가**하다.

#### C. dialogue state가 최신 정책 수준으로 확장되지 않았다
- 현재 `session_state`에는 `customer_name`, `birth_date`, `resolved_customer_type`, `pending_confirmation` 정도는 있으나
- 아래 상태는 아직 없다.
  - `is_proxy_booking`
  - `patient_name`
  - `patient_contact`
  - `pending_missing_info_queue`
  - `clarify_turn_count`
- 즉, **정책 digest의 4단계 clarify 상태머신과 직접 맞지 않는다**.

#### D. 저장소는 존재하지만 최신 계약과 아직 불일치한다
- `data/bookings.json`을 쓰는 구조는 있으나,
- 레코드에 `patient_contact`, `is_proxy_booking`를 필수 운영 필드로 다룬다는 최신 정책은 아직 미반영
- update/cancel/fresh recheck 관련 공통 계층은 아직 불충분하다.

#### E. impl 문서 자체가 이전 기준을 반영하고 있었다
- 이전 버전은 `src/storage.py`가 비어 있다고 가정했는데, 현재는 이미 일부 구현되어 있다.
- 반면 최신 기능 요구(F-031~F-039, F-062~F-066)는 반영되지 않았다.

---

## 3. 최신 기준에서 우선 구현해야 할 기능군

이번 impl 문서는 최신 `features.json` 기준으로 다음 기능군을 중심에 둔다.

### 3.1 최우선 신규/미완료 기능
| ID | 설명 |
| --- | --- |
| F-031 | 예약 시작 시 본인/대리인 여부 확인 |
| F-032 | 본인 예약 시 본인 성명+전화번호 수집 |
| F-033 | 대리 예약 시 환자 본인 성명+전화번호 수집 |
| F-034 | 전화번호 우선 환자 식별 |
| F-035 | 초진/재진 저장소 판정 |
| F-038 | 환자 식별 정보 bookings.json 저장 |
| F-039 | 환자 식별 정보를 세션 중간 상태로 관리 |
| F-062 | `find_bookings()` patient_contact 필터 지원 |
| F-063 | `resolve_customer_type_from_history()` 전화번호 우선 확장 |
| F-064 | 확인 직전 fresh storage recheck |
| F-066 | bookings.json 레코드에 patient_contact / is_proxy_booking 포함 |

### 3.2 함께 정리해야 할 관련 기능
| ID | 설명 |
| --- | --- |
| F-024 | 전화번호 추출 |
| F-026 | 대리 예약 감지 |
| F-041 | pending_missing_info_queue |
| F-042 | clarify_turn_count |
| F-043 | 누적 슬롯 유지 + is_proxy_booking 유지 |
| F-044 | 매 턴 재평가 |
| F-046 | 최종 확인 후 영속화 |
| F-052 | 운영시간 정책 |
| F-065 | 저장 실패 시 거짓 성공 금지 |

---

## 4. Phase별 구현 계획

`10_plan.md`의 Phase 순서를 그대로 따른다.

| Phase | 핵심 주제 | 주요 기능 |
| --- | --- | --- |
| Phase 0 | 환자 식별 기반 확보 | F-024, F-026, F-031~F-039, F-062, F-063, F-066 |
| Phase 1 | Dialogue State Machine 확장 | F-041~F-047 |
| Phase 2 | Policy 확장 | F-052~F-057, F-064 |
| Phase 3 | Safety / Extraction 강화 | F-006~F-010, F-024, F-026 |
| Phase 4 | Persistence 강화 | F-061, F-064~F-067 |
| Phase 5 | Metrics & documents | F-091~F-103 |

아래 작업은 이 순서대로 진행하는 것을 권장한다.

---

## 5. Phase 0 — 환자 식별 기반 확보 (최우선)

핵심 목표: **예약을 진행하는 사람과 실제 환자를 구분하고, 환자 전화번호를 1차 식별자로 사용할 수 있게 만드는 것**

### 5.1 F-031 예약 시작 시 본인/대리인 여부 확인

#### 현재 갭
- 예약 의도 확정 후 바로 날짜/시간/분과/이름을 묻거나 기존 흐름으로 넘어감
- self/proxy를 먼저 확인하는 단계가 없음

#### 수정 파일
- `src/agent.py`
- 필요 시 `src/response_builder.py`
- 필요 시 `src/models.py` (상태 타입 정리 시)
- `tests/test_dialogue.py`

#### 추가/변경 사항
- 세션 상태에 `is_proxy_booking: bool | None` 추가
- 예약 의도(`book_appointment`, `modify_appointment`, `cancel_appointment`, `check_appointment`)가 확정되면,
  `is_proxy_booking is None`인 경우 먼저 self/proxy clarify 질문 수행
- 질문 예시:
  - “예약하시는 분이 환자 본인이신가요, 아니면 가족이나 지인을 대신하여 예약하시는 건가요?”

#### 테스트
- `tests/test_dialogue.py`
  - 예약 의도 직후 self/proxy 질문이 먼저 나오는지
  - “본인이에요” 응답 시 `is_proxy_booking=False`
  - “어머니 대신 예약할게요” 응답 시 `is_proxy_booking=True`

#### 회귀 위험
- 기존 clarify 흐름보다 질문 단계가 하나 늘어나므로 confirmation/clarify 순서 테스트가 깨질 수 있음

---

### 5.2 F-024 + F-026 전화번호 추출 / 대리 예약 감지

#### 현재 갭
- agent/state 수준에서 `patient_contact`를 다루지 않음
- 명시적 대리 표현을 감지해도 상태로 보존하는 구조가 없음

#### 수정 파일
- `src/classifier.py` 또는 `src/agent.py` 내부 추출 helper
- `src/prompts.py` (LLM 프롬프트 기반이면)
- `tests/test_classifier.py`
- `tests/test_dialogue.py`

#### 추가/변경 사항
- 전화번호 정규식 기반 추출 helper 추가
  - 예: `010-1234-5678`, `01012345678`, `010 1234 5678`
- 대리 예약 표현 감지 helper 추가
  - “엄마 대신”, “아버지 예약”, “가족 대신”, “보호자”, “대리 예약”
- 추출 결과를 `patient_contact`, `is_proxy_booking` 후보로 세션에 반영

#### 테스트
- 전화번호 패턴 정규화 테스트
- 대리 표현 감지 테스트
- 배치 메시지에서 대리 표현이 있으면 `is_proxy_booking=true` 후보로 해석되는지

---

### 5.3 F-032 / F-033 / F-039 환자 정보 수집 상태 도입

#### 현재 갭
- 현재 세션은 사실상 `customer_name`, `birth_date` 중심
- 최신 정책이 요구하는 `patient_name`, `patient_contact`, `is_proxy_booking` 상태가 없음

#### 수정 파일
- `src/agent.py`
- 필요 시 `src/models.py`
- `tests/test_dialogue.py`

#### 추가/변경 사항
- 세션 상태 필드 추가
  - `patient_name`
  - `patient_contact`
  - `is_proxy_booking`
- 분기 처리
  - 본인 예약: 본인 이름 + 전화번호 수집
  - 대리 예약: 환자 본인 이름 + 전화번호 수집
- `ticket.customer_name`은 힌트로만 사용하고, 최종 식별값은 session state에 저장

#### 테스트
- 본인 예약 flow: self 확인 → 이름/전화번호 수집 → 예약 정보 수집
- 대리 예약 flow: proxy 확인 → 환자 본인 이름/전화번호 수집 → 예약 정보 수집

---

### 5.4 F-062 `find_bookings()` patient_contact 필터 지원

#### 현재 갭
- `src/storage.py:find_bookings()`는 현재 `patient_contact` 필터가 없다.

#### 수정 파일
- `src/storage.py`
- `tests/test_storage.py`

#### 추가/변경 함수
```python
def find_bookings(
    customer_name: str | None = None,
    filters: dict[str, Any] | None = None,
    path: str | Path | None = None,
    include_cancelled: bool = False,
    patient_contact: str | None = None,
) -> list[dict]:
```

또는 `filters["patient_contact"]`를 공식 지원하도록 변경.

#### 테스트
- 동일 이름 2명이 있어도 전화번호가 다르면 정확히 한 명만 조회되는지
- include_cancelled와 patient_contact 필터가 함께 동작하는지

---

### 5.5 F-063 / F-035 전화번호 우선 초진/재진 판정

#### 현재 갭
- `resolve_customer_type_from_history()`는 `customer_name + birth_date` 기준
- 정책상 요구되는 우선순위와 불일치

#### 수정 파일
- `src/storage.py`
- `src/agent.py`
- `tests/test_storage.py`
- `tests/test_dialogue.py`

#### 추가/변경 함수
```python
def resolve_customer_type_from_history(
    customer_name: str | None = None,
    birth_date: str | None = None,
    path: str | Path | None = None,
    patient_contact: str | None = None,
) -> dict[str, Any]:
```

처리 우선순위:
1. `patient_contact`
2. `customer_name + birth_date`
3. `customer_name` 단독(유일할 때만)

#### 테스트
- 같은 이름이어도 전화번호 일치 시 재진 판정되는지
- 전화번호 이력 없고 cancelled만 있으면 초진 판정되는지
- 이름 중복 + 생년월일 없음이면 ambiguous 처리되는지

---

### 5.6 F-038 / F-066 bookings.json 운영 필드 확장

#### 현재 갭
- `create_booking()`은 record passthrough로 저장은 가능하지만,
- 정책상 필요한 `patient_name`, `patient_contact`, `is_proxy_booking`를 필수 운영 필드처럼 다루는 로직/테스트가 없다.

#### 수정 파일
- `src/storage.py`
- `src/agent.py`
- `tests/test_storage.py`

#### 추가/변경 사항
- `create_booking()` 호출 시 아래 필드 포함 강제
  - `patient_name`
  - `patient_contact`
  - `is_proxy_booking`
  - `birth_date`(있으면)
- 저장 전 필수 필드 검증 또는 agent 단계에서 선검증

#### 테스트
- book 확정 후 생성 레코드에 `patient_contact`, `is_proxy_booking`가 실제 저장되는지

---

## 6. Phase 1 — Dialogue State Machine 확장

핵심 목표: 최신 정책의 clarify 상태머신을 코드 구조로 반영

### 6.1 F-041 pending_missing_info_queue 도입

#### 현재 갭
- 현재는 missing info를 즉시 계산하는 흐름은 있으나 queue 기반 상태관리로 보기는 어렵다.

#### 수정 파일
- `src/agent.py`
- `tests/test_dialogue.py`

#### 추가/변경 사항
- 상태에 `pending_missing_info_queue: list[str]` 추가
- 우선순위 예시
  1. `is_proxy_booking`
  2. `patient_name`
  3. `patient_contact`
  4. `department`
  5. `date`
  6. `time`
  7. `birth_date`
  8. `confirmation`

---

### 6.2 F-042 clarify_turn_count 도입

#### 현재 갭
- 정책상 4단계 상한이 문서화됐지만 runtime counter가 없다.

#### 수정 파일
- `src/agent.py`
- `tests/test_dialogue.py`

#### 테스트
- 4단계 이상 미해결 시 `escalate` 검토 또는 안전 종료로 전환되는지

---

### 6.3 F-043 / F-044 누적 상태 유지 및 재평가

#### 현재 갭
- 일부 누적은 되지만, `is_proxy_booking`, `patient_contact`까지 포함한 최신 상태계약은 없다.

#### 수정 파일
- `src/agent.py`
- `tests/test_dialogue.py`

#### 테스트
- self/proxy 답변 후 다시 묻지 않는지
- 전화번호 입력 후 다음 턴에 보존되는지
- 이미 입력한 분과/날짜/시간이 유지되는지

---

## 7. Phase 2 — Policy 확장

핵심 목표: 현재 policy.py의 기본 기능을 최신 정책과 완전히 맞춤

### 7.1 F-052 운영시간/점심시간/휴진일

#### 현재 갭
- `src/policy.py`에는 운영시간/토요일/일요일/점심시간 정책이 아직 직접 반영되지 않았다.

#### 수정 파일
- `src/policy.py`
- `tests/test_policy.py`

#### 테스트
- 평일 18:00 시작 불가
- 토요일 13:00 시작 불가
- 12:30~13:30 겹침 불가
- 일요일 불가

---

### 7.2 F-057 일반 당일 신규 예약 허용 / 응급은 escalate

#### 현재 갭
- 현재 `evaluate_same_day_booking()`는 일반 당일 신규 예약을 `clarify` 쪽으로 보수 처리하고 있다.
- 최신 정책은 **일반 당일 신규 예약 허용**이다.

#### 수정 파일
- `src/policy.py`
- `tests/test_policy.py`

#### 변경 방향
- same-day 자체를 막지 않음
- 급성/응급이면 `escalate`

---

### 7.3 F-064 최종 확인 직전 fresh storage recheck

#### 현재 갭
- confirmation 직전 최신 저장소 상태 재조회가 강제되지 않는다.

#### 수정 파일
- `src/agent.py`
- `src/storage.py`
- `tests/test_dialogue.py`
- `tests/test_storage.py`

#### 테스트
- confirmation 전에 동일 슬롯이 다른 예약으로 차버린 상황을 재검증하는지

---

## 8. Phase 3 — Safety / Extraction 강화

핵심 목표: 최신 safety/patient 정책과 extraction을 자연스럽게 연결

### 8.1 F-006 의료+예약 혼합 요청 분리 처리

#### 현재 갭
- 문서상 허용 범위가 정리되었지만, code에서 예약 하위 요청을 안전하게 분리하는 구조는 재검토가 필요하다.

#### 수정 파일
- `src/classifier.py`
- `src/agent.py`
- `tests/test_safety.py`

---

### 8.2 F-009 / F-010 운영성 escalate 강화

#### 현재 갭
- 보험/비용, 의사 개인정보 요청 등은 최신 policy 기준으로 test/코드 정합 재검토 필요

#### 수정 파일
- `src/classifier.py`
- `tests/test_safety.py`

---

## 9. Phase 4 — Persistence 강화

핵심 목표: 저장소를 진짜 진실원천으로 만들고 거짓 성공을 차단

### 9.1 F-061 / F-067 저장소 우선 / context는 힌트

#### 현재 갭
- agent는 storage를 사용하고 있으나, 여전히 `ticket.customer_name`, `ticket.customer_type`, `context` 중심 해석이 남아 있다.

#### 수정 파일
- `src/agent.py`
- `src/storage.py`
- `tests/test_storage.py`

#### 테스트
- `context.has_existing_appointment=True`여도 storage에 없으면 예약이 없다고 판정하는지

---

### 9.2 F-065 저장 실패 시 거짓 성공 금지

#### 현재 갭
- 저장 실패를 표준화한 예외/복구 정책이 아직 약하다.

#### 수정 파일
- `src/storage.py`
- `src/agent.py`
- `tests/test_storage.py`
- `tests/test_batch.py`

#### 테스트
- write failure 시 success 응답 금지
- reasoning에 저장 실패 근거 반영

---

## 10. Phase 5 — Metrics / 문서 마감

핵심 목표: 최신 문서 기준과 런타임 계측을 연결

### 10.1 F-091 / F-092 이벤트 계측

#### 현재 갭
- `agent_success`, `clarify_resolved`, `agent_hard_fail` 등의 계측이 runtime에 없다.

#### 수정 파일
- `src/agent.py`
- 필요 시 `src/utils.py`
- `tests/test_batch.py`

---

### 10.2 문서 정합

이미 반영된 문서:
- `docs/policy_digest.md`
- `.ai/harness/features.json`
- `.ai/handoff/10_plan.md`
- `docs/architecture.md`
- `.ai/harness/progress.md`

향후 정리 대상:
- `docs/final_report.md`
- `docs/q1_metric_rubric.md`
- `docs/q3_safety.md`

---

## 11. 파일별 우선 수정 목록

### 최우선
1. `src/agent.py`
   - self/proxy state
   - patient_name/patient_contact state
   - latest clarify queue / recheck / confirmation flow
2. `src/storage.py`
   - patient_contact filter
   - phone-first history resolution
   - 운영 필드 저장
3. `tests/test_dialogue.py`
   - 본인/대리인 확인 flow
   - 전화번호 수집 flow
4. `tests/test_storage.py`
   - patient_contact 기반 조회/판정

### 그 다음
5. `src/policy.py`
   - 운영시간
   - 일반 당일 예약 허용
   - fresh recheck 연계
6. `tests/test_policy.py`

### 이후
7. `src/classifier.py`
   - 대리 표현 감지 / 전화번호 추출 / mixed request 정교화
8. `tests/test_safety.py`, `tests/test_classifier.py`

---

## 12. 권장 구현 순서

### Step 1. storage.py 전화번호 우선 조회 확장
- `find_bookings(patient_contact=...)`
- `resolve_customer_type_from_history(patient_contact=...)`

### Step 2. agent.py 상태 확장
- `is_proxy_booking`
- `patient_name`
- `patient_contact`
- self/proxy clarify 질문

### Step 3. booking confirm 전후 흐름 정리
- 환자 정보 확보 → 저장소 기반 customer_type 확정 → 정책 검증 → confirmation → fresh recheck → persist

### Step 4. policy.py 최신화
- 일반 당일 예약 허용
- 운영시간/점심시간/휴진일

### Step 5. tests 보강
- dialogue / storage / policy 우선

---

## 13. 회귀 위험 총정리

| 영향 받는 기존 동작 | 회귀 포인트 |
| --- | --- |
| 기존 clarify 흐름 | self/proxy 질문이 앞에 추가되며 대화 순서가 바뀜 |
| birth_date 기반 ambiguous 해소 | 전화번호 우선 로직이 들어오며 분기 순서가 달라짐 |
| same-day 처리 | 일반 당일 예약 허용으로 action 결과가 달라질 수 있음 |
| confirmation | fresh recheck/persist 실패 처리로 success 타이밍이 늦어짐 |
| batch output | customer_type / reasoning 근거가 저장소 우선으로 바뀜 |

핵심 원칙은 두 가지다.

1. **환자 identity가 확정되기 전에는 예약 확정으로 넘어가지 않는다.**
2. **성공 응답은 저장소 재검증과 영속화 성공 이후에만 반환한다.**

---

## 14. 착수 우선순위 결론

다음 구현 순서를 고정한다.

1. **Phase 0 환자 식별 기반 확보**
   - F-031~F-039, F-062, F-063, F-066
2. **Phase 1 Dialogue State Machine 확장**
   - F-041~F-047
3. **Phase 2 Policy 확장**
   - F-052~F-057, F-064
4. **Phase 4 Persistence 강화**
   - F-061, F-065, F-067
5. **Phase 3 Safety/Extraction 보강**
   - F-006, F-009, F-010 등
6. **Phase 5 Metrics / 문서 마감**

특히 첫 구현 단위는 다음으로 고정한다.

> **`src/storage.py`의 patient_contact 기반 조회/판정 확장 + `src/agent.py`의 self/proxy 상태 도입**

이 단계가 끝나야 최신 policy / plan / architecture 문서와 실제 코드가 처음으로 정렬된다.