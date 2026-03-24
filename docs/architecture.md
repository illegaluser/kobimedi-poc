# Q2 Agent Architecture

기준 문서: `.ai/handoff/00_request.md`, `.ai/harness/features.json`, `docs/policy_digest.md`, `AGENTS.md`  
설계 우선순위: **safety > correctness > policy compliance > demo polish > Q4**

이 문서는 코비메디 예약 Agent PoC의 **목표 아키텍처**를 정의한다.  
핵심은 단순 챗봇이 아니라, **공통 Agent Core + 결정론 정책 엔진 + 파일 기반 예약 저장소 + 선택적 cal.com 연동** 구조다.

이 문서의 목적은 세 가지다.

1. `chat.py`와 `run.py`가 반드시 **같은 `src/agent.py` 판단 로직**을 사용하게 하는 것
2. request.md와 features.json이 요구한 **안전 규칙 / 정책 규칙 / 저장소 규칙**을 한 구조 안에서 정렬하는 것
3. 이후 코딩 시 `.ai/handoff/10_plan.md`가 바로 구현 가능한 수준의 구조적 기준을 제공하는 것

---

## 1. 아키텍처 목표와 비목표

### 1.1 목표
- 의료 상담/목적 외 사용/프롬프트 인젝션을 **예약 처리보다 먼저** 차단
- 과제 원문 action 7개를 **축약 없이 그대로** 유지
- 예약/변경/취소/조회 판단을 **정책 + 저장소 기반**으로 일관되게 처리
- chat/run이 같은 입력에 같은 판단을 내리도록 **공통 오케스트레이터** 유지
- LLM은 자연어 해석에만 제한적으로 사용하고, 숫자/시간/정원/변경 가능 여부는 코드가 판정
- Q4를 붙이더라도 구조를 깨지 않고 **policy 이후 cal.com**만 확장 가능하게 설계

### 1.2 비목표
- 질병 진단, 약 추천, 치료 판단
- LLM 자유 생성에 의존한 정책 판단
- chat.py와 run.py의 별도 비즈니스 로직 분기
- 확인되지 않은 슬롯/분과/의사/예약 정보 생성

---

## 2. 전체 시스템 구조

### 2.1 End-to-End 다이어그램

```text
[입력 계층]
  ├─ chat.py  : 단일 세션 CLI 대화
  └─ run.py   : tickets.json 배치 처리
        ↓
[공통 Agent Core: src/agent.py]
        ↓
[1) Safety Gate]
  ├─ 의료 상담 차단 -> reject
  ├─ 목적 외 사용/인젝션 차단 -> reject
  ├─ 급성 통증/응급 -> escalate
  ├─ 반복 민원/상담원 연결 -> escalate
  └─ 증상 기반 분과 안내 요청 -> safe + guidance mode
        ↓
[2) Classification / Extraction]
  ├─ action 7개 분류
  ├─ 분과/의사/날짜/시간 추출
  ├─ 초진/재진 추론
  └─ 기존 예약 식별 정보 추출
        ↓
[3) Dialogue State Merge]
  ├─ clarify 누적 슬롯 병합
  ├─ confirmation pending 확인
  └─ 다수 예약 후보 선택 처리
        ↓
[4) Storage Lookup]
  ├─ 고객 기존 예약 조회
  ├─ modify/cancel/check 대상 검증
  └─ 로컬 예약 기록 진실원천 확보
        ↓
[5) Deterministic Policy Engine: src/policy.py]
  ├─ 24시간 변경/취소 규칙
  ├─ 1시간당 최대 3명
  ├─ 초진 40분 / 재진 30분
  ├─ 당일 신규 예약 보수 처리
  └─ 대체 슬롯 계산
        ↓
[6) Persistence / Integration]
  ├─ Q2: 파일 저장소 create/modify/cancel/check 반영
  └─ Q4: cal.com slot 조회 및 booking 생성
        ↓
[7) Response Builder]
  ├─ 사용자 응답 생성
  ├─ batch JSON 스키마 구성
  └─ confidence / reasoning 계산
        ↓
[출력]
  ├─ chat.py : 자연어 응답 + 세션 상태 갱신
  └─ run.py  : results.json
```

### 2.2 레이어별 책임

