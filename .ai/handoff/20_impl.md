# 구현 갭 분석 및 Phase별 작업 계획

기준 문서: `.ai/handoff/00_request.md`, `.ai/handoff/10_plan.md`, `docs/architecture.md`, `docs/policy_digest.md`, `AGENTS.md`, `.ai/harness/features.json`, `.ai/harness/progress.md`  
분석 대상 코드: `src/*.py`, `chat.py`, `run.py`, `docs/final_report.md`, `tests/*.py`, `data/appointments.json`, `data/bookings.json`

---

## 1. 목적

이 문서는 `features.json`에서 `passes: false`인 기능만 추려, `10_plan.md`의 **Phase 순서**에 맞춰 실제 구현 작업으로 변환한 문서다.  
각 기능마다 아래 4가지를 반드시 포함한다.

1. 어떤 파일을 수정/생성해야 하는가
2. 어떤 함수를 추가/변경해야 하는가
3. 어떤 테스트를 작성해야 하는가
4. 기존 `passes: true` 기능에 영향이 있는가 (회귀 위험)

우선순위는 항상 다음을 따른다.

**safety > correctness > policy compliance > demo polish > Q4**

---

## 2. 현재 코드 기준 전체 요약

### 2.1 이미 동작하는 영역 (`passes: true`)
- Safety gate 기본 차단(F-001, F-002, F-003)
- action 7종 분류 및 핵심 추출(F-006~F-011)
- clarify/확인/후보 선택 대화 흐름(F-012~F-014)
- 정책 기본 엔진(F-015~F-020)
- chat/run 공통 core, 배치 출력, confidence/reasoning(F-021~F-024)
- Ollama 안전 폴백(F-026)
- now 주입 가능(F-040)
- 평가 입력 계약(F-041)

### 2.2 현재 명확한 갭 (`passes: false`)
- **혼합 요청 안전 분리(F-004)**: 현재는 의료 질문이 섞이면 전체 reject로 끝남
- **저장소 진실원천/영속화(F-018, F-036~F-039)**: `src/storage.py`가 비어 있고, `src/agent.py`가 직접 `data/appointments.json`을 읽으며 실제 create/update/cancel persist가 없음
- **문서 완성(F-027, F-035)**: `docs/final_report.md`가 골격만 있고 Q4 준비 문서가 없음
- **Q4 cal.com 전체(F-028~F-034)**: `src/calcom_client.py`가 비어 있고 agent orchestration도 없음

### 2.3 현재 코드에서 확인된 핵심 사실
- `src/agent.py`
  - `_load_appointments_from_disk()`가 `data/appointments.json`을 직접 읽음
  - 예약 확정 시 `build_success_message()`만 호출하고 파일 저장은 하지 않음
  - modify/cancel/check도 전달받은 `all_appointments` 리스트 기반으로만 처리하며 저장소 CRUD가 없음
  - confirmation 완료 시 storage/cal.com 호출 없이 바로 “예약이 완료되었습니다” 응답 가능
- `src/storage.py`는 비어 있음
- `src/calcom_client.py`는 비어 있음
- `tests/test_storage.py`, `tests/test_calcom.py`, `tests/test_batch.py`는 비어 있음
- `docs/final_report.md`는 목차만 있고 실질 내용이 없음
- `AGENTS.md`는 저장소 진실원천을 **`data/bookings.json`** 으로 고정하고 있음
- 반면 현재 코드/샘플 데이터는 `data/appointments.json`을 사용하고 있어, **문서-코드 불일치**가 존재함

---

## 3. `passes: false` 기능 목록

