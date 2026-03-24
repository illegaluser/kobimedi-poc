# 상세 구현 설계 계획서

기준 문서: `.ai/handoff/00_request.md`, `AGENTS.md`, `.ai/harness/features.json`, `docs/policy_digest.md`, `.ai/harness/progress.md`  
목표: `features.json`의 35개 기능을 구현하기 위한 상세 설계를 정리한다.  
우선순위: **safety > correctness > policy compliance > demo polish > Q4**

추가 고정 조건:

- 런타임 LLM은 **Ollama 로컬 모델 `qwen3-coder:30b`** 를 사용한다.
- 호출 방식은 `from ollama import chat` 이다.
- 구조화 출력은 반드시 `format='json'` 을 사용한다.
- 시간 의존 로직은 `agent.py` 핵심 함수에서 `now` 파라미터를 받는다.

```python
def process_ticket(ticket, now=None):
    if now is None:
        now = datetime.now(timezone.utc)
```

- Q4 cal.com 연동은 **정책 검사 후, 응답 생성 전** 단계에 위치한다.
- cal.com 연동은 `src/calcom_client.py`에 격리하며, 설정이 없으면 graceful skip 한다.

---

## 1. 구현 순서 (Phase별, features.json ID 기준)

### Phase 2: F-001~F-005 (safety)
- F-001 의료 상담 요청을 `reject`로 차단
- F-002 목적 외 사용(잡담/인젝션)을 `reject`로 차단
- F-003 급성 통증/응급을 `escalate`로 처리
- F-004 의료+예약 혼합 요청을 안전하게 분리 처리
- F-005 확인되지 않은 분과/의사/정책/가용시간을 지어내지 않음

이 단계는 Hard Constraint와 AGENTS.md의 safety-first 규칙 때문에 최우선이다. 이후 단계가 추가되더라도 unsafe 요청은 이 단계에서 종결되도록 설계한다.

### Phase 3: F-006~F-011 (classification + extraction)
- F-006 7개 action taxonomy 정확 분류
- F-007 분과 올바른 추정
- F-008 증상 기반 분과 안내, 의료 진단 금지
- F-009 의사명 요청을 분과/의사 매핑으로 해석
- F-010 자유문장에서 예약 핵심 슬롯 정보 추출
- F-011 정보 부족 시 `clarify` 반환

안전 요청만 남긴 뒤 자연어를 구조화 데이터로 바꾸는 단계다. extraction 결과가 다음 policy.py의 결정론 판정 입력이 된다.

### Phase 4: F-015~F-020 (policy)
- F-015 1시간당 최대 3명 제한
- F-016 당일 변경/취소 금지와 24시간 규칙
- F-017 초진 40분 / 재진 30분 슬롯
- F-018 기존 예약 유무와 대상 식별 정합성 검증
- F-019 당일 신규 예약 일반/응급 구분 보수 처리
- F-020 슬롯 불가 시 대체 시간 또는 대체 처리 안내

이 단계는 LLM이 아니라 순수 결정론으로 구현한다. classification/extraction 결과를 받아 허용·불가·대체안 여부를 확정한다.

### Phase 5: F-012~F-014 (dialogue)
- F-012 clarify 이후 대화 컨텍스트 유지
- F-013 예약 제안 후 사용자 확인 2단계 흐름 지원
- F-014 여러 기존 예약 또는 불명확한 대상의 모호성 해소

멀티턴은 core 분류/정책이 안정화된 뒤 붙여야 한다. session_state 중심으로 pending intent와 missing fields를 누적 관리한다.

### Phase 6: F-021~F-024 (runtime)
- F-021 `chat.py`가 `src/agent.py` 공통 로직 호출 + 멀티턴 지원
- F-022 `run.py`가 `src/agent.py` 공통 로직 호출 + 배치 처리
- F-023 배치 JSON이 과제 예시 키와 의미 정합성 충족
- F-024 `confidence`/`reasoning`을 실제 판단 근거로 생성

입출력 어댑터는 이 단계에서 정리한다. chat/run이 서로 다른 판단 로직을 가지지 않도록 core를 완전히 공유한다.

### Phase 7: F-025~F-026 (reliability + evaluation)
- F-025 golden_eval 기반 일반화 점검 가능
- F-026 Ollama 호출 실패/비정상 JSON 안전 폴백 제공

