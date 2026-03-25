# Q4: cal.com 연동 구현 명세 (Implementation Details)

이 문서는 실제 코드 작성 시 고려해야 할 핵심 코드 구조와 가이드입니다.

## 1. `src/calcom_client.py` 뼈대
```python
import os
import requests
from datetime import datetime

CALCOM_API_KEY = os.environ.get("CALCOM_API_KEY")
CALCOM_BASE_URL = "https://api.cal.com/v2"

# ... (Event Type 매핑 설정) ...

def get_available_slots(department: str, target_date: str):
    # 1. 활성화 여부 검사
    # 2. v2 GET /slots API 요청 (cal-api-version: 2024-09-04)
    # 3. RequestException 등 예외 발생 시 로깅 후 None 반환 (Fail-safe)
    # 4. JSON 응답 파싱하여 ["09:00", "09:30", ...] 형태로 반환
    # 5. API 비활성 시 우회 처리를 위해 caller 측에서 is_calcom_enabled 검사 필수
    pass

def create_booking(department: str, date: str, time: str, patient_name: str, patient_contact: str):
    # 1. 활성화 여부 검사
    # 2. ISO 8601 변환 (KST 기준 +09:00)
    # 3. v2 POST /bookings API 요청 (cal-api-version: 2024-08-13)
    # 4. RequestException 예외 발생 시 None 반환
    # 5. 응답 코드 409 (Conflict) 발생 시 Race Condition 식별을 위해 특수 값(예: False) 반환 고려
    pass
```

## 2. `src/agent.py` 수정 사항
- 예약 확정 직전 (`action == "book_appointment"` 블록 내부):
  - 사용자에게 `build_confirmation_question` 메시지를 생성하기 직전 `get_available_slots`을 호출. 
  - 가용 시간이 None(통신 실패)이거나 요청 시간이 목록에 없다면, 대체 슬롯 문구 조합 후 `clarify` 액션 반환.
- 예약 확정 처리 (`_handle_pending_confirmation` 및 배치모드 확정부):
  - `calcom_client.create_booking()` 실행 결과가 `None`인 경우,
  - `record_kpi_event(KpiEvent.AGENT_HARD_FAIL)` 호출
  - `action="clarify"`와 "예약 시스템 응답 지연..." 메시지로 응답하여 거짓 성공 원천 차단.

*위 명세를 기준으로 다음 스텝에서 코딩을 바로 시작할 수 있습니다.*