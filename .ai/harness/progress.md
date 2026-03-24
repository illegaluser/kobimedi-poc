# Progress

## Phase 1 — Storage truth-source hardening completed (2026-03-25)
- `src/storage.py` 보강 완료
  - `create_booking()`에 최종 저장 직전 fresh recheck 훅(`availability_rechecker`)을 추가하여, 확정 직전 최신 저장소 상태 기준 재검증이 가능하도록 확장 (F-064)
  - 동일 `patient_contact` + 동일 `booking_time` + 동일 `department`의 active 예약 중복을 저장 직전 차단하는 `StorageConflictError` 추가
  - 저장소 재검증 실패/충돌 시 예외를 발생시켜 거짓 성공이 절대 반환되지 않도록 보강 (F-065)
  - `data/bookings.json` 기반의 식별/판정 원칙을 유지하면서 `patient_name`, `patient_contact`, `is_proxy_booking` 필수 기록 계약을 지속 보장 (F-061, F-066)

- `tests/test_storage.py` 보강 완료
  - 동일 환자 동일 시각 active 중복 예약 차단 검증 추가
  - 사용자 정의 fresh recheck 콜백이 예약 마감/충돌을 반환할 경우 예외가 발생하고 기존 파일이 보존되는지 검증 추가
  - 기존 전화번호 우선 조회/초진·재진 판정/쓰기 실패 회귀 테스트와 함께 storage 정책 요구사항 재검증 완료

- 검증 완료
  - `python3 -m pytest tests/test_storage.py tests/test_dialogue.py tests/test_policy.py` → **28 passed**
  - `.ai/harness/features.json`에서 `F-061 ~ F-067` `passes: true` 반영 완료

## Phase 3 — 의도 분류 및 LLM 예외 방어 구현 완료 (2026-03-25)
- `src/llm_client.py` 구현 보강 완료
  - Ollama `qwen3-coder:30b` 호출을 `format='json'`으로 강제 유지 (F-011, F-083)
  - `build_classification_fallback()` / `build_safety_fallback()` 추가로 분류/안전성 호출별 안전 기본 payload 제공
  - connection refused / timeout / invalid response / JSON parse fail 시 시스템 중단 대신 `clarify` 중심 폴백 payload 반환하도록 표준화 (F-012, F-084)
  - `ollama_response_invalid`도 안전 폴백 대상으로 확장하여 잘못된 응답 구조에서 거짓 성공을 방지

- `src/models.py` 구현 완료
  - 과제 원문 7개 action을 그대로 갖는 `Action` Enum 추가
  - classifier가 Enum 기반으로 action 값을 강제 검증하도록 공통 상수(`VALID_ACTION_VALUES`) 제공 (F-011)

- `src/prompts.py` 분류 프롬프트 보강 완료
  - 구조화 JSON 스키마에 `patient_name`, `patient_contact`, `birth_date`, `is_proxy_booking`, `is_emergency`, `symptom_keywords` 필드 추가
  - 증상→분과 매핑은 예약 안내 목적일 뿐이며 진단/질병명 단정 금지 규칙을 시스템 프롬프트에 명시 (F-029)
  - 대리 예약 표현 감지 및 필수 정보 부족 시 `clarify` 우선 규칙을 명시

- `src/classifier.py` 구현 완료
  - LLM action 값을 `Action` Enum으로 정규화하여 7개 허용 enum 외 값은 자동 폐기 후 규칙 기반/`clarify`로 폴백 (F-011)
  - 날짜/시간/분과/의사명/customer_type 추출 유지 및 확장 (F-021, F-022, F-030)
  - 자유문장에서 환자 이름/전화번호/생년월일 추출 추가 (F-023, F-024, F-025)
  - "엄마를 대신해서", "아버지를 위해" 등 패턴의 대리 예약 감지 및 `is_proxy_booking` 신호 추출 구현 (F-026)
  - 응급/급성 통증 신호, 기존 예약 식별 힌트, 증상 키워드 리스트 추출 추가 (F-027, F-028, F-029)
  - LLM 실패 시에도 rule-based 엔터티가 없으면 `error=True`, `fallback_action=clarify`를 가진 안전 결과를 반환하도록 구현 (F-012)

- `tests/test_classifier.py` 보강 완료
  - 기존 action/시간/분과 분류 회귀 테스트 유지
  - invalid enum → 안전 폴백 검증
  - JSON parse failure / connection refused / timeout 폴백 검증
  - 대리 예약 + 환자 정보 추출 검증
  - 증상 기반 분과 추천 시 진단명 비생성 검증

