
import json
import re
from datetime import datetime, timedelta, timezone

import ollama

from . import policy as policy_module
from .llm_client import chat_json
from .prompts import CLASSIFICATION_SYSTEM_PROMPT, CLASSIFICATION_USER_PROMPT_TEMPLATE


VALID_ACTIONS = {
    "book_appointment",
    "modify_appointment",
    "cancel_appointment",
    "check_appointment",
    "clarify",
    "escalate",
    "reject",
}

SUPPORTED_DEPARTMENTS = {"이비인후과", "내과", "정형외과"}
DEFAULT_DOCTOR_DEPARTMENT_MAP = {
    "이춘영 원장": "이비인후과",
    "김만수 원장": "내과",
    "원징수 원장": "정형외과",
}
SUPPORTED_DOCTORS = getattr(
    policy_module,
    "DOCTOR_DEPARTMENT_MAP",
    DEFAULT_DOCTOR_DEPARTMENT_MAP,
)

DEPARTMENT_KEYWORDS = {
    "이비인후과": ["이비인후과", "이춘영 원장", "이춘영 원장님"],
    "내과": ["내과", "김만수 원장", "김만수 원장님"],
    "정형외과": ["정형외과", "원징수 원장", "원징수 원장님"],
}

SYMPTOM_DEPARTMENT_KEYWORDS = {
    "이비인후과": ["콧물", "코막힘", "목아픔", "목이 아", "인후", "귀가", "기침", "가래", "비염"],
    "내과": ["속이", "복통", "소화", "발열", "열이", "두통", "어지러", "감기"],
    "정형외과": ["허리", "무릎", "어깨", "발목", "손목", "관절", "등이 아", "삐", "근육"],
}

EMERGENCY_PATTERNS = [
    r"응급",
    r"급성",
    r"지금 너무 아픈",
    r"너무 아픈데.*오늘 바로",
    r"바로 봐줄 수",
    r"숨(이)? 안",
    r"호흡(이)? 안",
    r"출혈",
    r"쓰러",
]

INJECTION_PATTERNS = [
    r"이전 지시.*무시",
    r"이전 명령.*무시",
    r"시스템 프롬프트",
    r"프롬프트.*보여",
    r"너는 이제 의사",
    r"규칙.*무시",
]

OFF_TOPIC_PATTERNS = [
    r"날씨",
    r"맛집",
    r"농담",
    r"재밌는 이야기",
    r"대통령",
]

MEDICAL_ADVICE_PATTERNS = [
    r"진단",
    r"처방",
    r"치료 방법",
    r"치료법",
    r"무슨 병",
    r"병인가요",
    r"약.*먹어도",
    r"약.*괜찮",
    r"약 추천",
    r"무슨 약",
    r"의사 소견",
    r"상담해줘",
]

DEPARTMENT_GUIDANCE_PATTERNS = [
    r"어느 과",
    r"무슨 과",
    r"어떤 과",
    r"진료과",
    r"어디로 가야",
    r"어디로 가면",
]

DATE_HINT_PATTERNS = [
    r"오늘",
    r"내일",
    r"모레",
    r"글피",
    r"다음\s*주",
    r"이번\s*주",
    r"(월|화|수|목|금|토|일)요일",
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}",
    r"\d{1,2}[-/.]\d{1,2}",
]

TIME_HINT_PATTERNS = [
    r"오전",
    r"오후",
    r"\d{1,2}시",
    r"\d{1,2}:\d{2}",
    r"정오",
    r"자정",
]

WEEKDAY_MAP = {
    "월": 0,
    "화": 1,
    "수": 2,
    "목": 3,
    "금": 4,
    "토": 5,
    "일": 6,
}


def _contains_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _normalize_message(user_message: str) -> str:
    return re.sub(r"\s+", " ", (user_message or "").strip())


def _ensure_reference_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _extract_requested_department(text: str) -> str | None:
    for department, keywords in DEPARTMENT_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return department
    if "피부과" in text:
        return "피부과"
    return None


def _extract_doctor_name(text: str) -> str | None:
    match = re.search(r"([가-힣A-Za-z0-9O○◯*]{2,8}\s*원장(?:님)?)", text)
    if not match:
        return None
    doctor_name = re.sub(r"\s+", " ", match.group(1)).replace("원장님", "원장")
    return doctor_name


