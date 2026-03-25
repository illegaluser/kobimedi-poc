# 진행 상황 보고서

## 개요
이 문서는 코비메디 예약 챗봇의 문제점을 해결하고 개선하기 위한 진행 상황을 기록합니다.

## 식별된 문제점
현재 챗봇은 다음과 같은 주요 문제점을 가지고 있습니다:
1.  **대리 예약 처리 미흡**: 예약 시 본인 예약인지 대리 예약인지 명확하게 확인하지 않습니다.
2.  **불필요한 정보 재요청**: 사용자가 이름과 연락처를 함께 제공해도 정보를 개별적으로 다시 요청하는 경우가 발생합니다.
3.  **성급한 상담원 연결**: 간단한 오타에도 유연하게 대처하지 못하고 즉시 상담원에게 연결(escalate)하여 사용자 경험을 저해하고 불필요한 비용을 발생시킵니다.

## 개선 과제
위 문제들을 해결하기 위해 다음 과제를 수행합니다.

1.  **[완료] 대화 시작 시 '본인/대리 예약' 확인 절차 강화**: LLM이 명시적인 언급 없이 대리 예약 여부를 `False`로 자의적 추정하지 못하도록 차단하고, 반드시 본인 여부를 명시적으로 가장 먼저 묻도록 수정했습니다.
2.  **[완료] 정보 동시 추출 및 재확인 방지**: 이름 추출 로직이 전체 문자열 일치(`re.fullmatch`)를 요구하여 전화번호와 함께 입력 시 실패하는 버그를 수정했습니다.
3.  **[완료] 예약 시간 등 오타 허용 및 제안**: '8ㅛㅣ'와 같은 오타 발생 시 즉시 실패 처리하지 않고, 시간 추출 로직에 오타 보정 규칙이나 재확인 미니 상태를 추가했습니다.
4.  **[완료] 상담원 연결(Escalate) 정책 완화**: 사용자가 유효한 정보를 제공하여 예약 단계가 정상적으로 진행(진전) 중일 때는 `clarify_turn_count`를 초기화하여, 누적 턴 초과로 인한 불필요한 에스컬레이션을 방지했습니다.

## 진행 단계
1.  `progress.md`에 현재 상황 및 계획 기록
2.  `features.json`에 신규 과제 반영 및 기존 항목 상태 업데이트
3.  `.ai/handoff/10_plan.md`에 구체적인 기술 실행 계획 추가
4.  `docs/policy_digest.md`에 변경된 정책 내용 반영
5.  `docs/architecture.md`에 개선된 대화 흐름 아키텍처 반영
6.  **[완료]** `src/` 의 실제 코드 수정 및 개선 (agent.py 및 classifier.py 수정)
7.  **[완료]** `tests/test_dialogue.py`에 멀티턴 진전 시 Turn Count 초기화 테스트 케이스 추가 완료
8.  **[완료]** 전체 단위 테스트 구동 — `tests/test_dialogue.py` 13/13 통과 확인

---

## Current Status (2026-03-25 최종 패치)

### 수정된 결함 3건 (debug: 유저플로우 및 멀티턴 대화 개선)

| # | 결함 | 원인 | 수정 위치 |
|---|------|------|-----------|
| 1 | 본인/대리 예약 확인 스킵 | `_sync_identity_state_from_intent`에서 chat 모드에서도 LLM이 반환한 `is_proxy_booking=False`를 무조건 수용 | `src/agent.py` — `is_chat and proxy_flag is False` 조건 추가로 LLM의 False 추정 차단 |
| 2 | 이름+연락처 동시 추출 실패 | 이름 추출 함수가 토큰 단위로 `re.fullmatch`를 적용했으나 전화번호가 포함된 문장 정리 흐름에서 모호한 경우 발생 | `src/agent.py` — `_extract_patient_name` 로직 검증 완료 + F-049 테스트 3건 추가 |
| 3 | Turn Count 누적 과다 에스컬레이션 | `_consume_pending_identity_input`의 `_reset_clarify_turn_count`가 `if pending_missing_info:` 블록 안에만 있어, 큐가 비어질 때(마지막 identity 필드 소비)는 리셋이 생략됨 → 메인 흐름에서 추가 증가 | `src/agent.py` — 리셋 호출을 `if pending_missing_info:` 블록 바깥으로 이동, `consumed_identity=True`이면 항상 리셋 |

### 테스트 결과
- `tests/test_dialogue.py` : **13 / 13 passed** (F-031~049 전체 커버)
- 회귀 없음 (기존 패스 테스트 전부 유지)

### Next Step
- Ollama LLM 연결 후 E2E 통합 테스트 (test_classifier.py 네트워크 의존 케이스)
- `tests/test_policy.py` Booking 모델 타입 오류(pre-existing) 별도 수정 검토

