
import json
from src.agent import process_ticket

def main():
    """
    Runs an interactive chat session with the appointment booking agent.
    """
    print("코비메디 예약 에이전트입니다. 무엇을 도와드릴까요? (종료하려면 'exit' 입력)")
    
    # In a real application, this would be a database.
    # For this PoC, we load a static list and keep it in memory.
    try:
        with open("data/appointments.json", 'r', encoding='utf-8') as f:
            all_appointments = json.load(f)
    except FileNotFoundError:
        all_appointments = []
    
    # 사용자 이름을 입력받아 기존/신규 사용자를 구분합니다.
    # (예: 최유진, 서장훈, 또는 목록에 없는 이름)
    customer_name = input("사용자 이름을 입력하세요: ")
    if not customer_name:
        customer_name = "신규사용자" # Default for empty input

    existing_appointment = next(
        (appt for appt in all_appointments if appt["customer_name"] == customer_name),
        None
    )
    if existing_appointment:
        print(f"({customer_name}님, 기존 예약이 확인되었습니다.)")


    while True:
        try:
            user_message = input("나> ")
            if user_message.lower() == 'exit':
                print("Agent> 감사합니다. 안녕히 가세요.")
                break

            # Create a ticket from the user's message
            ticket = {
                "customer_name": customer_name,
                "message": user_message,
                # In a real chat, we'd need to parse time/type from the message.
                # We'll add dummy data here that tests might rely on.
                "booking_time": "2026-04-11T16:00:00Z",
                "customer_type": "재진"
            }

            # Process the ticket
            result = process_ticket(ticket, all_appointments, existing_appointment)

            # Print the agent's response
            print(f"Agent> {result.get('response', '오류가 발생했습니다.')}")
            if result.get('action') not in ['clarify', 'reject', 'escalate']:
                print(f"      (Action: {result.get('action')}, Department: {result.get('department')})")


        except (KeyboardInterrupt, EOFError):
            print("\nAgent> 감사합니다. 안녕히 가세요.")
            break
        except Exception as e:
            print(f"오류가 발생했습니다: {e}")

if __name__ == "__main__":
    main()
