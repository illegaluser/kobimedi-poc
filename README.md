# 코비메디 예약 챗봇 PoC

서울 소재 중형 네트워크 병원 **코비메디**의 진료 예약 접수/변경/취소 업무를 자동화하는 AI Agent PoC.

## 빠른 시작

```bash
# 1. 환경 초기화
./scripts/init.sh

# 2. 인터랙티브 데모
python chat.py

# 3. 배치 처리
python run.py --input data/tickets.json --output results.json
```

## 프로젝트 구조

```
kobimedi-poc/
├── chat.py                  # 인터랙티브 챗봇 (모드 1)
├── run.py                   # 배치 처리 (모드 2)
├── src/
│   ├── agent.py             # 핵심 에이전트 파이프라인
│   ├── classifier.py        # Safety gate + 의도 분류 (Ollama)
│   ├── policy.py            # 결정론적 정책 엔진
│   ├── storage.py           # bookings.json 저장소
│   ├── calcom_client.py     # Cal.com API v2 연동 (Q4)
│   ├── response_builder.py  # 응답 생성
│   ├── llm_client.py        # Ollama LLM 래퍼
│   ├── models.py            # 데이터 모델 (Action, Booking, Ticket 등)
│   └── metrics.py           # KPI 이벤트 기록
├── scripts/                 # 운영 스크립트
├── tests/                   # 테스트 (유닛 + E2E + 시나리오)
├── data/
│   ├── tickets.json         # 입력 티켓 50건
│   └── bookings.json        # 예약 저장소 (진실원천)
└── docs/
    ├── final_report.md          # 최종 리포트 (Q1~Q4 통합)
    ├── q1_metric_rubric.md      # Q1: PoC 성공 지표 제안서
    ├── q3_safety.md             # Q3: 안전성 대응 방안
    ├── architecture.md          # 에이전트 아키텍처 설계 문서
    ├── policy_digest.md         # 진료 예약 정책 요약
    ├── demo_evidence.md         # 인터랙티브 데모 증빙
    ├── test_scenarios.md        # 테스트 시나리오 명세 (유닛 51 + E2E 28)
    ├── test_results_unit.txt    # 유닛 테스트 실행 결과
    └── test_results_scenario.txt # 시나리오 테스트 실행 결과
```

## 시스템 아키텍처

### 파이프라인 흐름

사용자 메시지가 입력되면 아래 순서로 처리된다. 각 단계는 독립적이며, 앞 단계에서 차단되면 뒤 단계는 실행되지 않는다.

```
사용자 발화
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ 1. Safety Gate  (classifier.py)                         │
│    규칙 기반 패턴 매칭 → LLM 폴백                        │
│    의료 상담 → reject                                    │
│    프롬프트 인젝션 → reject                               │
│    응급/불만 → escalate                                  │
│    비용/보험 → escalate                                  │
│    개인정보 요청 → reject                                 │
│    미지원 분과/의사 → reject                              │
│    증상 기반 분과 안내 → clarify (진단 아닌 안내)           │
└──────────────┬──────────────────────────────────────────┘
               │ safe
               ▼
┌─────────────────────────────────────────────────────────┐
│ 2. 의도 분류 + 정보 추출  (classifier.py → Ollama LLM)   │
│    action: book / modify / cancel / check / clarify      │
│    추출: 분과, 날짜, 시간, 환자명, 연락처, 대리 여부       │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 3. 대화 상태 병합  (agent.py)                            │
│    채팅 모드: session_state에 누적 슬롯 병합              │
│    is_proxy_booking → patient_name → patient_contact     │
│    → department → date → time 순서로 수집                │
│    4회 clarify 실패 → escalate                           │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 4. 저장소 조회  (storage.py → bookings.json)             │
│    전화번호 우선 환자 식별                                │
│    초진/재진 판정 (저장소가 진실원천)                      │
│    기존 예약 조회 (변경/취소/확인 시)                      │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 5. 정책 검사  (policy.py) — 결정론, LLM 위임 금지        │
│    운영시간: 평일 09-18, 토 09-13, 일 휴진, 점심 차단     │
│    정원: 1시간당 최대 3명                                 │
│    겹침: 초진 40분 / 재진 30분 슬롯 충돌 감지             │
│    24시간 룰: 변경/취소는 예약 24시간 전까지               │
│    대안 슬롯: 거절 시 같은 날 1-3개 제안                  │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 6. Cal.com 연동  (calcom_client.py) — Q4 선택            │
│    가용 슬롯 교차 검증 (확인 질문 전)                     │
│    예약 생성 (사용자 확인 후)                              │
│    실패 시 로컬 저장 차단 (거짓 성공 방지)                 │
│    API 미설정 시 로컬만으로 정상 동작 (Graceful Degradation)│
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 7. 영속화 + 응답  (storage.py → response_builder.py)     │
│    bookings.json 원자적 저장 (temp + rename)             │
│    확인 질문 / 성공 메시지 / clarify / reject 생성        │
│    confidence + reasoning 동적 계산                       │
└─────────────────────────────────────────────────────────┘
```