| ID | 범주 | 설명 |
| --- | --- | --- |
| F-004 | safety | 의료+예약 혼합 요청을 안전하게 분리 처리 |
| F-018 | policy | modify/cancel/check를 영속 저장소 기준으로 검증 |
| F-027 | documentation | `final_report.md` 필수 제출 항목 완성 |
| F-028 | integration | cal.com 환경설정 로드 |
| F-029 | integration | 분과↔Event Type 정확 매핑 |
| F-030 | integration | available slots 조회 |
| F-031 | dialogue | chat slot 제안 / run 자동 booking |
| F-032 | integration | 실제 cal.com booking 생성 |
| F-033 | reliability | cal.com 실패 시 안전 폴백 |
| F-034 | runtime | chat/run 공통 cal.com 로직 공유 |
| F-035 | documentation | Q4 외부 준비사항 문서화 |
| F-036 | storage | 신규 예약 영속 저장 |
| F-037 | storage | 변경/취소 영속 반영 |
| F-038 | runtime | chat/run 공통 저장소 계층 공유 |
| F-039 | reliability | 저장소 실패 시 거짓 성공 금지 |

---

## 4. Phase별 구현 계획

`10_plan.md`의 순서를 그대로 따른다. 현재 `passes:false` 기능은 아래 Phase에 매핑된다.

| Phase | 포함 기능 |
| --- | --- |
| Phase 1. 저장소 계층 도입/정리 | F-036, F-037, F-038, F-039 |
| Phase 2. Safety 완결 | F-004 |
| Phase 6. Persistence 연결 / 저장소 진실원천 | F-018 |
| Phase 8. Q4 cal.com 구현 | F-028, F-029, F-030, F-031, F-032, F-033, F-034 |
| Phase 9. 제출 문서 마감 | F-027, F-035 |

아래 세부 작업은 반드시 이 순서대로 진행한다.

---

## 5. Phase 1 — 저장소 계층 도입/정리

핵심 목표: `src/agent.py`의 직접 파일 접근을 제거하고, **`data/bookings.json`을 진실원천으로 사용하는 공통 storage 계층**을 먼저 만든다.

### F-036 신규 예약이 영속 JSON 저장소에 실제로 기록된다

#### 현재 갭
- chat confirmation 후 `book_appointment` 성공 응답은 생성되지만 파일 저장이 전혀 일어나지 않는다.
- batch 모드도 success response만 반환할 뿐, 예약 레코드를 추가하지 않는다.
- `src/storage.py`가 비어 있다.

#### 수정/생성 파일
- **수정**: `src/agent.py`
- **생성/구현**: `src/storage.py`
- **수정 가능**: `src/models.py` (typed dict/dataclass 정리 시)
- **수정 가능**: `tests/test_storage.py`
- **수정 가능**: `tests/test_dialogue.py`
- **수정 가능**: `tests/test_batch.py`
- **데이터 정리 필요**: `data/bookings.json`

#### 추가/변경 함수
- `src/storage.py`
  - `get_storage_path(path: Path | None = None) -> Path`
  - `load_bookings(path: Path | None = None) -> list[dict]`
  - `save_bookings(bookings: list[dict], path: Path | None = None) -> None`
  - `create_booking(record: dict, path: Path | None = None, now: datetime | None = None) -> dict`
  - `generate_booking_id(bookings: list[dict]) -> str`
  - `atomic_write_json(path: Path, payload: list[dict]) -> None`
- `src/agent.py`
  - `_load_appointments_from_disk()` 제거 또는 storage wrapper로 대체
  - confirmation 완료 분기에서 `storage.create_booking(...)` 호출 추가
  - batch의 `book_appointment` 성공 시에도 동일한 persist 경로 사용

#### 작성할 테스트
- `tests/test_storage.py`
  - 예약 생성 후 `data/bookings.json`에 새 레코드가 추가되는지
  - 생성 직후 다시 load하면 동일 레코드가 조회되는지
- `tests/test_dialogue.py`
  - chat confirmation에서 “네” 후 success 전에 storage.create_booking이 호출되는지
- `tests/test_batch.py`
  - batch `book_appointment` 성공 시에도 storage 계층을 통해 persist 되는지

#### 회귀 위험
- **F-013**: 현재 2단계 확인 흐름은 메모리 상태만 바꾸는데, 저장이 들어오면 confirm 후 실패 처리 분기가 추가됨
- **F-021/F-022**: chat/run 공통 core는 유지되어야 하며 adapter에 저장 로직이 새면 안 됨
- **F-024**: 저장 성공/실패가 reasoning/confidence에 반영되도록 바뀌어야 함

---

### F-037 예약 변경/취소가 영속 JSON 저장소에 반영된다