기능이 동작하더라도 실패 내성과 평가 루프가 없으면 PoC로 불안정하다. 특히 LLM 실패는 unsafe 허용 없이 `clarify/reject`로 안전 복구해야 한다.

### Phase 8: F-028~F-035 (Q4 cal.com)
- F-028 cal.com 환경설정 로드
- F-029 3개 분과를 Event Type으로 정확히 매핑
- F-030 `book_appointment` 요청 시 available slots 조회
- F-031 가용 슬롯 제안 기반 2단계 예약 흐름
- F-032 사용자 확인 후 실제 booking 생성
- F-033 cal.com API 실패 시 거짓 성공 없이 안전 복구
- F-034 `chat.py`와 `run.py`가 동일 cal.com 연동 Agent 로직 공유
- F-035 Q4 외부 준비사항과 Event Type 체크리스트 문서 반영

Q4는 선택 과제이므로 필수 기능 안정화 후 붙인다. 다만 연결 위치는 반드시 `policy 후, response 전`으로 고정한다.

### Phase 9: F-027 (documentation)
- F-027 `final_report.md`가 필수 제출 항목을 모두 포함

문서는 실제 구현 결과와 데모 증빙이 정리된 뒤 마무리한다.

---

## 2. 각 기능의 구현 방법 (파일명 + 핵심 함수 시그니처 + 로직 1~2문장)

### Phase 2: Safety

#### F-001 의료 상담 요청 차단
- **파일명**: `src/classifier.py`, `src/prompts.py`, `src/agent.py`
- **핵심 함수 시그니처**:
  - `def classify_safety(message: str) -> dict:`
  - `def process_ticket(ticket: dict, all_appointments: list | None = None, existing_appointment: dict | None = None, session_state: dict | None = None, now: datetime | None = None) -> dict:`
- **로직**: safety prompt에서 진단/약물/치료 조언 여부를 JSON으로 분류한다. `medical_advice`이면 agent는 즉시 `reject` 응답을 반환하고 이후 단계로 진행하지 않는다.

#### F-002 목적 외 사용/인젝션 차단
- **파일명**: `src/classifier.py`, `src/prompts.py`, `src/agent.py`
- **핵심 함수 시그니처**:
  - `def classify_safety(message: str) -> dict:`
- **로직**: 날씨/잡담/타 서비스 문의와 프롬프트 인젝션을 `off_topic` 또는 별도 unsafe subtype으로 판정한다. agent는 모두 `reject`로 처리하고 내부 지침/프롬프트는 공개하지 않는다.

#### F-003 급성 통증/응급 escalate
- **파일명**: `src/classifier.py`, `src/agent.py`, `src/response_builder.py`
- **핵심 함수 시그니처**:
  - `def classify_safety(message: str) -> dict:`
  - `def build_escalation_response(ticket: dict, safety_result: dict) -> dict:`
- **로직**: 급성 통증/응급/즉시 진료 요구를 safety 단계에서 `emergency`로 식별한다. response_builder는 자동 예약 대신 상담원 또는 의사 확인 필요 안내를 만든다.

#### F-004 의료+예약 혼합 요청 분리 처리
- **파일명**: `src/classifier.py`, `src/agent.py`
- **핵심 함수 시그니처**:
  - `def classify_safety(message: str) -> dict:`
  - `def process_ticket(..., session_state: dict | None = None, now: datetime | None = None) -> dict:`
- **로직**: safety 결과에 `contains_booking_subrequest`와 `safe_booking_text`를 담아 예약 부분만 후속 처리할 수 있게 한다. 의료 판단 부분은 거부하되 예약 가능한 부분만 classification으로 넘기거나, 안전하게 처리 불가 시 전체 `reject`로 종료한다.

#### F-005 확인되지 않은 정보 비창작
- **파일명**: `src/classifier.py`, `src/policy.py`, `src/response_builder.py`
- **핵심 함수 시그니처**:
  - `def validate_department(raw_department: str | None) -> str | None:`
  - `def build_response(..., reasoning_parts: list[str], confidence: float, **kwargs) -> dict:`
- **로직**: 허용된 분과/의사/정책 값만 통과시키고 불명확 값은 `None` 또는 `clarify`로 강등한다. response_builder는 확인되지 않은 가용시간이나 정책을 문장에 넣지 않는다.

