
def build_response(action: str, message: str, **kwargs) -> dict:
    """
    Constructs the final response dictionary.

    Args:
        action: The classified action (e.g., "reject", "escalate").
        message: The response message for the user.
        **kwargs: Any other key-value pairs to include in the response.

    Returns:
        A dictionary structured for the final output.
    """
    response = {
        "action": action,
        "response": message,
    }
    response.update(kwargs)
    return response
