"""The investigator: an Anthropic tool-use loop for exceptions only. Narrative output, never numbers."""

import json
import os
from typing import Any

import config
import fetch as fetch_module
import prompt
from api_errors import MissingKeyError
from mock_service import MockService


class AgentError(Exception):
    """The model never produced a usable submission within the call budget."""


def model_name() -> str:
    return os.environ.get("ANTHROPIC_MODEL") or config.ANTHROPIC_DEFAULT_MODEL


def make_client():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise MissingKeyError("ANTHROPIC_API_KEY is missing; set it in the environment or a local .env file.")
    from anthropic import Anthropic

    return Anthropic(api_key=key, max_retries=config.ANTHROPIC_MAX_RETRIES, timeout=config.ANTHROPIC_TIMEOUT_SECONDS)


def _blocks(response) -> list:
    return [b for b in response.content if getattr(b, "type", None) == "tool_use"]


def investigate(
    client, service: MockService, assessment: dict[str, Any], hint: str | None = None, sleep=None,
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Run the loop and return the raw submit_case_analysis input (validated later by case_file).

    If `usage` is given, token counts are added to it (used by the eval to compare models' cost).
    """
    shipment_id = assessment["shipment_id"]
    tool_specs = prompt.read_tool_specs() + [prompt.submit_tool_spec()]
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt.case_message(assessment, hint)}]
    seen_calls: set[str] = set()
    tool_calls = 0
    force_submit = False
    kwargs = {"sleep": sleep} if sleep else {}

    for _ in range(config.MAX_TOOL_CALLS_PER_CASE + 3):
        request: dict[str, Any] = dict(
            model=model_name(), max_tokens=4096, system=prompt.SYSTEM_PROMPT, tools=tool_specs, messages=messages
        )
        if force_submit:
            request["tool_choice"] = {"type": "tool", "name": prompt.SUBMIT_TOOL_NAME}
        response = client.messages.create(**request)
        if usage is not None and getattr(response, "usage", None) is not None:
            usage["input_tokens"] = usage.get("input_tokens", 0) + (getattr(response.usage, "input_tokens", 0) or 0)
            usage["output_tokens"] = usage.get("output_tokens", 0) + (getattr(response.usage, "output_tokens", 0) or 0)

        uses = _blocks(response)
        submitted = next((b for b in uses if b.name == prompt.SUBMIT_TOOL_NAME), None)
        if submitted is not None:
            return dict(submitted.input)
        if not uses:
            messages += [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": f"Call {prompt.SUBMIT_TOOL_NAME} to finish."},
            ]
            force_submit = True
            continue

        results = []
        for use in uses:
            key = f"{use.name}:{json.dumps(use.input, sort_keys=True)}"
            if tool_calls >= config.MAX_TOOL_CALLS_PER_CASE:
                content = {"error": "budget_exhausted", "message": f"Call {prompt.SUBMIT_TOOL_NAME} now."}
                force_submit = True
            elif key in seen_calls:
                content = {"error": "repeated_call", "message": f"Identical call already made. Call {prompt.SUBMIT_TOOL_NAME}."}
                force_submit = True
            elif str(use.input.get("shipment_id", "")).strip().upper() != shipment_id.upper():
                content = {"error": "wrong_shipment", "message": f"This case is only about {shipment_id}."}
            else:
                seen_calls.add(key)
                tool_calls += 1
                if usage is not None:
                    usage["tool_calls"] = usage.get("tool_calls", 0) + 1
                content = fetch_module.fetch(service, use.name, shipment_id, **kwargs).as_tool_content()
            results.append({"type": "tool_result", "tool_use_id": use.id, "content": json.dumps(content)})
        messages += [{"role": "assistant", "content": response.content}, {"role": "user", "content": results}]

    raise AgentError("no submit_case_analysis within the call budget")