- 검증 완료
  - `python -m pytest tests/test_classifier.py tests/test_safety.py` → **54 passed**
  - `python -m pytest tests/test_classifier.py tests/test_safety.py tests/test_dialogue.py` → **59 passed**
  - `.ai/harness/features.json`에서 `F-011 ~ F-014`, `F-021 ~ F-030` `passes: true` 반영 완료

## Safety gate implementation completed (2026-03-25)
- `src/classifier.py` 안전 게이트 보강 완료
  - safety gate가 classification/policy 이전에 항상 선행되도록 파이프라인 계약 유지 (F-001)
  - 의료 상담, 목적 외 사용, 프롬프트 인젝션, 타 환자 개인정보 요청 즉시 차단 규칙 보강 (F-002 ~ F-005)
  - 의료+예약 혼합 요청에서 안전한 예약 하위 요청만 분리 추출하고, 불명확한 결합 요청은 전체 reject 처리 구현 (F-006)
  - 증상 기반 분과 안내는 허용하되 진단/치료로 이어지지 않도록 안전 분기 유지 (F-007)
  - 급성 통증/출혈/호흡곤란, 상담원 요청/반복 불만, 보험·비용 문의 및 의사 개인 연락처 요청을 즉시 escalate하도록 규칙 확장 (F-008 ~ F-010)
  - 예약 진행 중 이름/생년월일/전화번호 등 후속 응답은 안전 게이트에서 오탐 차단되지 않도록 예외 처리 추가

- `src/agent.py` safety-first 응답 분기 보강 완료
  - `privacy_request` → `reject`
  - `operational_escalation` → `escalate`
  - reasoning에 신규 safety 카테고리 근거 반영

- `tests/test_safety.py` 회귀 테스트 보강 완료
  - safety gate 선행 실행으로 `classify_intent` / `apply_policy` 미호출 검증
  - 타 환자 정보 요청 reject 검증
  - 상담원 요청/반복 불만 escalate 검증
  - 보험·비용 및 의사 연락처 문의 escalate 검증
  - 혼합 요청 분리/전면 reject, 증상 기반 분과 안내, 응급 escalate 경로 검증

- 검증 완료
  - `python -m pytest tests/test_safety.py tests/test_classifier.py tests/test_dialogue.py` → **56 passed**
  - `.ai/harness/features.json`에서 `F-001 ~ F-010` `passes: true` 반영 완료

## Storage implementation completed (2026-03-25)
- `src/storage.py` 구현 완료
  - `data/bookings.json`을 진실원천으로 유지하는 영속 저장소 계층 정비
  - `find_bookings(..., patient_contact=...)` 추가로 전화번호 우선 조회 지원 (F-034)
  - `resolve_customer_type_from_history(..., patient_contact=...)` 추가로 전화번호 기반 초진/재진 판정 지원 (F-035)
  - `create_booking()`에서 `patient_name`, `patient_contact`, `is_proxy_booking`, `booking_time`, `department`, `customer_type`, `status`, `id` 필수 보장 (F-038)
  - 임시 파일 기록 후 rename하는 원자적 쓰기 및 `StorageDecodeError` / `StorageWriteError` / `StorageValidationError` 명시적 예외 추가 (F-039)
  - `cancel_booking()` 추가로 취소 상태 영속 반영 가능

- `tests/test_storage.py` 보강 완료
  - 생성 시 필수 필드 저장 검증
  - 전화번호 기반 조회 검증
  - 전화번호 우선 초진/재진 판정 검증
  - 동명이인 + 생년월일 clarify 경로 검증
  - 취소 반영 검증
  - 파일 손상(JSON decode error) 및 쓰기 실패 시 폴백 검증

- 검증 완료
  - `python -m pytest` → **72 passed**
  - features.json에서 `F-034 ~ F-039` `passes: true` 반영 완료

## Current Status

- Safety gate / classifier hardening 완료
  - `src/classifier.py`의 `safety_check()`가 classification 전에 선행되며, 의료 상담/목적 외 사용/인젝션/타 환자 정보 요청 reject 및 응급/민원/운영 문의 escalate 규칙을 반영
  - 의료+예약 혼합 요청 분리, 증상 기반 분과 추천(진단 금지), 후속 신원 응답 예외 처리까지 테스트로 검증 완료
  - safety 관련 feature(`F-001 ~ F-010`)와 classifier/extraction 관련 feature(`F-011 ~ F-030`) 상태를 harness에 반영 완료

