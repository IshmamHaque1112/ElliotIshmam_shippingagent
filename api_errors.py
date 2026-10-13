"""One place to turn Anthropic SDK exceptions into short, safe messages."""

from anthropic import APIConnectionError, APIError, APIStatusError


def api_error_message(error: APIError) -> str:
    """Return a concise message for the page or terminal (never includes secrets)."""
    if isinstance(error, APIConnectionError):
        return "Could not reach Anthropic. Check your internet connection and try again."

    if isinstance(error, APIStatusError):
        if error.status_code == 401:
            message = "Anthropic rejected the API key. Check that the local .env key is valid."
        elif error.status_code == 429:
            message = "Anthropic rate or usage limit reached. Check account limits before retrying."
        elif error.status_code == 402:
            message = "Anthropic reports a billing problem. Check the API account billing settings."
        else:
            message = f"Anthropic returned an API error (HTTP {error.status_code})."

        request_id = getattr(error, "request_id", None)
        if request_id:
            message += f" Request ID: {request_id}."
        return message

    return "Anthropic request failed. Check the API configuration and try again."


class MissingKeyError(RuntimeError):
    """Raised before any API request when no API key is configured."""