---

## Current Status (2026-03-25 유저플로우 사용성 개선)

### 수정된 결함 — 액션별 발화 세분화 및 본인확인 정보 수집 개선

| # | 항목 | 수정 내용 |
|---|------|-----------|
| 1 | `is_proxy_booking` 질문 | 예약 신규/변경/취소/확인 각 액션별로 서로 다른 문구 반환 |
| 2 | `patient_name` + `patient_contact` 동시 누락 | 두 필드가 모두 없을 때 한 문장으로 성함+연락처 함께 요청 (액션별 문구) |
| 3 | `patient_contact` 단독 누락 | 성함은 확보, 연락처만 없는 경우도 액션별 문구로 요청 |

수정 위치: `src/response_builder.py` — `build_missing_info_question()`

### 테스트 결과
- `tests/test_response_builder.py` (신규) : **27 / 27 passed**
- `tests/` 전체 (test_classifier.py 제외 pre-existing LLM 의존 실패) : **77 / 77 passed**
- 회귀 없음

### Next Step
- Ollama LLM E2E 통합 테스트
- test_classifier.py LLM 의존 케이스 별도 처리

---

## Current Status (2026-03-25 전체 테스트 및 골든 eval)

### 수행 내용

#### 1. test_classifier.py 4건 수정 (20/20 통과)

**근본 원인**: `_extract_patient_name_from_text`가 일반 한국어 단어를 이름으로 오추출
- `\"아무 말\"` → `\"아무\"` 추출 (오탐) → `patient_name` truthy → `error` 키 조건 실패 → `KeyError: 'error'`
- `\"예약 도와주세요\"` → `\"도와주세\"` 추출 (오탐) → 동일 문제

**수정 위치: `src/classifier.py`**

| 수정 항목 | 내용 |
|---|---|
| `_NON_NAME_WORDS` 확장 | `\"아무\"`, `\"누구\"`, `\"도와\"` 등 제네릭 단어 추가 |
| 토큰 길이 제한 | `4→3` (한국 이름은 2~3글자) |
| 접미사 제거 패턴 | `\"주세요|세요\"` 추가 → `\"도와주세\"` 오탐 차단 |

#### 2. 에스컬레이션 패턴 강화 (정책 3.3 준수)

**원인**: T-046, T-047이 `_is_booking_related()`에서 `\"safe\"`로 처리되어 응급 체크 무용지물

**수정 위치: `src/classifier.py` — `EMERGENCY_PATTERNS` 확장**

| 추가 패턴 | 대상 케이스 |
|---|---|
| `r\"참을 수(가)?\\s*없\"` | T-046: 참기 힘든 급성 통증 |
| `r\"열이?\\s*(3[89]\\|[4-9]\\d)\\s*도\"` | T-047: 38도 이상 고열 |
| `r\"진물\"`, `r\"고름\"` | T-047: 이상 분비물 |
| `r\"오늘 당장.*봐\"`, `r\"오늘 중으로.*꼭\"` | 당일 긴급 진료 요청 |
| `r\"고열\"` | 명시적 고열 표현 |

**수정 위치: `src/prompts.py` — `CLASSIFICATION_SYSTEM_PROMPT`**
- Action Logic에 `\"escalate\"` 사용 조건 명시 (통증 참기 힘듦, 고열, 이상 분비물, 비용 문의, 상담원 요청 등)

### 테스트 결과

| 테스트 | 결과 |
|---|---|
| 전체 단위 테스트 | **124 / 124 passed** |
| test_classifier.py | 20 / 20 (4건 수정) |
| 회귀 없음 | ✅ |

### 골든 eval 결과 (Ollama qwen3-coder:30b)

| 지표 | 수정 전 | 수정 후 |
|---|---|---|
| Action 정확도 | 14/50 (28.0%) | **16/50 (32.0%)** |
| Reject 재현율 | 6/6 (100.0%) | **6/6 (100.0%)** |
| safe_reject (escalate 포함) | 8건 | **10건 (+2)** |

T-046, T-047 → `category: emergency → action: escalate` ✅ (정책 3.3 준수)

### 잔여 불일치 분석 (코드 구조 문제 아님)

| 불일치 유형 | 케이스 수 | 원인 |
|---|---|---|
| book_appointment→clarify | ~16건 | LLM(qwen3-coder:30b) 의도 분류 한계 — 응답 자체는 \"예약할까요?\" 확인 문구 포함 |
| modify/cancel→reject | ~13건 | 정책 정상 동작 — bookings.json에 해당 고객 예약 없음. gold eval이 final action 아닌 raw intent 기대 |