- Next step
  - `src/agent.py` 대화 상태에 `is_proxy_booking`, `patient_name`, `patient_contact`를 본격 반영하여 본인/대리인 확인 및 세션 중간 상태 수집 흐름을 구현
  - confirmation 직전 storage fresh recheck를 agent 예약 확정 흐름과 직접 연결
  - dialogue / patient identity 중심의 후속 Phase 구현을 계속 진행

### Documentation update completed (2026-03-25)
- `docs/policy_digest.md` 업데이트 완료
  - §5.7 **본인/대리인 확인 정책** 신규 추가
    - 성명+전화번호만으로는 본인/대리인 구분 불가 → 예약 의도 확정 직후 명시적 질문 필수
    - chat 모드: 예약 의도 확정 후 첫 clarify 턴으로 본인/대리인 확인
    - batch 모드: 대리 표현 감지 시 is_proxy_booking=true, 없으면 본인으로 간주
    - is_proxy_booking 플래그는 tickets.json에 기록하지 않음
  - §6.5 **본인/대리인 판단 불가 시 처리** 신규 추가
    - 명시적 대리 표현 없으면 본인으로 간주
    - 판단 불가 시 clarify 질문
  - §5.6 환자 식별 기본 정책 유지 (전화번호 우선 수집)
  - §7.3 초진/재진 판정 규칙: 전화번호 기반 조회 명시

- `.ai/harness/features.json` 전면 작성 완료 (v2.1, 이전 빈 파일 대체)
  - 11개 그룹, 60개 feature 정의
  - **patient_identity 그룹 신규** (F-031 ~ F-039):
    - F-031: 예약 시작 시 본인/대리인 여부 확인 ★ (핵심 신규)
    - F-032: 본인 예약 시 본인 성명+전화번호 수집
    - F-033: 대리 예약 시 환자 본인 성명+전화번호 수집
    - F-034: 전화번호 우선 환자 식별
    - F-035: 초진/재진 저장소 판정
    - F-036: 동명이인 → 생년월일 clarify
    - F-037: ticket_id correlation key 전용
    - F-038: bookings.json에 patient_contact 포함
    - F-039: 세션 중간 상태로 환자 정보 관리
  - storage 그룹: F-062 (find_bookings patient_contact 필터), F-063 (resolve 전화번호 우선) 명시
  - dialogue 그룹: F-043에 is_proxy_booking 슬롯 포함

- `.ai/handoff/10_plan.md` 신규 작성 완료
  - 3가지 핵심 문제 정의 (신원확인, 본인/대리인 구분, 멀티턴 상태관리)
  - 파이프라인 순서 명시 (safety → classification → extraction → dialogue state merge → storage → policy → cal.com → persist → response)
  - Phase 0 (환자 식별 기반 확보) 최우선 신규 작업으로 추가
    - Phase 0a: storage.py 확장 (patient_contact 파라미터)
    - Phase 0b: dialogue state 확장 (is_proxy_booking 슬롯)
    - Phase 0c: extraction 확장 (전화번호 추출, 대리 감지 패턴)
  - storage.py 변경 사양 상세 (find_bookings, resolve_customer_type_from_history)
  - dialogue state 추가 필드 사양
  - batch vs chat 처리 원칙 비교표

---

## 핵심 설계 결정: 본인/대리인 구분

### 문제
성명과 전화번호만으로는 예약을 진행하는 사람이 환자 본인인지 대리인인지 알 수 없다.

### 결정
1. 예약 의도 확정 직후, 환자 정보 수집 전에 **명시적 본인/대리인 확인 질문** 수행
2. `is_proxy_booking` 플래그를 dialogue state에 추가
3. 분기:
   - 본인 → 본인 성명+전화번호 수집
   - 대리인 → 환자 본인 성명+전화번호 수집 (대리인 정보는 수집 안 함)
4. batch 모드에서는 메시지 패턴으로 감지. 감지 불가 시 본인으로 간주

### 영향 범위
- `src/agent.py`: dialogue state에 is_proxy_booking, patient_name, patient_contact 슬롯 추가
- `src/storage.py`: find_bookings(), resolve_customer_type_from_history()에 patient_contact 파라미터 추가
- `src/prompts.py` (있다면): 전화번호 추출, 대리 감지 패턴 추가

---

## 핵심 설계 결정: 전화번호 기반 환자 식별

### 문제
`tickets.json`에 전화번호 없음 → 성명만으로 동명이인 식별 불가, 초진/재진 판정 불확실

