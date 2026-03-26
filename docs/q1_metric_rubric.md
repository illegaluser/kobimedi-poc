# Q1: 코비메디 AI Agent PoC 성공 지표 (Metric Rubric)

## 1. 비용 구조와 설계 원칙

예약 처리 과정의 비용 구조가 이번 PoC의 설계 방향을 결정합니다.

| 결과 유형 | 건당 영향 | 설명 |
| :--- | :---: | :--- |
| AI 성공 처리 (Agent Success) | **+$10** 절감 | 상담원 없이 예약 확정 |
| AI 실패 → 상담원 연결 (Soft Fail) | **-$20** 비용 | 추가 인력 투입 |
| AI 실패 → 고객 이탈 (Hard Fail) | **-$500** 손실 | 거짓 예약, 의료 오답 등 |

Hard Fail 1건 = 성공 50건의 절감액이 소멸됩니다.
따라서 자동화율보다 **치명적 실패 원천 차단**이 최우선 설계 원칙입니다.

---

## 2. 설계 원칙과 구현 현황

### 원칙 1: 치명적 실패 원천 차단

| 방어 수단 | 구현 위치 | 동작 방식 |
| :--- | :--- | :--- |
| 결정론적 정책 엔진 | `src/policy.py` | 정원(시간당 3명), 운영시간, 24시간 규칙을 Python 코드로 강제. LLM 판단 위임 없음 |
| Safety Gate 최우선 실행 | `src/classifier.py` → `src/agent.py` | 모든 입력에 대해 의도 파악 전 안전 검증을 1순위로 실행 |
| 본인/대리인 선확인 | `src/agent.py` | 대화 첫 턴에 `is_proxy_booking` + 전화번호(진실원천)를 최우선 확인 |
| 저장소 진실원천 | `src/storage.py` | modify/cancel/check 시 `bookings.json`이 유일한 정보원. ticket.context 직접 신뢰 안 함 |
| 거짓 성공 차단 | `src/agent.py` | `create_booking`/`cancel_booking`/Cal.com 실패 시 `AGENT_HARD_FAIL` 반환. SUCCESS 미반환 |

### 원칙 2: 불필요한 상담원 연결 최소화

| 방어 수단 | 구현 위치 | 동작 방식 |
| :--- | :--- | :--- |
| 누락 정보 큐 기반 Clarify | `src/agent.py` | 날짜, 시간, 분과 등 누락 시 우선순위 큐로 자연스럽게 추가 질문 |
| 4턴 에스컬레이션 제한 | `src/agent.py` | 4회 초과 clarify 시 상담원 인계 (무한 루프 방지) |
| 오타/변형 표현 교정 | `src/classifier.py` | "8ㅛㅣ" 등 한영 변환 오타를 자동 정규화 |

### 원칙 3: 비용 효율적 하이브리드 설계

| 방어 수단 | 구현 위치 | 동작 방식 |
| :--- | :--- | :--- |
| Safety Fast-path | `src/classifier.py` | 응급/의료 키워드 매칭 시 LLM 호출 없이 즉시 차단 |
| 대화 상태 Fast-path | `src/agent.py` | 예/아니오 확인, 후보 선택, 본인 응답 등 6개 시나리오에서 LLM 생략 |
| Cal.com Graceful Degradation | `src/calcom_client.py` | API 키 미설정 시 로컬 전용 모드로 자동 전환 |

---

## 3. 성공 지표 (Success Metrics)

| 핵심 KPI | 목표 | 측정 방법 | 관련 KpiEvent |
| :--- | :---: | :--- | :--- |
| **안전 종결률** | **>= 70%** | `(AGENT_SUCCESS + SAFE_REJECT) / 전체 세션` | `AGENT_SUCCESS`, `SAFE_REJECT` |
| **완전 자동화 성공률** | **>= 45%** | `AGENT_SUCCESS / 예약 의도 세션` | `AGENT_SUCCESS` |
| **대화 복구 성공률** | **>= 60%** | `Clarify 후 AGENT_SUCCESS / SOFT_FAIL_CLARIFY 진입 건` | `AGENT_SOFT_FAIL_CLARIFY` → `AGENT_SUCCESS` |

**측정 인프라**: `src/metrics.py`의 `KpiEvent` enum과 `record_kpi_event()`로 모든 이벤트가 실시간 기록됩니다.

---

## 4. 안전 및 제약 지표 (Constraint Metrics)

아래 임계값을 **단 하나라도 초과할 경우 PoC 운영을 즉시 중단**합니다.

| 제약 지표 | 임계값 | 중단 기준 | 방어 구현 |
| :--- | :---: | :--- | :--- |
| **의료 상담 오답률** | **0.0%** | 챗봇이 증상 진단, 약물 추천 등 의료법 위반 소지 답변을 생성한 비율 | Safety Gate (규칙 + LLM) + 하드코딩 거절 문구 |
| **치명적 실패율** | **< 1.0%** | 저장 실패인데 성공 안내, 정원/운영시간 위반 예약 강행 비율 | 결정론적 정책 엔진 + 저장소 실패 시 HARD_FAIL 반환 |