#### 현재 갭
- `modify_appointment`, `cancel_appointment`는 정책 판정과 성공 응답만 있고 실제 파일 갱신이 없다.
- 취소/변경 이후 `check_appointment`에서 최신 상태를 보장할 수 없다.

#### 수정/생성 파일
- **수정**: `src/agent.py`
- **구현**: `src/storage.py`
- **수정**: `tests/test_storage.py`
- **수정**: `tests/test_dialogue.py`
- **수정**: `tests/test_policy.py` (storage truth source 흐름 연동 케이스 보강)

#### 추가/변경 함수
- `src/storage.py`
  - `update_booking(booking_id: str, changes: dict, path: Path | None = None, now: datetime | None = None) -> dict`
  - `cancel_booking(booking_id: str, path: Path | None = None, now: datetime | None = None) -> dict`
  - `mark_booking_cancelled(record: dict, now: datetime) -> dict`
- `src/agent.py`
  - modify 성공 시 storage update 수행
  - cancel 성공 시 storage cancel 수행
  - check는 active/cancelled 상태를 고려해 응답 생성

#### 작성할 테스트
- `tests/test_storage.py`
  - modify 후 `booking_time`, `department`, `updated_at` 반영 확인
  - cancel 후 `status='cancelled'`, `cancelled_at` 반영 확인
  - cancel된 예약이 조회 필터에서 제외되는지
- `tests/test_dialogue.py`
  - 여러 후보 중 하나 선택 후 취소/변경 시 올바른 id가 반영되는지

#### 회귀 위험
- **F-014**: 후보 선택 후 잘못된 예약이 수정/취소되지 않도록 id 기반 update 필요
- **F-018**과 강하게 연결됨: context 힌트만 믿고 수정하는 흐름을 남기면 회귀 발생

---

### F-038 chat.py와 run.py가 동일한 파일 기반 저장소 계층을 공유한다

#### 현재 갭
- 현재는 `src/agent.py` 내부 helper가 `data/appointments.json`을 직접 읽는다.
- chat/run 모두 간접적으로 같은 helper를 쓰고는 있지만, **공식 storage 모듈 경유 구조가 아니다**.
- AGENTS.md는 진실원천을 `data/bookings.json`으로 고정하고 있다.

#### 수정/생성 파일
- **수정**: `src/agent.py`
- **구현**: `src/storage.py`
- **검토**: `chat.py`, `run.py`
- **수정**: `tests/test_batch.py`
- **수정**: `tests/test_storage.py`

#### 추가/변경 함수
- `src/storage.py`
  - `find_customer_bookings(customer_name: str, *, status: str = 'active', path: Path | None = None) -> list[dict]`
  - `find_matching_bookings(customer_name: str, filters: dict, *, path: Path | None = None) -> list[dict]`
- `src/agent.py`
  - create_session 초기 데이터 로드를 storage 모듈로 이관
  - process_ticket/process_message에서 전달받은 `all_appointments`를 storage API 결과로 통합

#### 작성할 테스트
- `tests/test_storage.py`
  - chat와 run이 동일 storage path를 사용한다는 점을 monkeypatch/path fixture로 검증
- `tests/test_batch.py`
  - run_batch가 storage 모듈을 직접 쓰지 않고 `process_ticket -> storage` 경로만 쓰는지 검증

#### 회귀 위험
- **F-021/F-022**: adapter는 그대로 얇게 유지해야 하며, 저장소 로직이 chat/run 각각에 중복되면 회귀
- **F-025**: golden eval 재현성 확보를 위해 테스트용 path 주입이 가능해야 함

---

### F-039 파일 저장소 갱신 실패 시 거짓 성공 없이 안전하게 복구한다

#### 현재 갭
- 저장을 아예 하지 않으므로, 현재 구조는 “성공 응답만 있고 실제 반영은 없는” 상태다.
- storage 예외 처리 정책이 아직 없다.

#### 수정/생성 파일
- **구현**: `src/storage.py`
- **수정**: `src/agent.py`
- **수정**: `src/response_builder.py` (실패 안내 문구가 필요하면)
- **수정**: `tests/test_storage.py`
- **수정**: `tests/test_batch.py`