### 모듈 의존성

```
chat.py ──┐
           ├──▶ src/agent.py (공유 핵심 로직)
run.py  ──┘         │
                     ├──▶ src/classifier.py ──▶ src/llm_client.py ──▶ Ollama
                     ├──▶ src/policy.py (순수 산술, 외부 의존 없음)
                     ├──▶ src/storage.py ──▶ data/bookings.json
                     ├──▶ src/calcom_client.py ──▶ Cal.com API v2
                     ├──▶ src/response_builder.py
                     └──▶ src/metrics.py
```

### Action 분류 체계

에이전트는 모든 요청을 아래 7개 action 중 하나로 판정한다.

| Action | 설명 | 예시 |
|--------|------|------|
| `book_appointment` | 신규 예약 | "내일 오후 2시 이비인후과 예약" |
| `modify_appointment` | 기존 예약 변경 | "수요일 예약을 목요일로 변경" |
| `cancel_appointment` | 기존 예약 취소 | "금요일 예약 취소해주세요" |
| `check_appointment` | 예약 확인/조회 | "다음 주 예약 확인해주세요" |
| `clarify` | 정보 부족, 추가 확인 | "예약하고 싶어요" (날짜/시간/분과 누락) |
| `escalate` | 상담원 연결 | 급성 통증, 반복 불만, 보험/비용 문의 |
| `reject` | 목적 외 사용 거부 | 의료 상담, 잡담, 프롬프트 인젝션 |

---

## 스크립트 사용법

### `scripts/init.sh` — 환경 초기화

프로젝트 최초 세팅 시 실행. 가상환경 생성, 의존성 설치, Ollama 모델 상태 확인.

```bash
./scripts/init.sh
```

### `scripts/check.sh` — 전체 검증

구문 검사, Feature 통과율, 유닛 테스트, 배치 처리, Gold 평가를 한 번에 실행.

```bash
./scripts/check.sh
```

### `scripts/run_tests.sh` — 테스트 실행기

유닛 테스트와 시나리오 테스트를 선택적으로 실행하고 결과 파일을 자동 생성한다.

```bash
./scripts/run_tests.sh              # 유닛 테스트만 (~9초)
./scripts/run_tests.sh --scenario   # 시나리오 테스트만 (51개, ~80초)
./scripts/run_tests.sh --all        # 유닛 + 시나리오 전체
```

| 옵션 | 대상 | 결과 파일 |
|------|------|----------|
| (기본) | 유닛 테스트 | `docs/test_results_unit.txt` |
| `--scenario` | 시나리오 테스트 51개 (실제 Ollama + Cal.com) | `docs/test_results_scenario.txt` |
| `--all` | 위 전체 | 2개 파일 모두 |

### `scripts/run_scenario_tests.py` — 시나리오 테스트 러너

`docs/test_scenarios.md`에 정의된 51개 시나리오를 실제 LLM으로 대화 흐름대로 실행하고, 턴별 결과를 상세히 리포트한다.

```bash
# 전체 9개 카테고리
python scripts/run_scenario_tests.py

# 특정 카테고리만
python scripts/run_scenario_tests.py --category 1    # Happy Path
python scripts/run_scenario_tests.py --category 5    # Safety Gate

# LLM 없이 정책 엔진만 (카테고리 3, 4, 7)
python scripts/run_scenario_tests.py --policy-only

# 결과를 파일에 저장
python scripts/run_scenario_tests.py --output docs/test_results_scenario.txt
```

| 카테고리 | 테스트 수 | LLM 필요 |
|---------|----------|---------|
| 1. 정상 예약 완료 | 4 | O |
| 2. 환자 식별 & 대리 | 4 | O |
| 3. 정책 엔진 슬롯 계산 | 5 | X |
| 4. 24시간 변경/취소 | 5 | X |
| 5. Safety Gate | 7 | O |
| 6. 분과/운영시간 | 3 | O |
| 7. 운영시간 정책 (F-052) | 12 | X |
| 8. 대화 상태 관리 | 3 | O |
| 9. Cal.com 외부 연동 | 8 | O |

### `scripts/cleanup_bookings.py` — Cal.com 예약 일괄 삭제 + 로컬 동기화

테스트 과정에서 생성된 Cal.com 원격 예약과 로컬 bookings.json을 한 번에 정리한다.