### Phase 3: Classification + Extraction

#### F-006 7개 action 정확 분류
- **파일명**: `src/classifier.py`, `src/llm_client.py`, `src/prompts.py`
- **핵심 함수 시그니처**:
  - `def classify_intent(message: str, context: dict | None = None) -> dict:`
  - `def chat_json(system_prompt: str, user_prompt: str, schema_hint: dict | None = None) -> dict:`
- **로직**: action 후보를 과제 원문 7개 enum으로 제한한 prompt를 사용한다. validator는 enum 밖의 값이나 누락 응답을 `clarify`로 폴백한다.

#### F-007 분과 추정
- **파일명**: `src/classifier.py`, `src/utils.py`
- **핵심 함수 시그니처**:
  - `def infer_department(message: str, extracted: dict) -> str | None:`
- **로직**: 명시 분과가 있으면 그대로 사용하고, 없으면 증상 키워드·의사명·ticket context를 근거로 추정한다. 확신이 부족하면 분과를 비워 두고 clarify로 넘긴다.

#### F-008 증상 기반 분과 안내, 의료 진단 금지
- **파일명**: `src/classifier.py`, `src/response_builder.py`
- **핵심 함수 시그니처**:
  - `def classify_intent(message: str, context: dict | None = None) -> dict:`
  - `def build_clarify_response(ticket: dict, intent_result: dict, missing_fields: list[str]) -> dict:`
- **로직**: 증상은 질병 판단이 아니라 분과 추천 신호로만 사용한다. 응답은 “어느 과 예약을 도와드릴 수 있다” 수준으로 제한하고, 진단명/치료법은 생성하지 않는다.

#### F-009 의사명 요청의 분과 매핑
- **파일명**: `src/classifier.py`, `src/utils.py`
- **핵심 함수 시그니처**:
  - `def map_doctor_to_department(name: str | None) -> str | None:`
- **로직**: 이춘영→이비인후과, 김만수→내과, 원징수→정형외과의 고정 매핑을 사용한다. 없는 의사명은 추정하지 않고 `clarify` 또는 처리 불가 안내로 전환한다.

#### F-010 예약 핵심 슬롯 정보 추출
- **파일명**: `src/classifier.py`, `src/llm_client.py`, `src/utils.py`
- **핵심 함수 시그니처**:
  - `def extract_entities(message: str, ticket: dict, session_state: dict | None = None, now: datetime | None = None) -> dict:`
  - `def normalize_datetime_phrase(raw_text: str, now: datetime) -> str | None:`
- **로직**: 날짜/시간/분과/의사명/초진·재진/대상 예약 식별 정보를 구조화한다. 상대 시각 표현은 `now` 기준으로 해석하여 freezegun 테스트와 일치시킨다.

#### F-011 정보 부족 시 clarify
- **파일명**: `src/agent.py`, `src/response_builder.py`
- **핵심 함수 시그니처**:
  - `def determine_missing_fields(action: str, extracted: dict, ticket: dict, existing_appointment: dict | None = None) -> list[str]:`
  - `def build_clarify_response(ticket: dict, intent_result: dict, missing_fields: list[str]) -> dict:`
- **로직**: action별 필수 필드를 정의하고 누락 항목을 계산한다. 누락이 있으면 policy 전에 `clarify` 응답으로 전환한다.

### Phase 4: Policy

#### F-015 1시간당 최대 3명 제한
- **파일명**: `src/policy.py`
- **핵심 함수 시그니처**:
  - `def check_hourly_capacity(requested_start: datetime, all_appointments: list[dict]) -> tuple[bool, str | None]:`
- **로직**: 요청 시작 시각이 속한 1시간 윈도우의 예약 수를 계산한다. 4번째 예약이면 자동 확정하지 않고 대체안 단계로 넘긴다.

#### F-016 24시간 변경/취소 규칙
- **파일명**: `src/policy.py`
- **핵심 함수 시그니처**:
  - `def is_change_allowed(appointment_time_str: str, now: datetime) -> bool:`
  - `def apply_policy(intent: dict, existing_appointment: dict | None, all_appointments: list[dict], now: datetime) -> dict:`
