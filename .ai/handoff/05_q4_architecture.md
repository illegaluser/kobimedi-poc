# Q4: cal.com 연동 아키텍처 (Architecture)

## 1. 개요
이 문서는 기존 코비메디 Agent 아키텍처에 선택 과제인 Q4(cal.com 연동)를 어떻게 결합할지 정의합니다. 
핵심 목표는 **기존 시스템의 안정성(Safety)과 로컬 결정론적 정책(Deterministic Policy)을 훼손하지 않으면서, 외부 API를 안전하게 통합**하는 것입니다.

## 2. 아키텍처 원칙
1. **로컬 저장소 우위 (Local Storage as Source of Truth)**
   - 시스템의 주 진실원천은 여전히 로컬의 `data/bookings.json`입니다.
   - 외부 예약 시스템(cal.com)은 로컬 정책 검증이 통과된 이후에만 보조적으로 접근합니다.
2. **단일 진입점 (Single Entry Point)**
   - 외부 HTTP 통신, API 버전 헤더(`cal-api-version`), 토큰 인증(Bearer) 처리는 모두 `src/calcom_client.py` 내부로 캡슐화합니다.
3. **거짓 성공 원천 차단 (Fail-Safe) 및 동시성 방어**
   - Cal.com API 호출 실패, 네트워크 타임아웃 발생 시 로컬 저장소 기록도 중단하며, 사용자에게 절대로 "예약 성공" 응답을 반환하지 않습니다.
   - 확정 질문 시점과 실제 예약 시점 사이의 Race Condition(슬롯 선점) 발생 시 충돌로 감지하여 확정을 보류합니다.
4. **우아한 성능 저하 (Graceful Degradation)**
   - 환경 변수 미설정, API Key 누락 시 시스템 장애 없이 외부 연동 로직만 건너뛰고 기존 로컬 파이프라인으로 예약을 진행합니다.
5. **모드 통합 (Batch vs Chat)**
   - 배치 모드(`!is_chat`)에서도 슬롯 가용성 확인 후 즉시 예약 생성을 시도하며, 마감 시에는 상호작용 없이 `clarify`로 대안을 응답에 포함합니다.

## 3. 예약 파이프라인 통합 흐름 (End-to-End)

```text
[기존 파이프라인]
1. Safety Gate 통과
2. Intent Classification & Entity Extraction
3. Dialogue State Merge (본인 확인, 누락 정보 수집 완료)
4. Local Storage Lookup
5. Local Policy Check (apply_policy) -> 성공 시 진행
      ↓
[Q4 연동 구간 1: 가용성 교차 검증]
6-a. calcom_client.get_available_slots() 호출
     - 실패 시: 외부 시스템 응답 지연 안내 (clarify 폴백)
     - 마감 시: 대체 슬롯 제시 (clarify 폴백)
     - 통과 시: 사용자에게 최종 확인 질문(Confirmation) 반환
      ↓
[사용자 확정("네", "진행해주세요" 등) 수신]
      ↓
[Q4 연동 구간 2: 실제 예약 생성 (로컬 기록 직전)]
6-b. calcom_client.create_booking() 호출
     - 실패 시: 예약 시스템 응답 지연으로 실패 안내 (clarify) + AGENT_HARD_FAIL
     - 충돌(Conflict) 시: 방금 전 마감됨을 안내하고 대안 제시 모드로 롤백 (clarify)
     - API 비활성 시: 우회 통과
     - 성공 시: 진행
      ↓
7. 로컬 Persistence (src/storage.py create_booking)
8. 최종 예약 완료 응답 반환
```

## 4. 컴포넌트별 책임
### `src/calcom_client.py` (신규)
- `.env` 파일로부터 `CALCOM_API_KEY` 및 분과별 `Event Type ID` 로드.
- `get_available_slots(department, date)`: v2 `/slots` API를 호출하여 가용 `HH:MM` 문자열 배열 반환.
- `create_booking(...)`: v2 `/bookings` API를 호출하여 예약 생성 (ISO 8601 KST 포맷 변환 및 전화번호 기반 더미 이메일 생성 처리 포함).
- 모든 HTTP 예외(`requests.RequestException`)를 내부에서 Catch하여 `None`을 반환, 시스템 중단(Crash) 방지.