### 결정
1. 대화에서 전화번호를 수집해 세션 상태(`patient_contact`)에 저장
2. `bookings.json` 레코드에 `patient_contact` 필드 포함
3. 초진/재진 판정은 전화번호 우선으로 `data/bookings.json` 조회
4. 전화번호 없으면 이름+생년월일로 폴백, 그것도 없으면 이름 단독(유일한 경우만)

### tickets.json 스키마는 변경하지 않음
- tickets.json은 입력 계약 불변 원칙(policy_digest §0.2) 유지
- 추가 정보는 대화에서 수집하거나 bookings.json에서 조회

---

## Reusable Implementation Baseline

### Still Reusable (변경 불필요)
- safety gate 선행 실행 구조
- action enum validation (7개)
- doctor/department/symptom 기본 추론
- confirmation 단계 분리 구조
- batch/chat 공통 agent core 구조
- policy.py 기본 구조 (24시간, 정원, 슬롯 겹침)

### Must Be Implemented / Reopened

#### Phase 0 (신규 — 최우선)
- ✅ `storage.find_bookings()`: patient_contact 파라미터 추가 완료
- ✅ `storage.resolve_customer_type_from_history()`: patient_contact 우선 조회 확장 완료
- dialogue state: is_proxy_booking, patient_name, patient_contact 슬롯 추가
- chat 모드: 예약 의도 확정 후 본인/대리인 확인 질문 생성
- extraction: 전화번호 패턴 추출, 대리 예약 감지 패턴 강화

#### Phase 1 (재오픈)
- pending_missing_info_queue → 우선순위: is_proxy_booking → patient_contact → dept/date/time → birth_date
- clarify_turn_count 4단계 상한
- 누적 슬롯 유지 및 매 턴 재평가

#### Phase 2 (재오픈)
- 운영시간/점심시간/휴진일 정책 완전 반영
- 슬롯 불가 시 대체안 제시

#### Phase 3 (재오픈)
- 보험/비용 문의 escalate
- 의사 개인정보 요청 escalate
- 타 환자 정보 요청 reject

#### Phase 4 (재오픈)
- confirmation 직전 fresh storage recheck
- 저장 실패 시 거짓 성공 금지

#### Phase 5 (신규)
- KPI/constraint 이벤트 계측
- 문서 산출물 완성

---

## New Priorities

### Priority 1. Hard Fail 방지
- 의료 상담 오답 0건 유지 (Unsafe Medical Answer Rate = 0%)
- 허위 성공/잘못된 예약 확정/개인정보 노출 방지

### Priority 2. 불필요한 Escalation 축소
- 본인/대리인 → 환자 정보 → 예약 정보 → 슬롯 불가 → 대체안 순서로 clarify 처리
- 사람이 꼭 필요한 케이스만 escalate

### Priority 3. 성공 자동화율 향상
- 멀티턴 clarify 안정화로 recoverable case 자동 처리

---

## Known Issues (Current Codebase)

- dialogue state: is_proxy_booking 슬롯 없음 → 본인/대리인 구분 불가
- chat 모드: 본인/대리인 확인 질문 생성 로직 없음
- clarify 멀티턴: 정책상 요구하는 4단계 누적 상태머신 구현 부족
- 운영시간/점심시간/휴진일: policy.py에 완전 반영 안 됨
- KPI/constraint 이벤트 런타임 집계 없음

---

## Submission Readiness

| 산출물 | 상태 |
| --- | --- |
| `docs/policy_digest.md` | ✅ 업데이트 완료 (본인/대리인 정책 포함) |
| `.ai/harness/features.json` | ✅ 신규 작성 완료 |
| `.ai/handoff/10_plan.md` | ✅ 신규 작성 완료 |
| `.ai/harness/progress.md` | ✅ 업데이트 완료 |
| `src/storage.py` | ✅ Phase 0a 구현 완료 |
| `tests/test_storage.py` | ✅ 저장소 회귀 테스트 보강 완료 |
| `docs/q1_metric_rubric.md` | ⬜ 미작성 |
| `docs/q3_safety.md` | ⬜ 미작성 |
| `docs/final_report.md` | ⬜ 미업데이트 |
| 코드 구현 (Phase 0~5) | ◐ Phase 0a 완료, 이후 단계 진행 필요 |

## Practical Interpretation

현재 프로젝트 상태:

> **정책/기능/계획 문서 정렬 및 Phase 0a 저장소 구현이 완료되었습니다.  
> 다음 작업은 dialogue state / extraction 중심의 Phase 0b~1 구현입니다.**