- **로직**: `now <= 예약시각 - 24시간`이면 허용, 그보다 늦으면 불가로 처리한다. 정확히 24시간 전은 허용 경계값으로 고정한다.

#### F-017 초진 40분 / 재진 30분 슬롯
- **파일명**: `src/policy.py`
- **핵심 함수 시그니처**:
  - `def get_appointment_duration(customer_type: str | None) -> int | None:`
  - `def is_slot_available(requested_start_str: str, customer_type: str, existing_appointments: list[dict]) -> tuple[bool, str]:`
- **로직**: 초진 40분, 재진 30분 기준으로 겹침 여부를 계산한다. `customer_type`이 없으면 availability 계산을 하지 않고 clarify가 필요하다는 상태로 보낸다.

#### F-018 기존 예약 유무/대상 식별 정합성
- **파일명**: `src/policy.py`, `src/agent.py`
- **핵심 함수 시그니처**:
  - `def validate_existing_appointment(action: str, existing_appointment: dict | None, candidate_appointments: list[dict] | None = None) -> dict:`
- **로직**: modify/cancel/check는 기존 예약이 있어야 한다. 예약이 없거나 후보가 여러 건이면 바로 거절하지 말고 clarify 가능한 상태를 반환한다.

#### F-019 당일 신규 예약 보수 처리
- **파일명**: `src/policy.py`, `src/response_builder.py`
- **핵심 함수 시그니처**:
  - `def evaluate_same_day_booking(intent: dict, now: datetime) -> dict:`
- **로직**: 일반 당일 신규 예약은 자동 확정하지 않고 재확인 또는 대체 시간 안내로 보낸다. 응급은 safety 단계에서 이미 `escalate`되므로 policy는 일반 당일 예약만 보수 처리한다.

#### F-020 슬롯 불가 시 대체 시간/대체 처리 안내
- **파일명**: `src/policy.py`, `src/response_builder.py`
- **핵심 함수 시그니처**:
  - `def suggest_alternative_slots(requested_start_str: str, customer_type: str, all_appointments: list[dict], limit: int = 3) -> list[str]:`
  - `def build_policy_response(ticket: dict, policy_result: dict, intent_result: dict) -> dict:`
- **로직**: 요청 슬롯이 불가하면 인접한 시간대 후보를 계산한다. response_builder는 단순 불가 통지 대신 대체 시간이나 후속 절차를 함께 안내한다.

### Phase 5: Dialogue

#### F-012 clarify 후 대화 컨텍스트 유지
- **파일명**: `src/agent.py`, `chat.py`
- **핵심 함수 시그니처**:
  - `def merge_session_context(ticket: dict, session_state: dict | None) -> dict:`
  - `def process_ticket(..., session_state: dict | None = None, now: datetime | None = None) -> dict:`
- **로직**: session_state에 마지막 action 후보, 누락 필드, 추출된 entities를 저장한다. 후속 입력이 들어오면 이전 pending context와 새 메시지를 합쳐 재처리한다.

#### F-013 예약 2단계 확인 흐름
- **파일명**: `src/agent.py`, `src/response_builder.py`
- **핵심 함수 시그니처**:
  - `def handle_confirmation_step(ticket: dict, session_state: dict | None, policy_result: dict) -> dict:`
  - `def build_confirmation_response(ticket: dict, intent_result: dict, slot_payload: dict) -> dict:`
- **로직**: 첫 턴에서는 예약 후보 또는 가용 슬롯을 제안하고 `pending_confirmation` 상태를 저장한다. 사용자가 동의하면 같은 agent core가 최종 확정 또는 Q4 booking 단계로 이어진다.

#### F-014 여러 기존 예약/불명확 대상 모호성 해소
- **파일명**: `src/agent.py`, `src/response_builder.py`
- **핵심 함수 시그니처**:
  - `def resolve_target_appointment(ticket: dict, candidate_appointments: list[dict]) -> dict:`
- **로직**: 기존 예약이 여러 건이면 날짜/분과/시간 기준으로 후보를 좁힌다. 한 건으로 확정되지 않으면 `clarify` 상태를 유지하고 재질문한다.

### Phase 6: Runtime

#### F-021 chat.py 공통 agent 로직 + 멀티턴
- **파일명**: `chat.py`, `src/agent.py`
- **핵심 함수 시그니처**:
  - `def main() -> None:`
  - `def process_ticket(..., session_state: dict | None = None, now: datetime | None = None) -> dict:`
