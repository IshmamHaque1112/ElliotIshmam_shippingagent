"""Call Claude to classify one shipment that has already passed validation."""

import os
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

from prompt import build_classification_prompt


MODEL = "claude-haiku-5-5"


def classify_shipment(shipment: dict[str, Any]) -> str:
    """Return Claude's classification text for a validated shipment.

    Calling this function sends shipment data to Anthropic and may incur API cost.
    """
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is missing; check the local .env file.")

    client = Anthropic(api_key=api_key, max_retries=0, timeout=30.0)
    response = client.messages.create(
        model=MODEL,
        max_tokens=120,
        messages=[{"role": "user", "content": build_classification_prompt(shipment)}],
    )

    for block in response.content:
        if block.type == "text":
            return block.text.strip()

    raise RuntimeError("Claude returned no text classification.")
