
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
    
    # For this PoC, we'll simulate being a single user, 'user-123',
    # who has an existing appointment.
    customer_id = "user-123"
    existing_appointment = next(
        (appt for appt in all_appointments if appt["customer_id"] == customer_id), 
        None
    )
    if existing_appointment:
        print(f"({customer_id}님, 기존 예약이 확인되었습니다.)")


    while True:
        try:
            user_message = input("나> ")
            if user_message.lower() == 'exit':
                print("Agent> 감사합니다. 안녕히 가세요.")
                break

            # Create a ticket from the user's message
            ticket = {
                "customer_id": customer_id,
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