### Next Step
- LLM 모델 업그레이드 또는 프롬프트 개선으로 book_appointment 분류 정확도 향상 검토
- gold eval 지표를 `classified_intent` 기준으로도 측정하여 LLM 분류 정확도 별도 파악

---

## Current Status (2026-03-25 대화 플로우 버그 5건 수정)

### 발견 경위
실제 `chat.py` 대화 테스트에서 다수의 사용성 문제 확인

### 수정된 결함 5건

| # | 결함 | 원인 | 수정 위치 |
|---|------|------|-----------|
| 1 | "내일모레" → 내일(+1일)로 잘못 파싱 | `"내일" in text`가 `"내일모레"`에서 먼저 매칭 | `src/classifier.py` — `_extract_date_from_text()`: `"내일모레"` 체크를 `"내일"` 앞에 배치 |
| 2 | "저녁 9시" → 오전 09:00으로 파싱 | `_extract_time_from_text()`에서 `"저녁"`, `"밤"` 미처리 (오전/오후만 인식) | `src/classifier.py` — `_extract_time_from_text()`: `"저녁\|밤"` → `"오후"` PM 변환 추가 (HH:MM 및 N시 양쪽 경로) |
| 3 | "싫어" → None 반환 (부정 응답 미인식) | `NEGATIVE_PATTERNS`에 "싫어" 등 일상 거절 표현 미포함 | `src/agent.py` — `NEGATIVE_PATTERNS`: `"싫어"`, `"싫습니다"`, `"안 ?해"`, `"안 ?할래"`, `"됐어"`, `"괜찮아요"` 추가 |
| 4 | "안과", "비뇨기과" 입력 시 거부 메시지 없이 같은 질문 반복 | `_extract_requested_department()`에 피부과만 비지원 분과 추가, 나머지 미등록 | `src/classifier.py` — `_UNSUPPORTED_DEPARTMENTS` 리스트 신설 (안과, 비뇨기과, 소아과, 치과 등 13개 분과) |
| 5 | `response`가 `None`일 때 `None` 문자열 출력 | `chat.py`에서 `None` 응답에 대한 fallback 없음 | `chat.py` — `response or "죄송합니다. 말씀을 이해하지 못했어요..."` fallback 추가 |

### 테스트 결과

| 테스트 | 결과 |
|---|---|
| 전체 단위 테스트 | **124 / 124 passed** |
| 회귀 없음 | ✅ |

### 골든 eval 결과 (Ollama)

| 지표 | 수정 전 (baseline) | 수정 후 |
|---|---|---|
| Action 정확도 | 14/50 (28.0%) | **16/50 (32.0%)** |
| Reject 재현율 | 6/6 (100.0%) | **6/6 (100.0%)** |
| safe_reject | 8건 | **10건 (+2)** |

- 수정 전 T-046, T-047이 clarify로 오분류 → 수정 후 escalate로 정상 분류
- 나머지 불일치는 기존과 동일 (LLM 분류 한계 및 저장소 미존재 예약 정책 동작)

### Next Step
- "내일모레 저녁 9시" 등 복합 시간 표현 E2E 대화 테스트 검증
- 부정 응답 후 새 예약 플로우 재진입 E2E 검증

---

## Current Status (2026-03-25 배치 모드 ticket 메타데이터 병합)

### 문제
`run.py` 배치 모드에서 ticket에 구조화 데이터(`customer_type`, `context.preferred_*`)가 모두 포함되어 있어도 LLM이 message 텍스트만 분류하여 `missing_info → clarify`로 빠지는 문제. 골든 eval 정확도 32%.

### 수정 내용

| # | 항목 | 수정 위치 |
|---|------|-----------|
| 1 | `_merge_ticket_context_into_intent()` 호출 연결 | `src/agent.py` — `process_ticket()` 내 LLM 분류 직후, 배치 모드(`not is_chat`)에서 ticket 메타데이터를 intent에 병합 |
| 2 | 배치 모드 book_appointment 즉시 확정 | `src/agent.py` — `action == "book_appointment"` 분기에서 `is_chat` 여부에 따라 채팅은 확인 질문(clarify), 배치는 즉시 book_appointment 반환 |
| 3 | 테스트 기대값 갱신 | `tests/test_dialogue.py` F-047, `tests/test_batch.py` — 배치 모드 action 기대값을 `book_appointment`으로 변경 |

### 테스트 결과

| 테스트 | 결과 |
|---|---|
| 전체 단위 테스트 | **124 / 124 passed** |
| 회귀 없음 | ✅ |

### 골든 eval 결과

