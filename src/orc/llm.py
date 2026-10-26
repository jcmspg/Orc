"""Thin wrapper around the Anthropic Messages API.

Credentials are resolved by the SDK itself: set ANTHROPIC_API_KEY, or run
`ant auth login`. Nothing is read from or written to disk here.
"""

from __future__ import annotations

from typing import Literal

from anthropic import Anthropic

DEFAULT_MODEL = "claude-opus-5-5"

Effort = Literal["low", "medium", "high", "xhigh", "max"]


def make_client() -> Anthropic:
    """Build a client using the SDK's default credential resolution."""
    return Anthropic()


def complete(
    client: Anthropic,
    prompt: str,
    *,
    system: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
    effort: Effort = "high",
) -> str:
    """Send one user prompt and return the concatenated text of the reply.

    Uses adaptive thinking; `effort` controls how much it thinks.
    """
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": prompt}],
    }
    if system is not None:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)

    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        raise RuntimeError(f"Model refused the request (category: {category})")

    return "".join(block.text for block in response.content if block.type == "text")