- **로직**: chat.py는 CLI 입출력과 session_state 저장만 담당한다. 실제 판단은 모두 `process_ticket`로 위임한다.

#### F-022 run.py 공통 agent 로직 배치 처리
- **파일명**: `run.py`, `src/agent.py`
- **핵심 함수 시그니처**:
  - `def run_batch(input_path: str, output_path: str, now: datetime | None = None) -> list[dict]:`
- **로직**: run.py는 입력 JSON을 순회하며 각 ticket을 `process_ticket`에 전달한다. 배치도 동일 core를 사용해 chat와 판단 일관성을 보장한다.

#### F-023 배치 JSON 스키마 정합성
- **파일명**: `src/response_builder.py`, `run.py`
- **핵심 함수 시그니처**:
  - `def build_batch_result(ticket: dict, action: str, classified_intent: str, department: str | None, response: str, confidence: float, reasoning: str) -> dict:`
- **로직**: 과제 예시의 `ticket_id`, `classified_intent`, `department`, `action`, `response`, `confidence`, `reasoning` 키를 항상 채운다. chat 모드도 내부적으로 동일 payload를 공유해 스키마 드리프트를 막는다.

#### F-024 confidence/reasoning 근거 기반 생성
- **파일명**: `src/response_builder.py`, `src/agent.py`
- **핵심 함수 시그니처**:
  - `def compute_confidence(safety_result: dict, intent_result: dict, policy_result: dict) -> float:`
  - `def build_reasoning(safety_result: dict, intent_result: dict, extraction_result: dict, policy_result: dict) -> str:`
- **로직**: confidence는 rule-based score로 계산한다. reasoning은 실제 안전 판정, 추출 성공, 정책 검토 결과를 조합하여 생성하고 고정 문구 하드코딩을 피한다.

### Phase 7: Reliability + Evaluation

#### F-025 golden_eval 일반화 점검
- **파일명**: `golden_eval/eval.py`, `tests/test_generalization.py`
- **핵심 함수 시그니처**:
  - `def evaluate_gold_cases(gold_path: str, now: datetime | None = None) -> dict:`
- **로직**: gold case를 agent core에 통과시켜 expected action/department와 비교한다. accuracy 외에 safety miss와 hard fail을 별도로 집계한다.

#### F-026 Ollama 실패/비정상 JSON 폴백
- **파일명**: `src/llm_client.py`, `src/classifier.py`
- **핵심 함수 시그니처**:
  - `def chat_json(system_prompt: str, user_prompt: str, schema_hint: dict | None = None) -> dict:`
  - `def safe_parse_json(raw_content: str) -> dict | None:`
- **로직**: `from ollama import chat` 호출을 한 곳에 모으고 JSON decode error, timeout, connection failure를 표준 에러코드로 변환한다. classifier는 이를 받아 unsafe 허용 없이 `clarify` 또는 `reject`로 복구한다.

### Phase 8: Q4 cal.com

#### F-028 cal.com 환경설정 로드
- **파일명**: `src/calcom_client.py`
- **핵심 함수 시그니처**:
  - `def load_calcom_config() -> dict:`
- **로직**: API key, base URL, username, event type 식별자를 env에서 읽고 누락 여부를 검사한다. 설정이 없으면 `enabled=False`를 반환해 graceful skip 한다.

#### F-029 분과 ↔ Event Type 정확 매핑
- **파일명**: `src/calcom_client.py`, `src/utils.py`
- **핵심 함수 시그니처**:
  - `def map_department_to_event_type(department: str) -> str | None:`
- **로직**: 이비인후과→`ent-consultation`, 내과→`internal-medicine`, 정형외과→`orthopedics` 매핑을 고정 테이블로 둔다. 허용되지 않은 분과는 API 호출 전에 차단한다.

#### F-030 available slots 조회
- **파일명**: `src/calcom_client.py`, `src/agent.py`
- **핵심 함수 시그니처**:
  - `def get_available_slots(department: str, requested_date: str, customer_type: str, config: dict) -> dict:`
- **로직**: policy를 통과한 `book_appointment` 요청만 available slots API로 보낸다. 응답은 agent가 쓰기 쉬운 normalized slot list로 변환한다.