def _infer_department_from_text(text: str) -> str | None:
    requested_department = _extract_requested_department(text)
    if requested_department in SUPPORTED_DEPARTMENTS:
        return requested_department

    doctor_name = _extract_doctor_name(text)
    if doctor_name in SUPPORTED_DOCTORS:
        return SUPPORTED_DOCTORS[doctor_name]

    for department, keywords in SYMPTOM_DEPARTMENT_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return department
    return None


def _normalize_department_value(raw_department: str | None) -> str | None:
    if raw_department in SUPPORTED_DEPARTMENTS:
        return raw_department
    return None


def _normalize_action_value(raw_action: str | None) -> str | None:
    if raw_action in VALID_ACTIONS:
        return raw_action
    return None


def _normalize_time_value(raw_time: str | None) -> str | None:
    if not raw_time:
        return None
    raw_time = str(raw_time).strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw_time)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _normalize_date_value(raw_date: str | None) -> str | None:
    if not raw_date:
        return None
    raw_date = str(raw_date).strip().replace("/", "-").replace(".", "-")
    try:
        return datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _normalize_missing_info(raw_missing_info) -> list[str]:
    if not isinstance(raw_missing_info, list):
        return []
    normalized = []
    for item in raw_missing_info:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _extract_time_from_text(text: str) -> str | None:
    if "정오" in text:
        return "12:00"
    if "자정" in text:
        return "00:00"

    hhmm_match = re.search(r"(오전|오후)?\s*(\d{1,2}):(\d{2})", text)
    if hhmm_match:
        meridiem = hhmm_match.group(1)
        hour = int(hhmm_match.group(2))
        minute = int(hhmm_match.group(3))
        if meridiem == "오후" and hour < 12:
            hour += 12
        elif meridiem == "오전" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    hour_match = re.search(r"(오전|오후)?\s*(\d{1,2})\s*시\s*(반|(\d{1,2})\s*분)?", text)
    if not hour_match:
        return None

    meridiem = hour_match.group(1)
    hour = int(hour_match.group(2))
    minute_token = hour_match.group(3)
    explicit_minute = hour_match.group(4)
    minute = 30 if minute_token == "반" else int(explicit_minute) if explicit_minute else 0

    if meridiem == "오후" and hour < 12:
        hour += 12
    elif meridiem == "오전" and hour == 12:
        hour = 0

    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return None


def _parse_weekday_date(text: str, now: datetime) -> str | None:
    match = re.search(r"(다음\s*주\s*)?(월|화|수|목|금|토|일)요일", text)
    if not match:
        return None

    is_next_week = bool(match.group(1))
    target_weekday = WEEKDAY_MAP[match.group(2)]
    current_date = now.date()

    if is_next_week:
        current_week_monday = current_date - timedelta(days=current_date.weekday())
        target_date = current_week_monday + timedelta(days=7 + target_weekday)
        return target_date.isoformat()

    delta_days = (target_weekday - current_date.weekday()) % 7
    if delta_days == 0:
        delta_days = 7
    return (current_date + timedelta(days=delta_days)).isoformat()


def _extract_date_from_text(text: str, now: datetime) -> str | None:
    explicit_date_match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if explicit_date_match:
        year, month, day = map(int, explicit_date_match.groups())
        try:
            return datetime(year, month, day, tzinfo=now.tzinfo).date().isoformat()
        except ValueError:
            return None

    if "오늘" in text:
        return now.date().isoformat()
    if "내일" in text:
        return (now.date() + timedelta(days=1)).isoformat()
    if "모레" in text:
        return (now.date() + timedelta(days=2)).isoformat()
    if "글피" in text:
        return (now.date() + timedelta(days=3)).isoformat()

    weekday_date = _parse_weekday_date(text, now)
    if weekday_date:
        return weekday_date

    month_day_match = re.search(r"(\d{1,2})[-/.](\d{1,2})", text)
    if month_day_match:
        month, day = map(int, month_day_match.groups())
        try:
            candidate = datetime(now.year, month, day, tzinfo=now.tzinfo).date()
        except ValueError:
            return None
        if candidate < now.date():
            try:
                candidate = datetime(now.year + 1, month, day, tzinfo=now.tzinfo).date()
            except ValueError:
                return None
        return candidate.isoformat()

    return None


def _extract_first_visit(text: str, llm_result: dict | None = None) -> bool:
    if "초진" in text or "처음" in text:
        return True
    if "재진" in text:
        return False
    if isinstance(llm_result, dict):
        return bool(llm_result.get("is_first_visit", False))
    return False