```bash
# 삭제 대상만 확인 (실제 삭제 안 함)
python scripts/cleanup_bookings.py --dry-run

# 전체 삭제 실행 (확인 프롬프트)
python scripts/cleanup_bookings.py

# 확인 없이 즉시 실행
python scripts/cleanup_bookings.py --force

# 로컬 bookings.json만 초기화 (Cal.com 안 건드림)
python scripts/cleanup_bookings.py --local-only
```

**동작 흐름:**
1. Cal.com `GET /bookings` → 원격 예약 목록 조회
2. 각 예약에 `POST /bookings/{uid}/cancel` → 원격 취소
3. 로컬 `data/bookings.json` → `[]` 초기화

## 테스트

### 테스트 파일 목록

총 10개 테스트 파일, **226개 유닛 테스트** + 시나리오 러너 51개.

| 파일 | 테스트 수 | 검증 대상 |
|------|----------|----------|
| `tests/test_scenarios.py` | 51 | 9개 카테고리 시나리오 (Happy Path, Identity, Policy, 24h, Safety, Department, Operating Hours, Dialogue, Cal.com) |
| `tests/test_calcom.py` | 51 | Cal.com API 연동 (슬롯 조회, 예약 생성, 취소, Race Condition, Graceful Degradation) |
| `tests/test_safety.py` | 35 | Safety gate (의료 상담 차단, 인젝션 방어, 증상 안내, 혼합 요청 분리, LLM 폴백) |
| `tests/test_response_builder.py` | 27 | 응답 메시지 생성 (proxy 질문, 이름/연락처 수집, 분과/시간 안내) |
| `tests/test_classifier.py` | 20 | 의도 분류 (LLM 파싱, 에러 복구, 의사→분과 매핑, 증상→분과 매핑) |
| `tests/test_policy.py` | 14 | 정책 엔진 (슬롯 겹침, 정원, 24시간 룰, 대안 슬롯, 초진/재진 시간) |
| `tests/test_dialogue.py` | 13 | 멀티턴 대화 (proxy 수집, 상태 유지, clarify 에스컬레이션, 배치/채팅 분기) |
| `tests/test_storage.py` | 11 | 저장소 (영속화, 중복 방지, 취소, 초진/재진 판정, 파일 손상 복구) |
| `tests/test_generalization.py` | 3 | 일반화 (한국어 인젝션, 혼합 요청, 모호한 환자 유형) |
| `tests/test_batch.py` | 1 | 배치 모드 (run.py 출력 스키마 + KPI 메트릭) |

### 2가지 테스트 레벨

| 레벨 | 설명 | LLM | 속도 |
|------|------|-----|------|
| **유닛 테스트** (226개) | Mock 기반, 각 컴포넌트 격리 검증 | Mock | ~9초 |
| **시나리오 테스트** (51개) | 실제 Ollama + Cal.com + Storage, 대화 흐름 검증 | 실제 호출 | ~80초 |

시나리오 테스트는 `scripts/run_scenario_tests.py`로 실행하며, 각 턴마다 사용자 발화 → 챗봇 응답 → action → 상태 변화를 상세히 출력한다. exit code로 PASS/FAIL을 판정하므로 CI에서도 사용 가능하다.

### 실행 환경 요구 사항

| 항목 | 유닛 테스트 | 시나리오 테스트 |
|------|-----------|--------------|
| Python 3.12+ | 필수 | 필수 |
| Ollama + qwen3-coder:30b | 불필요 | 필수 |
| .env (CALCOM_API_KEY) | 불필요 | Cal.com 관련 시나리오만 |

### 시나리오 명세서

모든 시나리오의 사용자 발화, 실행 의도, 기대 결과는 아래 문서에 정리되어 있다.

- [docs/test_scenarios.md](docs/test_scenarios.md) — 유닛 51개 + E2E 28개 + 비교표

## 도메인 정보

| 분과 | 담당 의사 | 기본 슬롯 |
|------|----------|----------|
| 이비인후과 | 이춘영 원장 | 30분 |
| 내과 | 김만수 원장 | 30분 |
| 정형외과 | 원징수 원장 | 30분 |

### 핵심 예약 정책

- 1시간 타임 윈도우당 최대 3명
- 초진 환자 40분 슬롯 (재진 30분)
- 변경/취소는 예약 시간 24시간 전까지만 가능
- 평일 09:00-18:00, 토 09:00-13:00, 일 휴진
- 점심시간 12:30-13:30 예약 불가
- 의료 상담/진단/약물 추천 절대 금지

## AI 도구 활용 내역

| 도구 | 용도 |
|------|------|
| Claude Code (Anthropic CLI) | 아키텍처 설계, 구현, 테스트 작성, 코드 리뷰 |
| Ollama + qwen3-coder:30b | 챗봇 LLM (Safety gate, 의도 분류, 정보 추출) |
| Cal.com API v2 | 외부 예약 시스템 연동 (Q4) |