#### F-031 가용 슬롯 제안 기반 2단계 흐름
- **파일명**: `src/agent.py`, `src/response_builder.py`, `src/calcom_client.py`
- **핵심 함수 시그니처**:
  - `def handle_booking_with_calcom(ticket: dict, intent_result: dict, policy_result: dict, session_state: dict | None) -> dict:`
- **로직**: cal.com이 활성화되어 있으면 조회된 slot 후보를 사용자에게 제안하고 선택 상태를 세션에 저장한다. 다음 턴에서 사용자가 slot을 고르면 booking 생성 단계로 이동한다.

#### F-032 실제 booking 생성
- **파일명**: `src/calcom_client.py`, `src/agent.py`
- **핵심 함수 시그니처**:
  - `def create_booking(slot_payload: dict, ticket: dict, config: dict) -> dict:`
- **로직**: 사용자가 확인한 slot에 대해 booking API를 호출하고 booking id/start time/event info를 정규화해 반환한다. 성공 시 response_builder가 실제 예약 완료 응답을 생성한다.

#### F-033 cal.com 실패 시 안전 폴백
- **파일명**: `src/calcom_client.py`, `src/agent.py`, `src/response_builder.py`
- **핵심 함수 시그니처**:
  - `def normalize_calcom_error(exc: Exception | dict) -> dict:`
- **로직**: 설정 누락, slot 조회 실패, booking 실패를 명시적 오류코드로 정규화한다. agent는 거짓 성공을 반환하지 않고 `clarify`, `escalate`, 또는 자동 연동 불가 안내로 복구한다.

#### F-034 chat/run 공통 cal.com 로직 공유
- **파일명**: `src/agent.py`, `chat.py`, `run.py`
- **핵심 함수 시그니처**:
  - `def process_ticket(..., session_state: dict | None = None, now: datetime | None = None) -> dict:`
- **로직**: chat와 run은 cal.com API를 직접 호출하지 않고 모두 `agent.py`를 통해 같은 흐름을 사용한다. Q4 활성 여부, graceful skip, slot 조회, booking 생성이 한 곳에서 관리된다.

#### F-035 Q4 준비사항 문서화
- **파일명**: `docs/architecture.md`, `docs/final_report.md`
- **핵심 함수 시그니처**: 문서 기능이므로 코드 시그니처 없음
- **로직**: cal.com 가입, API key 설정, Event Type 3개 생성, 테스트 방법, 캘린더 스크린샷 확보 절차를 체크리스트로 문서화한다.

### Phase 9: Documentation

#### F-027 final_report.md 필수 제출 항목 포함
- **파일명**: `docs/final_report.md`, `docs/q1_metric_rubric.md`, `docs/q3_safety.md`, `docs/demo_evidence.md`
- **핵심 함수 시그니처**: 문서 기능이므로 코드 시그니처 없음
- **로직**: Q1 metric rubric, Q2 아키텍처, 데모 증빙, Q3 안전 대응, 사용 AI 도구 내역을 빠짐없이 포함한다. action taxonomy와 정책 설명은 코드 설계와 동일하게 맞춘다.

---

## 3. `src/` 모듈 간 호출 관계

```text
agent.py
  ├─ classifier.py
  ├─ policy.py
  ├─ response_builder.py
  └─ calcom_client.py

classifier.py
  └─ llm_client.py
       └─ ollama chat(format='json')

policy.py
  └─ 순수 결정론 (ollama 없음)

llm_client.py
  └─ ollama chat(format='json') + 에러 처리

calcom_client.py
  └─ requests + cal.com API
```

### 모듈별 책임

#### `src/agent.py`
- 전체 오케스트레이터이자 단일 진실원천.
- 입력 정규화 → safety → classification/extraction → missing field 확인 → policy → cal.com(Q4) → response build 순서로 처리.
- `process_ticket(ticket, now=None)` 기본 형태를 유지하고, 멀티턴용 `session_state`를 선택적으로 받도록 확장.

#### `src/classifier.py`
- 메시지를 safety 결과와 구조화된 intent/extraction 결과로 변환.
- 직접 Ollama를 부르지 않고 `llm_client.py`를 경유.
- 허용되지 않은 action/department/value를 validator로 걸러 `clarify` 또는 error code로 강등.