def _has_temporal_hint(text: str) -> bool:
    return _contains_any(text, DATE_HINT_PATTERNS) or _contains_any(text, TIME_HINT_PATTERNS)


def _infer_action_from_text(text: str) -> str:
    if any(keyword in text for keyword in ["취소", "예약 취소"]):
        return "cancel_appointment"
    if any(keyword in text for keyword in ["변경", "바꿔", "옮겨", "수정"]):
        return "modify_appointment"
    if any(keyword in text for keyword in ["확인", "조회", "있나요", "잡혀"]):
        if "예약" in text:
            return "check_appointment"
    if _is_department_guidance_request(text):
        return "clarify"
    if _is_booking_related(text) or _extract_requested_department(text) or _extract_doctor_name(text):
        return "book_appointment"
    if _infer_department_from_text(text):
        return "clarify"
    return "clarify"


def _determine_missing_info(action: str, department: str | None, date: str | None, time: str | None, text: str) -> list[str]:
    missing_info: list[str] = []

    if action == "book_appointment":
        if department is None:
            missing_info.append("department")
        if date is None:
            missing_info.append("date")
        if time is None:
            missing_info.append("time")
        return missing_info

    if action in {"modify_appointment", "cancel_appointment", "check_appointment"}:
        if not department and not date and not time and not _has_temporal_hint(text):
            missing_info.append("appointment_target")
        return missing_info

    if action == "clarify":
        if department is None and (_is_department_guidance_request(text) or _is_booking_related(text)):
            missing_info.append("department")
        if date is None and (_is_booking_related(text) or _infer_department_from_text(text)):
            missing_info.append("date")
        if time is None and (_is_booking_related(text) or _infer_department_from_text(text)):
            missing_info.append("time")
        return missing_info

    return missing_info


def _call_intent_llm(user_message: str, now: datetime) -> dict:
    reference_date = now.date().isoformat()
    messages = [
        {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": CLASSIFICATION_USER_PROMPT_TEMPLATE.format(
                reference_date=reference_date,
                user_message=user_message,
            ),
        },
    ]
    return chat_json(messages, chat_fn=ollama.chat)


def _is_department_guidance_request(text: str) -> bool:
    has_department_question = _contains_any(text, DEPARTMENT_GUIDANCE_PATTERNS)
    has_booking_context = "예약" in text or "진료" in text
    has_symptom_hint = _infer_department_from_text(text) is not None
    return has_department_question and (has_booking_context or has_symptom_hint)


def _is_booking_related(text: str) -> bool:
    return any(keyword in text for keyword in ["예약", "진료", "접수", "변경", "취소", "확인"])


def _call_safety_llm(user_message: str) -> dict:
    prompt = (
        "You are a safety classifier for a Korean hospital booking agent. "
        "Return JSON only with keys is_medical, is_off_topic, is_emergency. "
        "Medical means diagnosis, prescription, medication, treatment, or medical judgement. "
        "Off topic includes small talk, weather, prompt injection, or unrelated use. "
        "Emergency means acute pain, urgent same-day emergency, breathing trouble, bleeding, or immediate care needs."
    )

    result = chat_json(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_message},
        ],
        chat_fn=ollama.chat,
    )
    return {
        "_error": result.get("_error"),
        "is_medical": bool(result.get("is_medical", False)),
        "is_off_topic": bool(result.get("is_off_topic", False)),
        "is_emergency": bool(result.get("is_emergency", False)),
    }