| 레이어 | 책임 | 실패 시 원칙 |
| --- | --- | --- |
| Input Adapter | CLI/배치 입력을 공통 ticket 포맷으로 정규화 | 입력이 비어 있으면 즉시 `reject` |
| Safety Gate | 위험 요청 선차단 | 위험하면 후단으로 보내지 않음 |
| Classification/Extraction | 자연어를 구조화 | 불확실하면 `clarify` |
| Dialogue State | 멀티턴 정보 누적 및 확인 단계 제어 | 세션 없으면 단일 턴으로만 처리 |
| Storage | 기존 예약 조회/저장/갱신 | 실패 시 거짓 성공 금지 |
| Policy Engine | 결정론적 허용/불가 판정 | 정책 우선, LLM 판단 무시 가능 |
| cal.com | 외부 슬롯 조회/예약 생성 | 실패 시 안전 폴백 |
| Response Builder | 최종 응답/JSON 계약 보장 | 스키마 일관성 유지 |

---

## 3. Shared Core 원칙

### 3.1 공통 진입점
모든 비즈니스 판단은 `src/agent.py`에서 수행한다.

```text
chat.py
  └─ create_session()
  └─ process_message(user_message, session)
        └─ process_ticket(ticket, session_state=..., now=...)

run.py
  └─ run_batch(...)
        └─ process_ticket(ticket, session_state=None, now=...)
```

### 3.2 왜 공통화가 필수인가
1. Hidden test에서 **chat와 batch 간 판단 불일치**를 막기 위해
2. 의료 상담 차단이 한 모드에서만 빠지는 위험을 막기 위해
3. confidence/reasoning 산식과 JSON 스키마를 한 군데서 관리하기 위해
4. 저장소/정책/cal.com 로직을 여러 곳에 중복하지 않기 위해

### 3.3 chat와 run의 차이점
| 항목 | chat.py | run.py |
| --- | --- | --- |
| 세션 상태 | 있음 | 없음 |
| clarify 누적 처리 | 가능 | 불가(단일 턴) |
| 확인 후 확정 2단계 | 가능 | 기본적으로 한 턴 결과만 반환 |
| 저장소 접근 | 공통 계층 사용 | 공통 계층 사용 |
| Agent Core | 동일 | 동일 |

즉, **대화형 상태 유무만 다르고 판단 로직은 동일**해야 한다.

---

## 4. Safety-First 설계

### 4.1 왜 가장 먼저 검사하는가
과제 Hard Constraints는 다음을 최우선으로 요구한다.

- 의료 상담 금지
- 목적 외 사용 거부
- 허위 정보 제공 금지

따라서 예약 의도 분류보다 먼저 “응답하면 안 되는 요청”을 차단해야 한다.

### 4.2 Safety 결과 카테고리
실제 파이프라인에서 safety 단계는 최소 다음 범주를 다룬다.

| category | 최종 동작 |
| --- | --- |
| `safe` | 후속 단계 진행 |
| `medical_advice` | `reject` |
| `off_topic` | `reject` |
| `emergency` | `escalate` |
| `complaint` | `escalate` |
| `classification_error` | 안전 폴백(`clarify` 또는 `reject`) |

### 4.3 혼합 요청 필터링
예: “이 약 먹어도 되나요? 그리고 내일 내과 예약하고 싶어요.”

목표 구조는 다음과 같다.

1. 의료 부분은 거부
2. 안전한 예약 하위 요청을 별도 텍스트로 분리 가능하면 후속 처리
3. 분리 불가하면 전체 `reject`

이는 “의료 질문이 들어왔다”와 “예약 업무는 여전히 도울 수 있다”를 동시에 만족시키기 위한 구조다.

---

## 5. Deterministic Policy + LLM Hybrid

### 5.1 역할 분리

| 계층 | 담당 |
| --- | --- |
| `src/classifier.py` | safety/intent/entity extraction, 증상 기반 분과 힌트, doctor→department 매핑 |
| `src/llm_client.py` | Ollama 호출, JSON 파싱, 오류 폴백 |
| `src/policy.py` | 시간/정원/슬롯/변경 가능 여부/대체안 결정 |
| `src/agent.py` | 오케스트레이션, 세션 병합, 저장소/정책/cal.com 연결 |

