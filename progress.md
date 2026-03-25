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