#### 추가/변경 함수
- `src/storage.py`
  - `StorageError` 예외 정의
  - JSON decode/write/permission 오류 표준화
- `src/agent.py`
  - persist 단계 try/except 추가
  - storage 실패 시 `reject` 또는 `clarify`로 안전 복구
  - reasoning에 저장 실패 근거 반영
  - booking 성공 메시지는 storage 성공 이후에만 생성

#### 작성할 테스트
- `tests/test_storage.py`
  - 깨진 JSON 파일 로드 시 안전 폴백
  - write permission 오류 시 success message 금지
  - 동시 갱신/atomic replace 실패 시 거짓 성공 금지
- `tests/test_batch.py`
  - batch 결과 JSON reasoning에 저장 실패 사유가 포함되는지

#### 회귀 위험
- **F-024**: 오류 시 confidence를 낮춰야 함
- **F-023**: 실패해도 배치 출력 키는 유지돼야 함
- **F-026**: LLM 오류와 storage 오류가 동시에 있어도 unsafe 허용 없이 끝나야 함

---

## 6. Phase 2 — Safety 완결

### F-004 의료+예약 혼합 요청을 안전하게 분리 처리한다

#### 현재 갭
- 현재 `src/classifier.py:safety_check()`는 `MEDICAL_ADVICE_PATTERNS`가 잡히면 곧바로 `medical_advice`를 반환한다.
- `src/agent.py`는 `medical_advice`이면 즉시 전체 `reject`한다.
- 따라서 “의료 질문 + 예약 요청” 혼합 문장에서 예약 부분만 이어받는 계약이 없다.

#### 수정/생성 파일
- **수정**: `src/classifier.py`
- **수정**: `src/agent.py`
- **수정 가능**: `src/models.py` (safety/intention 계약 명시 시)
- **수정**: `tests/test_safety.py`
- **수정**: `tests/test_generalization.py`

#### 추가/변경 함수
- `src/classifier.py`
  - `extract_booking_subrequest(text: str) -> str | None`
  - `split_mixed_medical_and_booking_request(text: str) -> dict`
  - `safety_check()` 반환 필드 확장
    - `contains_booking_subrequest`
    - `safe_booking_text`
    - `fallback_action`
    - `fallback_message`
- `src/agent.py`
  - safety_result가 `medical_advice`이면서 `contains_booking_subrequest=True`인 경우,
    의료 부분 거부 문구 + `safe_booking_text`를 downstream classification으로 재주입
  - safe subrequest 추출 실패 시 전체 reject 유지

#### 작성할 테스트
- `tests/test_safety.py`
  - “이 약 먹어도 되나요? 그리고 내일 내과 예약하고 싶어요” → 의료 조언은 거부하지만 예약 흐름은 이어지는지
  - “무슨 병인가요? 내일 2시요” 같은 모호 혼합 요청은 분리 실패 시 reject 유지하는지
- `tests/test_generalization.py`
  - 한국어 변형 혼합 문장, 문장 순서 반전, 접속사 없는 혼합 요청 케이스

#### 회귀 위험
- **F-001**: 의료 상담을 예약 요청으로 잘못 통과시키면 치명적 회귀
- **F-008**: 증상 기반 분과 안내와 의료 조언 차단의 경계가 더 복잡해짐
- **F-003**: 응급 표현이 있는 혼합 요청은 분리보다 escalate가 우선되어야 함

---

## 7. Phase 6 — Persistence 연결 / 저장소 진실원천 검증

### F-018 modify/cancel/check는 실제 예약 저장소 상태를 진실원천으로 검증한다

#### 현재 갭
- `_resolve_existing_appointment_from_ticket()`와 `_find_customer_appointments()`는 메모리 리스트와 `ticket.context` 힌트 기반이다.
- 실제 영속 저장소를 authoritative source로 사용하는 구조가 아니다.
- AGENTS.md의 “`ticket.context`는 보조 힌트일 뿐, 저장소가 최종 판정 기준”을 만족하지 못한다.

