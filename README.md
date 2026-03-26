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
└── docs/                    # 문서 및 테스트 결과
```

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

유닛 테스트, E2E 테스트, 시나리오 테스트를 선택적으로 실행하고 결과 파일을 자동 생성한다.

```bash
./scripts/run_tests.sh              # 유닛 테스트만 (214개, ~9초)
./scripts/run_tests.sh --e2e        # E2E 테스트만 (28개, ~70초)
./scripts/run_tests.sh --scenario   # 시나리오 테스트만 (51개, ~80초)
./scripts/run_tests.sh --all        # 전체 (유닛 + E2E + 시나리오)
```

| 옵션 | 대상 | 결과 파일 |
|------|------|----------|
| (기본) | 유닛 테스트 214개 | `docs/test_results_unit.txt` |
| `--e2e` | E2E 28개 (Ollama + Cal.com 실제 호출) | `docs/test_results_e2e.txt` |
| `--scenario` | 시나리오 51개 (9개 카테고리) | `docs/test_results_scenario.txt` |
| `--all` | 위 전체 | 3개 파일 모두 |

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

### 3가지 테스트 레벨

| 구분 | 파일 | LLM | 검증 수준 | 속도 |
|------|------|-----|----------|------|
| 유닛 테스트 | `tests/test_scenarios.py` 외 | Mock | 정확한 문자열 + action + 상태 | ~9초 |
| E2E 테스트 | `tests/test_e2e.py` | 실제 Ollama | action enum (느슨) | ~70초 |
| 시나리오 러너 | `scripts/run_scenario_tests.py` | 실제 Ollama | action + 응답 + 상태 전이 (상세) | ~80초 |

### 실행 환경 요구 사항

| 항목 | 유닛 | E2E / 시나리오 |
|------|------|--------------|
| Python 3.12+ | 필수 | 필수 |
| Ollama + qwen3-coder:30b | 불필요 | 필수 |
| .env (CALCOM_API_KEY) | 불필요 | Cal.com 관련 테스트만 |

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