### 5.2 결정론이어야 하는 것
- 정확히 24시간 전 허용 여부
- 1시간 창 최대 3명 여부
- 초진 40분 / 재진 30분
- 기존 예약 존재 여부
- 여러 예약 후보 중 모호성 발생 여부
- same-day 신규 예약의 보수 처리

### 5.3 LLM이 맡는 것
- 자유 문장 해석
- 날짜/시간/분과 표현 보조 해석
- 의사명/증상 표현 normalization 보조
- subtle safety case fallback 분류

### 5.4 LLM을 제한하는 이유
- 정책은 흔들리면 안 됨
- 결과 JSON enum이 엄격함
- 허위 정보 생성 위험이 큼

따라서 LLM은 **구조화 출력 생성기**이고, 최종 정책 판정자는 아니다.

---

## 6. LLM 계층 아키텍처

### 6.1 모델 및 호출 원칙
- 모델: `qwen3-coder:30b`
- 런타임: Ollama 로컬 실행
- structured output: `format='json'`
- 파싱 실패/연결 실패/timeout 시 안전 폴백

### 6.2 `src/llm_client.py` 책임
- 공통 모델명 고정
- `chat_json()`으로 JSON 파싱 강제
- `safe_parse_json()` 제공
- connection refused / timeout / invalid response / parse fail을 표준 오류 코드로 정규화

### 6.3 LLM 실패 처리 원칙
| 실패 유형 | 기본 폴백 |
| --- | --- |
| connection refused | `clarify` |
| timeout | `clarify` |
| JSON parse fail | `clarify` |
| 기타 Ollama 호출 실패 | `reject` 또는 `clarify` |

중요한 점은 **실패가 곧 unsafe 허용으로 이어지면 안 된다**는 것이다.

---

## 7. Dialogue / Session 아키텍처

### 7.1 세션 상태가 필요한 이유
예약 대화는 한 번에 모든 정보가 오지 않는다.

예:

1. “내일 2시 예약”
2. “내과요”
3. “네”

따라서 다음 상태를 보관해야 한다.

- 누적 slot (`date`, `time`, `department`)
- pending action
- pending confirmation
- pending candidate appointments
- conversation history

### 7.2 핵심 멀티턴 시나리오
| 시나리오 | 구조 |
| --- | --- |
| clarify 후 정보 보완 | 이전 슬롯 + 새 입력 병합 |
| 예약 확정 2단계 | 후보 제안 → 사용자 동의 → 확정 |
| 다수 예약 후보 선택 | 옵션 나열 → 번호/분과/일시로 선택 |

### 7.3 batch 모드와의 차이
batch는 세션이 없으므로 아래만 지원한다.

- single-turn 안전 판정
- single-turn 정책 판정
- 즉시 반환 가능한 결과 생성

즉, batch는 `clarify`를 반환할 수는 있지만 후속 턴을 갖지 않는다.

---

## 8. 예약 저장소 아키텍처

### 8.1 왜 저장소가 필요한가
features.json은 예약 업무를 단순 분류기가 아니라 **실제 예약 상태를 반영하는 자동화 시스템**으로 정의한다.

따라서 다음이 필요하다.

- 신규 예약이 저장되어야 함
- 변경/취소 후 상태가 남아야 함
- 조회 요청이 저장소 기준으로 답해야 함
- modify/cancel/check는 `ticket.context`가 아니라 저장소를 진실원천으로 삼아야 함

### 8.2 목표 저장소 구조
최소 기준은 JSON 파일 기반 영속 저장소다.

예시 레코드:

```json
{
  "id": "appt-001",
  "customer_name": "김민수",
  "booking_time": "2026-03-25T14:00:00+09:00",
  "department": "내과",
  "customer_type": "재진"
}
```

최종 구현에서는 아래 운영 필드 추가를 권장한다.

- `status` (`active`, `cancelled`)
- `created_at`
- `updated_at`
- `cancelled_at`
- `source` (`local`, `calcom`)
- `external_booking_id`

### 8.3 저장소 계층 책임
권장 분리:

| 모듈 | 책임 |
| --- | --- |
| `src/storage.py` (신규 권장) | load/save/find/create/update/cancel 공통 계층 |
| `src/agent.py` | storage 호출 오케스트레이션 |
| `src/policy.py` | 저장소 결과를 입력으로 받아 판정 |

초기 구현이 `agent.py` 내부 helper로 시작하더라도, 최종적으로는 **chat/run 공통 storage 계층**으로 수렴해야 한다.

### 8.4 저장소 실패 원칙
- 파일 손상
- 쓰기 실패
- 동시 갱신 충돌
- JSON decode error

위 경우 예약 성공을 거짓으로 말하면 안 된다.  
반드시 `clarify`, `reject`, 또는 연동 실패 안내로 복구한다.

---

## 9. 정책 엔진 아키텍처

### 9.1 핵심 함수군
`src/policy.py`는 최소 아래 책임을 가진다.

- `get_appointment_duration(customer_type)`
- `is_change_allowed(appointment_time, now)`
- `check_hourly_capacity(requested_start, appointments)`
- `is_slot_available(requested_start, customer_type, appointments)`
- `validate_existing_appointment(action, existing_appointment, candidates)`
- `evaluate_same_day_booking(intent, now)`
- `suggest_alternative_slots(requested_start, customer_type, appointments)`
- `apply_policy(intent, existing_appointment, all_appointments, now)`

### 9.2 정책 입력과 출력

입력:
- action
- date/time/booking_time
- department
- customer_type
- existing appointment
- all appointments
- now

출력:
- `allowed`
- `reason_code`
- `reason`
- `recommended_action`
- `needs_alternative`
- `alternative_slots`
- `slot_duration_minutes`

### 9.3 정책 결과 해석 규칙
| 정책 결과 | Agent 동작 |
| --- | --- |
| `allowed=True` | 저장/확정 또는 다음 단계 진행 |
| `recommended_action=clarify` | 부족 정보 또는 대체안 질의 |
| `recommended_action=escalate` | 사람 연결 |
| `allowed=False` + hard policy block | 자동 확정 금지 |

---

## 10. Action별 처리 흐름

### 10.1 `book_appointment`
1. safety 통과
2. 분과/날짜/시간/고객유형 확보
3. same-day 일반 예약 여부 확인
4. 정원/겹침/슬롯 길이 검증
5. chat 모드면 확인 질문 후 확정
6. 저장소 기록 생성
7. Q4 활성 시 cal.com booking 동기화

### 10.2 `modify_appointment`
1. 기존 예약 후보 조회
2. 대상 0건이면 `clarify`
3. 대상 2건 이상이면 `clarify`
4. 24시간 규칙 검증
5. 새 슬롯 가용성 검증
6. 저장소 update
7. 필요 시 cal.com reschedule 전략 적용

### 10.3 `cancel_appointment`
1. 기존 예약 후보 조회
2. 대상 모호성 해소
3. 24시간 규칙 검증
4. 저장소 cancel 반영
5. 필요 시 cal.com cancel 반영

### 10.4 `check_appointment`
1. 기존 예약 후보 조회
2. 다수면 clarify
3. 1건이면 조회 응답 생성

### 10.5 `clarify`
- 필수 정보 부족
- 대상 예약 모호함
- LLM 실패 후 안전 폴백
- 일반 당일 신규 예약 보수 처리

### 10.6 `escalate`
- 급성 통증/응급
- 강한 불만/상담원 요청
- 정책상 자동 처리보다 사람 개입이 우선인 케이스

### 10.7 `reject`
- 의료 상담
- 목적 외 사용
- 인젝션
- 확인되지 않는 정보에 대해 답변을 만들어내야 하는 상황

---

## 11. Q4 cal.com 확장 아키텍처

### 11.1 위치
cal.com은 **policy 이후, response build 이전**에만 위치한다.

### 11.2 이유
1. unsafe 요청에 외부 API를 호출하면 안 된다.
2. 정책 위반 요청에 외부 슬롯 조회를 할 이유가 없다.
3. booking은 사용자 확인 이후에만 가능하다.

### 11.3 목표 역할
| 단계 | 역할 |
| --- | --- |
| config load | API key/base url/env 확인 |
| event mapping | 분과→event type 변환 |
| slot lookup | available slots 조회 |
| slot proposal | 사용자에게 후보 제시 |
| booking create | 동의 후 실제 생성 |
| sync | 로컬 저장소와 cal.com 결과 동기화 |