#### `src/llm_client.py`
- `from ollama import chat` 기반 공통 호출 계층.
- 모든 호출에 `format='json'`을 강제하고 JSON parse/예외/비정상 응답을 표준 오류 구조로 반환.
- 외부 모델 의존성을 classifier에서 분리해 테스트를 쉽게 만든다.

#### `src/policy.py`
- 시간, 정원, 슬롯 길이, 기존 예약 존재 여부를 판정하는 순수 결정론 엔진.
- LLM을 쓰지 않으며 구조화 입력만 사용.
- 결과는 `allowed`, `reason_code`, `reason`, `needs_alternative`, `alternative_slots` 등을 포함.

#### `src/response_builder.py`
- safety/intent/policy/cal.com 결과를 종합해 최종 사용자 응답과 배치 JSON을 조립.
- `confidence`와 `reasoning`을 실제 파이프라인 결과 기반으로 계산.
- 미확인 사실이 최종 문장에 들어가지 않도록 마지막 방어선을 담당.

#### `src/calcom_client.py`
- Q4 외부 연동 전용 계층.
- 설정 로드, event type 매핑, slot 조회, booking 생성, API 오류 정규화를 담당.
- 설정 누락 시 graceful skip, 실패 시 false success 없이 안전 복구 상태만 반환.

---

## 4. 데이터 흐름 (ticket dict → 각 단계 → result dict)

### 입력 예시

```python
ticket = {
    "ticket_id": "T-001",
    "customer_name": "김민수",
    "customer_type": "재진",
    "message": "내일 오후 2시에 이비인후과 예약하고 싶습니다",
    "timestamp": "2025-03-16T09:30:00+09:00",
    "context": {
        "has_existing_appointment": False,
        "preferred_department": "이비인후과"
    }
}
```

### Step 0. normalize input (`agent.py`)
- ticket에서 `message`, `customer_type`, `context`, `timestamp`를 정규화한다.
- `now`가 없으면 `datetime.now(timezone.utc)`를 사용한다.

```python
normalized = {
    "ticket": ticket,
    "message": ticket["message"],
    "customer_type": ticket.get("customer_type"),
    "context": ticket.get("context", {}),
    "now": now,
}
```

### Step 1. safety gate (`classifier.py`)
- `message`를 safety classifier에 보내 unsafe 여부를 판정한다.
- 혼합 요청이면 `safe_booking_text`를 추가로 반환할 수 있다.

```python
safety_result = {
    "category": "safe",
    "contains_booking_subrequest": False,
    "safe_booking_text": None,
    "reason": "예약 관련 일반 문의"
}
```

### Step 2. classification + extraction (`classifier.py`)
- 안전한 메시지를 action, department, doctor, datetime, customer_type, target appointment 등으로 구조화한다.

```python
intent_result = {
    "classified_intent": "book_appointment",
    "action": "book_appointment",
    "department": "이비인후과",
    "doctor_name": "이춘영 원장",
    "booking_time": "2026-03-17T05:00:00Z",
    "customer_type": "재진",
    "missing_fields": [],
    "raw_confidence": 0.86
}
```

### Step 3. clarification guard (`agent.py`)
- 필수 정보가 부족하면 policy 전에 `clarify`로 전환한다.
- 멀티턴이면 `session_state.pending_slots`에 누락 필드를 저장한다.

```python
clarify_state = {
    "action": "clarify",
    "missing_fields": ["booking_time", "customer_type"],
    "pending_intent": {"action": "book_appointment"}
}
```

### Step 4. policy check (`policy.py`)
- 구조화된 intent와 기존 예약/전체 예약 데이터를 바탕으로 허용 여부와 대체안을 계산한다.

```python
policy_result = {
    "allowed": True,
    "reason_code": "SUCCESS",
    "reason": "정책 검사를 통과했습니다.",
    "needs_alternative": False,
    "alternative_slots": []
}
```

또는:

```python
policy_result = {
    "allowed": False,
    "reason_code": "SLOT_FULL_CAPACITY",
    "reason": "해당 시간대에는 예약 인원이 가득 찼습니다. 다른 시간대를 선택해 주세요.",
    "needs_alternative": True,
    "alternative_slots": [
        "2026-03-17T06:00:00Z",
        "2026-03-17T06:30:00Z"
    ]
}
```