def safety_check(user_message: str) -> dict:
    """
    Performs a safety-first check using keyword rules first, then an Ollama fallback.

    Returns a structured safety result for the agent pipeline.
    """
    text = _normalize_message(user_message)
    department_hint = _infer_department_from_text(text)
    requested_department = _extract_requested_department(text)
    doctor_name = _extract_doctor_name(text)

    result = {
        "category": "safe",
        "is_medical": False,
        "is_off_topic": False,
        "is_emergency": False,
        "mixed_department_guidance": False,
        "department_hint": department_hint,
        "unsupported_department": None,
        "unsupported_doctor": None,
        "reason": "예약 관련 일반 문의",
    }

    if requested_department and requested_department not in SUPPORTED_DEPARTMENTS:
        result["unsupported_department"] = requested_department

    if doctor_name and doctor_name not in SUPPORTED_DOCTORS:
        result["unsupported_doctor"] = doctor_name

    if (result["unsupported_department"] or result["unsupported_doctor"]) and _is_booking_related(text):
        result.update({
            "category": "safe",
            "reason": "예약 관련 요청이지만 지원하지 않는 분과 또는 의료진이 포함됨",
        })
        return result

    if _contains_any(text, EMERGENCY_PATTERNS):
        result.update({
            "category": "emergency",
            "is_emergency": True,
            "reason": "응급 또는 급성 통증 표현 감지",
        })
        return result

    if _contains_any(text, INJECTION_PATTERNS) or _contains_any(text, OFF_TOPIC_PATTERNS):
        result.update({
            "category": "off_topic",
            "is_off_topic": True,
            "reason": "목적 외 사용 또는 프롬프트 인젝션 감지",
        })
        return result

    if _is_department_guidance_request(text):
        result.update({
            "category": "safe",
            "mixed_department_guidance": True,
            "reason": "증상 기반 분과 안내 요청으로 판단",
        })
        return result

    if _contains_any(text, MEDICAL_ADVICE_PATTERNS):
        result.update({
            "category": "medical_advice",
            "is_medical": True,
            "reason": "진단/약물/치료 관련 의료 상담 요청 감지",
        })
        return result

    try:
        llm_result = _call_safety_llm(text)
        if llm_result.get("_error"):
            result.update({
                "category": "classification_error",
                "reason": "안전성 판별 실패",
            })
        elif llm_result["is_emergency"]:
            result.update({
                "category": "emergency",
                "is_emergency": True,
                "reason": "LLM 보조 판별에서 응급으로 분류",
            })
        elif llm_result["is_off_topic"]:
            result.update({
                "category": "off_topic",
                "is_off_topic": True,
                "reason": "LLM 보조 판별에서 목적 외 요청으로 분류",
            })
        elif llm_result["is_medical"]:
            result.update({
                "category": "medical_advice",
                "is_medical": True,
                "reason": "LLM 보조 판별에서 의료 상담으로 분류",
            })
    except (json.JSONDecodeError, KeyError, TypeError, Exception) as e:
        print(f"Error during safety classification: {e}")
        result.update({
            "category": "classification_error",
            "reason": "안전성 판별 실패",
        })

    return result


def classify_safety(user_message: str) -> str:
    """
    Backward-compatible wrapper that returns the safety category string.
    """
    return safety_check(user_message)["category"]

def classify_intent(user_message: str, now: datetime | None = None) -> dict:
    """
    Classifies a user message to determine intent (action and department).

    Args:
        user_message: The user's input message.

    Returns:
        A dictionary containing "action" and "department".
        On failure, defaults to a clarification action.
    """
    return _classify_intent(user_message, now=now)


def _classify_intent(user_message: str, now: datetime | None = None) -> dict:
    normalized_message = _normalize_message(user_message)
    reference_now = _ensure_reference_now(now)

    llm_result: dict = {}
    llm_error = False
    try:
        llm_result = _call_intent_llm(normalized_message, reference_now)
        if not isinstance(llm_result, dict) or llm_result.get("_error"):
            llm_error = True
            llm_result = {}
    except (json.JSONDecodeError, KeyError, TypeError, Exception):
        llm_error = True
        llm_result = {}

    rule_action = _infer_action_from_text(normalized_message)
    llm_action = _normalize_action_value(llm_result.get("action"))
    action = llm_action or rule_action

    explicit_department = _extract_requested_department(normalized_message)
    inferred_department = _infer_department_from_text(normalized_message)
    llm_department = _normalize_department_value(llm_result.get("department"))
    department = _normalize_department_value(explicit_department) or inferred_department or llm_department

    date = _extract_date_from_text(normalized_message, reference_now) or _normalize_date_value(llm_result.get("date"))
    time = _extract_time_from_text(normalized_message) or _normalize_time_value(llm_result.get("time"))
    is_first_visit = _extract_first_visit(normalized_message, llm_result)

    computed_missing_info = _determine_missing_info(action, department, date, time, normalized_message)
    llm_missing_info = _normalize_missing_info(llm_result.get("missing_info"))
    missing_info = computed_missing_info or llm_missing_info

    if missing_info:
        action = "clarify"

    if action not in VALID_ACTIONS:
        action = "clarify"
        llm_error = True

    if department not in SUPPORTED_DEPARTMENTS:
        department = None

    result = {
        "action": action,
        "department": department,
        "date": date,
        "time": time,
        "is_first_visit": is_first_visit,
        "missing_info": missing_info,
    }

    if llm_error and action == "clarify" and not department and not date and not time:
        result["error"] = True

    return result