#### 수정/생성 파일
- **수정**: `src/agent.py`
- **구현**: `src/storage.py`
- **검토/보강**: `src/policy.py`
- **수정**: `tests/test_policy.py`
- **수정**: `tests/test_storage.py`
- **수정**: `tests/test_dialogue.py`

#### 추가/변경 함수
- `src/storage.py`
  - `find_customer_bookings(...)`
  - `find_matching_bookings(...)`
  - `get_booking_by_id(...)`
- `src/agent.py`
  - `_resolve_existing_appointment_from_ticket()`를 storage 조회 기반으로 변경
  - `_find_customer_appointments()` 제거 또는 storage wrapper화
  - pending candidate 목록도 storage 조회 결과만 사용
- `src/policy.py`
  - existing_appointment 부재/복수 후보 처리 계약은 유지하되, 입력은 storage가 만든 결과만 받게 정리

#### 작성할 테스트
- `tests/test_policy.py`
  - `context.has_existing_appointment=True`여도 storage에 없으면 `clarify`
  - 동일 고객 다수 active booking이면 `clarify`
- `tests/test_storage.py`
  - `find_matching_bookings`가 분과/날짜/시간 힌트로 후보를 좁히는지
- `tests/test_dialogue.py`
  - 후보 선택 후 storage id 기준으로 정확히 resolve 되는지

#### 회귀 위험
- **F-014**: 다수 예약 모호성 처리 흐름과 직접 연결됨
- **F-016**: 찾은 existing booking 시간이 달라지면 24시간 규칙 결과도 달라질 수 있음
- **F-023**: batch action/classified_intent가 기존보다 더 자주 `clarify`로 바뀔 수 있음. 이는 정책상 올바른 변화인지 확인 필요

---

## 8. Phase 8 — Q4 cal.com 구현

Q4는 선택 과제지만, 현재 `passes:false` 기능이 가장 많이 몰려 있다. 반드시 **storage 기반 persist 이후** 붙인다.

### F-028 Q4를 위한 cal.com 환경설정을 로드한다

#### 현재 갭
- `src/calcom_client.py`가 비어 있음
- `.env` / 환경변수 로드, graceful skip 로직 없음

#### 수정/생성 파일
- **구현**: `src/calcom_client.py`
- **수정 가능**: `src/agent.py`
- **수정**: `tests/test_calcom.py`

#### 추가/변경 함수
- `src/calcom_client.py`
  - `load_calcom_config() -> dict`
  - `is_calcom_enabled(config: dict) -> bool`
  - 필요한 env: `CALCOM_API_KEY`, `CALCOM_USERNAME`, `CALCOM_BASE_URL`

#### 작성할 테스트
- `tests/test_calcom.py`
  - env 3개 모두 있을 때 enabled
  - 하나라도 없으면 graceful skip

#### 회귀 위험
- **F-021/F-022**: config 로드를 adapter(chat/run)가 직접 하면 안 됨
- **F-033**: 설정 누락을 실패가 아닌 graceful skip으로 다뤄야 함

---

### F-029 과제 3개 분과를 cal.com Event Type으로 정확히 매핑한다

#### 현재 갭
- 분과↔Event Type 상수가 아직 없음

#### 수정/생성 파일
- **구현**: `src/calcom_client.py`
- **수정 가능**: `src/utils.py` 또는 `src/models.py` (공유 상수 분리 시)
- **수정**: `tests/test_calcom.py`

#### 추가/변경 함수
- `src/calcom_client.py`
  - `DEPARTMENT_EVENT_TYPE_MAP`
  - `get_event_type_slug(department: str) -> str | None`

#### 작성할 테스트
- `tests/test_calcom.py`
  - 이비인후과→`ent-consultation`
  - 내과→`internal-medicine`
  - 정형외과→`orthopedics`
  - 미지원 분과→`None`

#### 회귀 위험
- **F-007/F-009**: 분과 추정/의사 매핑이 잘못되면 Q4 이벤트 타입도 잘못 연결됨

---

### F-030 book_appointment 요청 시 cal.com available slots를 조회한다

#### 현재 갭
- API 호출 코드 자체가 없음
- header/version/query normalization도 없음