### Step 5. Q4 cal.com (`calcom_client.py`, optional)
- 조건: `action == 'book_appointment'`, policy allowed, cal.com enabled.
- slot 조회 및 booking 생성은 policy 이후에만 실행한다.

```python
calcom_result = {
    "enabled": True,
    "status": "slots_proposed",
    "event_type": "ent-consultation",
    "slot_options": [
        {"start": "2026-03-17T05:00:00Z", "end": "2026-03-17T05:30:00Z"}
    ],
    "booking": None
}
```

graceful skip 예:

```python
calcom_result = {
    "enabled": False,
    "status": "skipped",
    "reason": "missing_config"
}
```

### Step 6. response build (`response_builder.py`)
- safety, intent, policy, cal.com 결과를 종합해 최종 result dict를 만든다.
- `confidence`와 `reasoning`은 실제 근거를 기반으로 생성한다.

```python
result = {
    "ticket_id": "T-001",
    "classified_intent": "book_appointment",
    "department": "이비인후과",
    "action": "book_appointment",
    "response": "김민수님, 내일 오후 2시 이비인후과 예약 요청을 확인했습니다. 예약 가능 여부를 확인해 진행할까요?",
    "confidence": 0.91,
    "reasoning": "안전 요청으로 분류되었고, 이비인후과와 예약 시각이 추출되었으며 정책 위반이 확인되지 않았습니다."
}
```

### End-to-End 요약

```text
ticket dict
  ↓
agent.process_ticket(ticket, now=...)
  ↓
normalize input
  ↓
safety gate
  ├─ unsafe -> reject/escalate result dict
  └─ safe -> continue
  ↓
classification + extraction
  ↓
missing field check
  ├─ missing -> clarify result dict + session_state update
  └─ complete -> continue
  ↓
policy check (deterministic)
  ├─ disallowed -> policy response result dict
  └─ allowed -> continue
  ↓
(optional) cal.com slots/booking
  ↓
response_builder
  ↓
result dict
```

---

## 5. 테스트 전략 (5종 테스트 파일 × 어떤 기능을 검증하는가)

### `tests/test_safety.py`
- **검증 기능**: F-001, F-002, F-003, F-004, F-005
- **전략**: unsafe일 때 classifier/policy가 호출되지 않는 제어 흐름을 검증한다. 의료 상담, 잡담, prompt injection, 응급, empty message, 혼합 요청을 회귀 케이스로 유지한다.

### `tests/test_classifier.py`
- **검증 기능**: F-006, F-007, F-008, F-009, F-010, F-011, F-026
- **전략**: Ollama 또는 `llm_client`를 mock해 action enum 검증, 분과 추정, 의사명 매핑, datetime 추출, missing field 처리, JSON 파싱 오류 폴백을 확인한다.

### `tests/test_policy.py`
- **검증 기능**: F-015, F-016, F-017, F-018, F-019, F-020
- **전략**: freezegun으로 `now`를 고정하고 24시간 경계값, 3명/4명 용량, 초진/재진 충돌, 기존 예약 유무, same-day 신규 예약, 대체 슬롯 생성을 검증한다.

### `tests/test_batch.py`
- **검증 기능**: F-021, F-022, F-023, F-024, F-034
- **전략**: run.py가 각 ticket에 대해 `process_ticket`를 호출하는지, 결과 JSON 키가 모두 존재하는지, action enum이 유효한지, `confidence/reasoning`이 비어 있지 않은지 검증한다.

### `tests/test_generalization.py`
- **검증 기능**: F-004, F-005, F-012, F-013, F-014, F-025, F-033
- **전략**: 한국어 injection 변형, 의료+예약 혼합, clarify 누적 대화, 복수 예약 후보, 존재하지 않는 의사/분과, cal.com 실패 같은 edge case를 시나리오 단위로 검증한다.

---

## 마무리 설계 메모

1. 현재 코드베이스는 골격은 있으나 35개 기능을 충족하려면 safety/intent/extraction/policy/dialogue/runtime/reliability/Q4를 명시적으로 계층화해야 한다.
2. 핵심 리스크는 LLM 출력 불안정성, 시간 의존 정책, chat/run 로직 분기 오염이다.
3. 따라서 실제 구현은 반드시 **Phase 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9** 순서로 진행하는 것이 가장 안전하다.