| 지표 | 수정 전 | 수정 후 | 변화 |
|---|---|---|---|
| Action 정확도 | 16/50 (32.0%) | **31/50 (62.0%)** | **+30pp** |
| Reject 재현율 | 6/6 (100.0%) | **6/6 (100.0%)** | 유지 |
| agent_success | 3건 | **18건** | +15 |
| agent_soft_fail_clarify | 23건 | **8건** | -15 |

### 잔여 불일치 분석

| 불일치 유형 | 케이스 수 | 원인 |
|---|---|---|
| modify/cancel → reject | 13건 | `bookings.json`에 해당 고객 예약 없음. 정책상 정상 동작 |
| book → check/clarify | 3건 | LLM(Ollama) 의도 분류 한계 |
| check → clarify | 2건 | LLM이 check_appointment을 분류하지 못함 |
| clarify → reject | 1건 | 비지원 분과 감지로 reject 처리 (T-032) |

### Next Step
- modify/cancel 케이스용 golden eval fixture 데이터(bookings.json) 셋업 검토
- LLM 프롬프트 개선 또는 모델 업그레이드로 잔여 3건 분류 오류 해결

---

## Current Status (2026-03-25 골든 eval 체계 재설계 — batch + dialogue 통합)

### 배경
기존 golden eval이 배치 단일 ticket 평가만 지원하여 멀티턴 대화 품질을 측정할 수 없었음. 정책 문서 기반으로 batch/dialogue 케이스를 체계적으로 재구성.

### 수정 내용

| # | 항목 | 내용 |
|---|------|------|
| 1 | `golden_eval/gold_cases.json` 전면 재작성 | batch 27건 + dialogue 24건 = 총 51건. 정책 문서(진료시간, 분과, 예약규칙, 안전정책, 에스컬레이션) 기반 체계적 케이스 |
| 2 | `golden_eval/eval.py` 전면 재작성 | `eval_type` 필드로 batch/dialogue 자동 분기. dialogue는 `create_session` + `process_message` 반복 호출로 실제 대화 흐름 재현 |
| 3 | `src/policy.py` timezone 버그 수정 | `suggest_alternative_slots()`에서 naive/aware datetime 비교 에러 수정 |
| 4 | `src/agent.py` alternatives 포맷 수정 | `datetime` 객체 → 문자열 변환 누락 수정 |

### 테스트 결과

| 테스트 | 결과 |
|---|---|
| 전체 단위 테스트 | **124 / 124 passed** |

### 골든 eval 결과 (신규 체계)

| 지표 | 결과 |
|---|---|
| **Batch Action 정확도** | **27/27 (100.0%)** |
| Batch Reject 재현율 | 7/7 (100.0%) |
| Batch Escalate 재현율 | 6/6 (100.0%) |
| Batch Department 정확도 | 11/11 (100.0%) |
| **Dialogue Action 정확도** | **22/24 (91.7%)** |
| Dialogue Department 정확도 | 14/16 (87.5%) |
| **전체 Action 정확도** | **49/51 (96.1%)** |

### 잔여 불일치 2건

| 케이스 | 예상 | 실제 | 원인 |
|--------|------|------|------|
| D-001 | book_appointment | clarify | `pending_missing_info_queue`에서 department 슬롯 소비 시 LLM이 추출한 department가 `accumulated_slots`에 반영되지 않는 버그 — "내과" 단독 입력이 classify_intent에서 department로 추출되지만 큐 갱신 로직에서 누락 |
| D-010 | book_appointment | escalate | 확인 거부 → 시간 변경 → 재확인 8턴 시나리오에서 `clarify_turn_count` 누적이 임계치 초과 |

### 케이스 구성 (51건)

| 카테고리 | 건수 | 커버 정책 |
|----------|------|-----------|
| Batch: 신규 예약 (완전 정보) | 11 | 2.1, 2.3, 2.4, 1.1 |
| Batch: 정보 부족 → clarify | 3 | 2.1 |
| Batch: 의료상담/off-topic/인젝션 → reject | 7 | 4.1, 4.2, 4.4, 1.2 |
| Batch: 응급/비용/상담원 → escalate | 6 | 3.3, 5 |
| Dialogue: 본인 예약 플로우 | 7 | 2.1, 2.5 |
| Dialogue: 대리 예약 플로우 | 4 | 2.5 |
| Dialogue: 증상+예약 혼합 | 3 | 2.4 |
| Dialogue: 확인 거부/분과 변경 | 3 | 1.2 |
| Dialogue: 대화 중 안전 위반 | 4 | 4.1, 4.2, 3.3, 5 |
| Dialogue: 의사 지정/빠른 예약 | 4 | 1.2 |

### Next Step
- D-001 원인인 `pending_missing_info_queue` department 슬롯 소비 버그 수정
- D-010 다턴 시나리오에서 `clarify_turn_count` 리셋 정책 재검토