#### 수정/생성 파일
- **구현**: `src/calcom_client.py`
- **수정**: `src/agent.py`
- **수정**: `tests/test_calcom.py`

#### 추가/변경 함수
- `src/calcom_client.py`
  - `build_slots_request_params(...) -> dict`
  - `fetch_available_slots(department: str, requested_start: str, config: dict, duration_minutes: int | None = None) -> dict`
  - `normalize_slots_response(payload: dict) -> list[dict]`
- `src/agent.py`
  - policy 통과 후, `book_appointment`일 때만 slot lookup 분기 추가

#### 작성할 테스트
- `tests/test_calcom.py`
  - `GET /v2/slots` 호출 파라미터
  - `cal-api-version: 2024-09-04` 헤더 사용
  - 응답을 slot option list로 정규화

#### 회귀 위험
- **F-020**: 로컬 대체 슬롯 안내와 cal.com 슬롯 제안이 충돌하지 않도록 우선순위 정의 필요
- **F-019**: same-day 일반 예약은 policy가 먼저 막아야 하므로 cal.com을 호출하면 안 됨

---

### F-031 chat에서 가용 슬롯 제안, run에서 가용 슬롯 기반 자동 booking

#### 현재 갭
- chat는 로컬 confirmation만 있고 slot proposal state가 없음
- run은 단일 결과만 반환하며 “가장 가까운 가용 슬롯 선택” 로직 없음

#### 수정/생성 파일
- **수정**: `src/agent.py`
- **수정**: `src/response_builder.py`
- **수정**: `tests/test_dialogue.py`
- **수정**: `tests/test_batch.py`
- **수정**: `tests/test_calcom.py`

#### 추가/변경 함수
- `src/agent.py`
  - `pending_confirmation` 계약 확장 (`slot_options`, `selected_slot`)
  - chat slot 선택 state 처리
  - run 모드에서 nearest slot auto-select 분기
- `src/response_builder.py`
  - `build_slot_options_question(slot_options, ...)`
  - `build_slot_selection_confirmation(...)`

#### 작성할 테스트
- `tests/test_dialogue.py`
  - chat: slot 목록 제시 → 사용자 선택 → 최종 확인
- `tests/test_batch.py`
  - run: requested time에 가장 가까운 가용 슬롯으로 자동 booking 시도
- `tests/test_calcom.py`
  - slot 후보가 없을 때 clarify 처리

#### 회귀 위험
- **F-013**: 기존 confirmation flow와 slot proposal flow가 충돌 가능
- **F-023**: batch output 의미가 “요청 시간 그대로”인지 “가장 가까운 가용 슬롯”인지 reasoning에 명확히 남겨야 함

---

### F-032 사용자 확인 후 실제 cal.com booking을 생성한다

#### 현재 갭
- booking API 호출 코드 자체가 없음
- UTC 변환, header version, local storage sync가 없다

#### 수정/생성 파일
- **구현**: `src/calcom_client.py`
- **수정**: `src/agent.py`
- **수정**: `src/storage.py`
- **수정**: `tests/test_calcom.py`
- **수정**: `tests/test_dialogue.py`

#### 추가/변경 함수
- `src/calcom_client.py`
  - `build_booking_payload(ticket: dict, slot: dict, config: dict) -> dict`
  - `create_calcom_booking(slot: dict, ticket: dict, config: dict) -> dict`
  - `normalize_booking_response(payload: dict) -> dict`
- `src/agent.py`
  - chat confirm 이후 cal.com booking → local persist 순서 제어
  - run auto-booking 분기 연결
- `src/storage.py`
  - local record에 `source='calcom'`, `external_booking_id` 저장 지원

#### 작성할 테스트
- `tests/test_calcom.py`
  - `POST /v2/bookings` 호출 시 `cal-api-version: 2024-08-13` 헤더 검증
  - start 값 UTC 변환 검증
  - 성공 시 storage sync 호출 검증
- `tests/test_dialogue.py`
  - chat confirm 후 booking 성공 시에만 “완료” 응답 생성되는지

#### 회귀 위험
- **F-036**: local-only booking과 cal.com booking의 저장 스키마를 호환되게 맞춰야 함
- **F-039**: cal.com 성공 후 local sync 실패 시 처리 전략 필요

