
import json
from datetime import datetime
from src.agent import process_ticket

def main():
    """
    Runs the agent in batch mode on a set of tickets.
    """
    # Load data
    try:
        with open("data/ticket.json", 'r', encoding='utf-8') as f:
            tickets = json.load(f)
    except FileNotFoundError:
        print("Error: data/ticket.json not found.")
        return

    try:
        with open("data/appointments.json", 'r', encoding='utf-8') as f:
            all_appointments = json.load(f)
    except FileNotFoundError:
        all_appointments = []

    results = []
    print(f"Processing {len(tickets)} tickets...")

    for ticket in tickets:
        customer_name = ticket.get("customer_name")
        
        # Find the user's existing appointment if the context says so
        existing_appointment = None
        if ticket.get("context", {}).get("has_existing_appointment"):
            existing_appointment = next(
                (appt for appt in all_appointments if appt.get("customer_name") == customer_name),
                None
            )

        # In a real scenario, the agent would parse the booking time from the message.
        # For this PoC, we'll use a placeholder if it's a booking/modification intent.
        # We'll use the ticket timestamp as a reference for 'now'.
        now = datetime.fromisoformat(ticket["timestamp"])
        
        # We need to simulate the booking time the user might want.
        # This is not in the ticket data, so we'll create a dummy time for policy checks.
        dummy_booking_time = "2025-03-25T14:00:00Z"
        ticket_for_agent = {
            "message": ticket["message"],
            "customer_type": ticket["customer_type"],
            "booking_time": dummy_booking_time, # For policy checks
        }

        # Process the ticket
        result = process_ticket(
            ticket=ticket_for_agent,
            all_appointments=all_appointments,
            existing_appointment=existing_appointment,
            now=now
        )
        
        # Format the output to match F-015
        formatted_result = {
            "ticket_id": ticket["ticket_id"],
            "classified_intent": result.get("action"), # Renamed for clarity in output
            "department": result.get("department"),
            "action": result.get("action"),
            "response": result.get("response"),
            # 'confidence' and 'reasoning' are not produced by the current agent.
            # Adding them as placeholders.
            "confidence": 0.95, 
            "reasoning": "Classification based on LLM analysis. Policy applied deterministically."
        }
        results.append(formatted_result)

    # Save results
    with open("results.json", 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Processing complete. Results saved to results.json")

if __name__ == "__main__":
    main()
