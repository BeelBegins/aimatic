"""Shared client for calling NVIDIA Nemotron models via OpenRouter.

Configuration comes from site_config (or common_site_config.json, which
applies to every site on this bench) - never hardcode a key here:
    bench set-config -g openrouter_api_key "sk-or-..."
    bench set-config -g openrouter_nemotron_model "nvidia/nemotron-3-super-120b-a12b"
"""

from __future__ import annotations

import json

import frappe
import requests

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
DEFAULT_TIMEOUT = 60


class NemotronError(Exception):
    pass


def _get_api_key() -> str:
    api_key = frappe.conf.get("openrouter_api_key")
    if not api_key:
        raise NemotronError(
            "openrouter_api_key is not configured. Set it with: "
            "bench set-config -g openrouter_api_key <your-key>"
        )
    return api_key


def _get_model() -> str:
    return frappe.conf.get("openrouter_nemotron_model") or DEFAULT_MODEL


def get_chat_completion(
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    model: str | None = None,
) -> dict:
    """Multi-turn chat completion via OpenRouter, returning the raw assistant
    *message* dict (role/content/tool_calls) rather than just text - callers doing
    tool-calling need to inspect `tool_calls`, not only the final content. Raises
    NemotronError on any failure (missing key, network error, non-2xx response,
    unexpected response shape) rather than returning a partial/empty result."""
    payload = {
        "model": model or _get_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    try:
        response = requests.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {_get_api_key()}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as e:
        raise NemotronError(f"OpenRouter request failed: {e}")

    if response.status_code != 200:
        raise NemotronError(f"OpenRouter returned {response.status_code}: {response.text}")

    data = response.json()
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise NemotronError(f"Unexpected OpenRouter response shape: {data}")


def get_completion(
    prompt: str,
    system_prompt: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    model: str | None = None,
) -> str:
    """Single-turn, text-only convenience wrapper over get_chat_completion, for
    callers (e.g. api.py:ping) that don't need tool-calling."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    message = get_chat_completion(messages, temperature=temperature, max_tokens=max_tokens, model=model)
    content = message.get("content")
    if not content:
        raise NemotronError(f"OpenRouter returned no content: {message}")
    return content