---

### F-033 cal.com API 실패 시 거짓 성공 없이 안전한 폴백을 제공한다

#### 현재 갭
- 외부 실패 처리 계층이 전혀 없다.

#### 수정/생성 파일
- **구현**: `src/calcom_client.py`
- **수정**: `src/agent.py`
- **수정**: `tests/test_calcom.py`
- **수정**: `tests/test_batch.py`
- **수정**: `tests/test_generalization.py`

#### 추가/변경 함수
- `src/calcom_client.py`
  - `CalcomError` 예외 정의
  - 인증 실패/연결 실패/응답 malformed 정규화
- `src/agent.py`
  - slot lookup 실패와 booking 실패를 분리 처리
  - reasoning에 외부 실패 원인 축약 기록
  - chat는 `clarify` 또는 `escalate`, run은 안전 결과 JSON 반환

#### 작성할 테스트
- `tests/test_calcom.py`
  - connection refused
  - 401/403 인증 실패
  - slots empty
  - booking 실패
- `tests/test_batch.py`
  - 실패 시 reasoning 필드 포함 여부
- `tests/test_generalization.py`
  - 외부 실패 + 애매한 요청 조합 케이스

#### 회귀 위험
- **F-024**: external failure는 confidence 하락 요인으로 반영 필요
- **F-026**와 유사하게 unsafe 허용 없이 종료되어야 함

---

### F-034 chat.py와 run.py가 동일한 cal.com 연동 Agent 로직을 공유한다

#### 현재 갭
- 아직 cal.com 경로 자체가 없으므로 구조 보장이 필요하다.

#### 수정/생성 파일
- **수정**: `src/agent.py`
- **검토**: `chat.py`, `run.py`
- **수정**: `tests/test_batch.py`
- **수정**: `tests/test_calcom.py`

#### 추가/변경 함수
- `src/agent.py`
  - `orchestrate_booking_integration(...)` 또는 유사 helper 추가
  - chat/run 차이는 session 여부만 두고, cal.com 호출은 모두 agent 내부로 통합

#### 작성할 테스트
- `tests/test_batch.py`
  - run.py가 cal.com 직접 호출하지 않는지
- `tests/test_calcom.py`
  - chat와 run이 동일 client helper를 쓰는지 monkeypatch로 검증

#### 회귀 위험
- **F-021/F-022**: adapter에 조건분기 로직이 생기면 shared core 원칙 위반

---

## 9. Phase 9 — 제출 문서 마감

### F-027 `final_report.md`가 필수 제출 항목을 모두 포함한다

#### 현재 갭
- `docs/final_report.md`는 제목과 목차만 있고, 실질 본문이 전무하다.

#### 수정/생성 파일
- **수정**: `docs/final_report.md`
- **참조**: `docs/q1_metric_rubric.md`, `docs/q3_safety.md`, `docs/demo_evidence.md`, `docs/architecture.md`

#### 추가/변경 함수
- 코드 함수는 아님. 문서 섹션 작성 필요
  - Executive Summary
  - Q1 Metric Rubric 요약/링크
  - Q2 Architecture & Design Decisions
  - Demo Evidence 3개 시나리오
  - Q3 Safety Response Plan 요약
  - AI Tools / Harness Disclosure
  - Q4 Summary(선택)

#### 작성할 테스트
- 자동 테스트보다는 문서 체크리스트 검수
- 필요 시 수동 검토 체크리스트 문서화

#### 회귀 위험
- 코드 회귀는 거의 없지만, 문서와 실제 구현 상태가 어긋나면 제출 리스크 큼

---

### F-035 Q4 외부 준비사항과 Event Type 생성 체크리스트가 문서에 반영된다

#### 현재 갭
- cal.com 준비 절차, 환경변수, 이벤트 타입 slug, API version 헤더 주의사항이 문서화되어 있지 않다.

#### 수정/생성 파일
- **수정**: `docs/final_report.md`
- **수정 가능**: `README.md`
- **수정 가능**: `docs/demo_evidence.md`

