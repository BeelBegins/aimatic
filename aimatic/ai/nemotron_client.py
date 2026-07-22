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


def _get_settings():
    return frappe.get_cached_doc("AI Integration Settings")


def _check_enabled() -> None:
    if not _get_settings().enabled:
        raise NemotronError(
            "The AI Assistant is currently disabled. An administrator can "
            "re-enable it from AI Integration Settings."
        )


def _get_model() -> str:
    # Highest priority: the live, frontend-editable setting (AI Integration
    # Settings), so an admin can switch models without shell access. Falls
    # back to the bench-wide site config key, then a hardcoded default -
    # unchanged from before this field existed, so a site that never touches
    # the new setting keeps working exactly as it did.
    return _get_settings().model or frappe.conf.get("openrouter_nemotron_model") or DEFAULT_MODEL


def get_chat_completion(
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    model: str | None = None,
    reasoning: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    return_metadata: bool = False,
) -> dict:
    """Multi-turn chat completion via OpenRouter, returning the raw assistant
    *message* dict (role/content/tool_calls) rather than just text - callers doing
    tool-calling need to inspect `tool_calls`, not only the final content. Raises
    NemotronError on any failure (missing key, network error, non-2xx response,
    unexpected response shape) rather than returning a partial/empty result."""
    _check_enabled()
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
    if reasoning is not None:
        payload["reasoning"] = reasoning

    try:
        response = requests.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {_get_api_key()}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise NemotronError(f"OpenRouter request failed: {e}")

    if response.status_code != 200:
        raise NemotronError(f"OpenRouter returned {response.status_code}: {response.text}")

    data = response.json()
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise NemotronError(f"Unexpected OpenRouter response shape: {data}")
    if return_metadata:
        return {
            "message": message,
            "usage": data.get("usage") or {},
            "provider": data.get("provider") or "",
        }
    return message


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