### 11.4 실패 시 원칙
- 설정 누락: 연동 비활성 상태로 graceful skip
- slot 조회 실패: 거짓 가용시간 금지
- booking 실패: 거짓 예약 완료 금지
- sync 실패: 성공/실패 상태를 분리 기록하고 사람 확인 경로 제공

---

## 12. 데이터 계약

### 12.1 입력 Ticket 계약

```json
{
  "ticket_id": "T-001",
  "customer_name": "김민수",
  "customer_type": "재진",
  "message": "내일 오후 2시에 이비인후과 예약하고 싶습니다",
  "timestamp": "2025-03-16T09:30:00+09:00",
  "context": {
    "has_existing_appointment": false,
    "preferred_department": "이비인후과"
  }
}
```

### 12.2 내부 계약
- `safety_result`
- `intent_result`
- `policy_result`
- `session_state`
- `storage_record`
- `calcom_result`

이 계약은 plan.md에서 더 세밀한 필드까지 명시한다.

### 12.3 출력 계약

```json
{
  "ticket_id": "T-001",
  "classified_intent": "book_appointment",
  "department": "이비인후과",
  "action": "book_appointment",
  "response": "...",
  "confidence": 0.95,
  "reasoning": "..."
}
```

---

## 13. 장애 대응 아키텍처

### 13.1 LLM 장애
- `clarify` 또는 `reject`로 안전 복구
- unsafe 허용 금지

### 13.2 저장소 장애
- 예약 완료 메시지 금지
- 명시적 실패 응답 또는 사람 확인 유도

### 13.3 cal.com 장애
- 로컬 정책 결과와 외부 연동 결과를 분리
- 외부 성공을 확인하기 전까지 “완료”라고 말하지 않음

### 13.4 잘못된 입력
- 빈 메시지 → `reject`
- 지원하지 않는 분과/의사 → `reject`
- 모호한 기존 예약 → `clarify`

---

## 14. 테스트 가능성 설계

### 14.1 시간 의존성 분리
핵심 함수는 `now`를 인자로 받아야 한다.  
이 원칙으로 24시간 경계값, same-day 규칙, 상대 날짜 해석을 deterministic하게 테스트한다.

### 14.2 테스트 축
- safety test
- classifier test
- policy test
- dialogue/session test
- batch schema test
- generalization test
- storage test
- cal.com integration failure test

### 14.3 Hidden Test 방어 포인트
- exact enum
- no hallucination
- same chat/run core
- 경계값 명시 처리
- 기존 예약 검증의 저장소 기반화

---

## 15. 권장 모듈 구조

| 파일 | 책임 |
| --- | --- |
| `src/agent.py` | 전체 오케스트레이션, 세션 제어, 저장소/정책/응답 연결 |
| `src/classifier.py` | safety / intent / extraction |
| `src/llm_client.py` | Ollama JSON 호출 및 오류 정규화 |
| `src/policy.py` | 결정론 정책 엔진 |
| `src/response_builder.py` | 자연어 응답 및 batch JSON 조립 |
| `src/storage.py` | 예약 저장소 공통 계층 (신규 권장) |
| `src/calcom_client.py` | cal.com 설정/슬롯/booking 연동 |
| `chat.py` | 인터랙티브 어댑터 |
| `run.py` | 배치 어댑터 |

---

## 16. 결론

코비메디 Agent의 핵심 아키텍처는 **LLM 중심 챗봇**이 아니라, **Safety-first + Shared Agent Core + Deterministic Policy + Persistent Storage + Optional External Integration** 구조다.

이 구조를 택하는 이유는 명확하다.

1. 안전 요청을 가장 먼저 막아야 하고  
2. 숫자/시간 정책은 흔들리면 안 되며  
3. 예약 업무 자동화는 실제 저장소 상태를 반영해야 하고  
4. chat/run/Q4 확장이 모두 같은 코어 위에서 움직여야 하기 때문이다.

따라서 이후 구현은 이 아키텍처를 기준으로, `.ai/handoff/10_plan.md`의 세부 작업 순서대로 진행한다.