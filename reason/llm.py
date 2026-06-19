"""OpenRouter LLM client — OpenAI-compatible, structured JSON output.

Uses the openai SDK with base_url pointed at OpenRouter.  Model is
configurable (default: a mid-tier model that handles structured output well).

Run standalone:  python -m reason.llm "Explain TLB misses in 2 sentences"
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DEFAULT_MODEL = "deepseek/deepseek-v3.2"


def _client() -> OpenAI:
    key = os.environ.get("OPEN_ROUTER_TOKEN")
    if not key:
        raise SystemExit(
            "Set OPEN_ROUTER_TOKEN in your environment or .env file."
        )
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` wrappers that models sometimes add."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (with optional language tag)
        text = re.sub(r"^```\w*\s*\n?", "", text)
        # Remove closing fence
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
    max_retries: int = 1,
) -> Any:
    """Send a chat completion and parse the response as JSON.

    Strips code fences, validates JSON, and retries once on parse failure
    (appending the error to the conversation so the model can self-correct).
    """
    client = _client()
    attempt_messages = list(messages)

    for attempt in range(1 + max_retries):
        resp = client.chat.completions.create(
            model=model,
            messages=attempt_messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        if not resp.choices:
            raise ValueError("LLM returned empty choices list")
        raw = resp.choices[0].message.content or ""
        cleaned = _strip_fences(raw)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            if attempt < max_retries:
                attempt_messages.append({"role": "assistant", "content": raw})
                attempt_messages.append({
                    "role": "user",
                    "content": (
                        f"Your response was not valid JSON: {exc}. "
                        "Please reply with ONLY a valid JSON object."
                    ),
                })
            else:
                raise ValueError(
                    f"LLM did not return valid JSON after {1 + max_retries} "
                    f"attempts. Last response:\n{raw}"
                ) from exc


def chat_text(
    messages: list[dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.3,
) -> str:
    """Plain text chat completion (no JSON parsing)."""
    client = _client()
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    if not resp.choices:
        raise ValueError("LLM returned empty choices list")
    return resp.choices[0].message.content or ""


# ------------------------------------------------------------------
# CLI smoke test
# ------------------------------------------------------------------

if __name__ == "__main__":
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Say hello in JSON: {\"greeting\": \"...\"}"
    result = chat_json([{"role": "user", "content": prompt}])
    print(json.dumps(result, indent=2))
