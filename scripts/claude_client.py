"""
Thin adapter over the Anthropic Python SDK.

Why an adapter:
- Phases 2-4 should not know whether they're calling the SDK, direct HTTP,
  or a mock. This file is the single seam.
- Lets us swap models / providers / mocks without touching pipeline code.

Retries are delegated to the SDK: it already retries 408/409/429/5xx and
connection errors with exponential backoff and honours `retry-after`.
A 400 (bad request) or 401/403 is never retried — repeating it only
burns time — and surfaces immediately with a readable message.

Usage:
    from scripts.claude_client import ClaudeClient
    client = ClaudeClient(model="claude-sonnet-5-5")
    text = client.complete(
        system="You are a careful distiller.",
        user="...transcript...",
        cache_system=True,
    )

Environment:
    ANTHROPIC_API_KEY must be set (or another credential source the SDK
    resolves, such as an `ant auth login` profile). The adapter never
    logs the key.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional


@dataclass
class CompletionResult:
    text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    model: str
    stop_reason: str = ""


class ClaudeClient:
    def __init__(
        self,
        model: str = "claude-sonnet-5-5",
        max_tokens: int = 4096,
        max_retries: int = 4,
    ) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package not installed. "
                "Run: pip install anthropic"
            ) from exc

        # The SDK resolves ANTHROPIC_API_KEY itself (and other credential
        # sources). A missing credential raises AuthenticationError on the
        # first call, which we translate below.
        self._client = Anthropic(max_retries=max_retries)
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries

    def complete(
        self,
        *,
        system: str,
        user: str,
        cache_system: bool = True,
        max_tokens: Optional[int] = None,
    ) -> CompletionResult:
        """One round-trip. Transient errors are retried by the SDK."""
        import anthropic

        if cache_system:
            system_param = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_param = system

        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens or self.max_tokens,
                system=system_param,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AuthenticationError as exc:
            raise RuntimeError(
                "Anthropic rejected the credentials. Export ANTHROPIC_API_KEY "
                "(or run `ant auth login`) before running phases 2-4."
            ) from exc
        except anthropic.RateLimitError as exc:
            raise RuntimeError(
                f"Rate limited by Anthropic after {self.max_retries} retries. "
                "Lower --concurrency or wait a minute and re-run (completed "
                "videos are skipped)."
            ) from exc
        except anthropic.APIStatusError as exc:
            raise RuntimeError(
                f"Anthropic API error {exc.status_code}: {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise RuntimeError(
                f"Could not reach the Anthropic API after {self.max_retries} "
                "retries (network error)."
            ) from exc

        stop_reason = getattr(resp, "stop_reason", "") or ""
        if stop_reason == "refusal":
            raise RuntimeError("Claude declined this request (stop_reason=refusal).")
        text = "".join(
            getattr(block, "text", "") for block in resp.content
            if getattr(block, "type", "") == "text"
        )
        if stop_reason == "max_tokens":
            print(
                f"  warn: response hit max_tokens={max_tokens or self.max_tokens}; "
                "output may be truncated.",
                file=sys.stderr,
            )

        usage = resp.usage
        return CompletionResult(
            text=text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            model=self.model,
            stop_reason=stop_reason,
        )