#### 추가/변경 항목
- 문서 섹션 추가
  - cal.com 가입
  - `CALCOM_USERNAME` 확인
  - API key 발급
  - Event Type 3개 slug 정확 일치
  - slots/booking API version 헤더
  - 캘린더 스크린샷 확보 절차

#### 작성할 테스트
- 문서 체크리스트 검수

#### 회귀 위험
- 구현 회귀보다 운영/데모 실패 리스크가 큼. 문서 누락 시 Q4를 구현해도 재현이 어려움

---

## 10. 파일별 우선 수정 목록

### 최우선
1. `src/storage.py` — 현재 비어 있음, Phase 1 핵심
2. `src/agent.py` — direct file access 제거, persist/integration orchestration 추가
3. `tests/test_storage.py` — 현재 비어 있음, storage 기능 검증 필요

### 그다음
4. `src/classifier.py` — mixed request 분리 계약(F-004)
5. `tests/test_safety.py`, `tests/test_generalization.py` — 혼합 요청 회귀 방지

### Q4 착수 시
6. `src/calcom_client.py` — 현재 비어 있음
7. `tests/test_calcom.py` — 현재 비어 있음
8. `tests/test_batch.py` — 현재 비어 있음, run auto-booking 및 failure reasoning 검증 필요
9. `src/response_builder.py` — slot options 문구 추가

### 문서 마감
10. `docs/final_report.md`
11. `README.md` 또는 `docs/demo_evidence.md`

---

## 11. 권장 구현 순서 (실제 착수용)

### Step 1. 저장소 스키마 확정
- 진실원천을 `data/bookings.json`으로 통일
- 기존 `data/appointments.json`은 초기 샘플/마이그레이션 입력으로만 사용할지 결정
- 최소 필드 + 운영 필드(`status`, `created_at`, `updated_at`, `cancelled_at`, `source`, `external_booking_id`) 확정

### Step 2. `src/storage.py` 구현
- load/save/find/create/update/cancel + atomic write
- path 주입 가능하게 설계하여 테스트 가능성 확보

### Step 3. `src/agent.py` persist 연결
- book/modify/cancel/check 모두 storage 경유
- success message는 persist 성공 이후에만 생성

### Step 4. mixed request(F-004) 도입
- classifier safety_result 계약 확장
- agent가 medical rejection + safe booking continuation을 지원

### Step 5. Q4 client 구현
- config → mapping → slot lookup → booking create → error normalization

### Step 6. 문서 마감
- final_report / Q4 checklist / demo evidence 정합화

---

## 12. 회귀 위험 총정리

| 영향 받는 기존 true 기능 | 회귀 포인트 |
| --- | --- |
| F-001~F-003 | mixed request 도입 중 의료/응급 차단 우선순위가 흐려질 수 있음 |
| F-012~F-014 | storage/candidate/slot state가 추가되며 대화 상태가 복잡해짐 |
| F-015~F-020 | storage truth source가 들어오며 기존 policy 입력 값이 달라질 수 있음 |
| F-021~F-024 | persist/integration 실패가 response schema, confidence, reasoning 계산에 영향을 줌 |
| F-026 | LLM 오류 + storage/cal.com 오류가 결합될 때 안전 폴백 유지 필요 |
| F-040 | storage/cal.com/slot selection 경로도 now 주입 가능해야 테스트 재현 가능 |

핵심은 다음 두 가지다.

1. **성공 응답은 실제 저장/외부 booking 성공 이후에만 말한다**  
2. **safety gate 우선순위는 어떤 신규 기능보다 앞선다**

---

## 13. 착수 우선순위 결론

다음 구현 순서를 고정한다.

1. **Phase 1 저장소 계층 도입** — F-036, F-037, F-038, F-039
2. **Phase 2 혼합 요청 안전 분리** — F-004
3. **Phase 6 저장소 진실원천 연결** — F-018
4. **Phase 8 cal.com 구현** — F-028~F-034
5. **Phase 9 문서 마감** — F-027, F-035

특히 다음이 첫 구현 단위다.

> **Phase 1 저장소 계층 도입 (F-036~F-039)**

이 단계가 끝나야 F-018과 Q4가 모두 안정적으로 이어질 수 있다.