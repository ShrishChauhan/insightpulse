"""Centralised LLM wrapper — all generation calls go through here, never directly in agents.

Provider is selected via config.PROVIDER ('groq', 'gemini', or 'anthropic').
Swap providers by changing PROVIDER in .env — no agent code changes needed.
"""

from __future__ import annotations

import json
import time
import re
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from core.db import SupabaseClient


class LLMClient:
    """Provider-agnostic LLM client with retry, cost logging, and JSON helpers."""

    def __init__(self, db: "SupabaseClient | None" = None) -> None:
        """Initialise the correct SDK based on config.PROVIDER."""
        self.provider = config.PROVIDER
        self.db = db
        self._gemini_client = None
        self._anthropic_client = None
        self._groq_client = None

        if self.provider == "groq":
            from groq import Groq  # type: ignore[import]
            self.model = config.GROQ_MODEL
            self._groq_client = Groq(api_key=config.GROQ_API_KEY)

        elif self.provider == "gemini":
            from google import genai  # type: ignore[import]
            self.model = config.GEMINI_MODEL
            self._gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)

        elif self.provider == "anthropic":
            import anthropic  # type: ignore[import]
            self.model = config.ANTHROPIC_MODEL
            self._anthropic_client = anthropic.Anthropic(
                api_key=config.ANTHROPIC_API_KEY
            )

        else:
            raise ValueError(
                f"Unknown PROVIDER '{self.provider}'. Must be 'groq', 'gemini', or 'anthropic'."
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 1000,
    ) -> str:
        """Generate a text response. Identical signature regardless of provider."""
        start = time.monotonic()
        tokens_used = 0
        text = ""

        for attempt in range(3):
            try:
                if self.provider == "groq":
                    text, tokens_used = self._groq_complete(
                        system, user, max_tokens
                    )
                elif self.provider == "gemini":
                    text, tokens_used = self._gemini_complete(
                        system, user, max_tokens
                    )
                else:
                    text, tokens_used = self._anthropic_complete(
                        system, user, max_tokens
                    )
                break  # success

            except Exception as exc:
                if self._is_rate_limit(exc) and attempt < 2:
                    wait = 2 ** (attempt + 1)  # 2s, 4s
                    time.sleep(wait)
                    continue
                raise

        duration_ms = int((time.monotonic() - start) * 1000)
        cost = self._estimate_cost(tokens_used)
        self._log_call(self.provider, self.model, tokens_used, duration_ms, cost)
        return text

    def complete_json(
        self,
        system: str,
        user: str,
        schema_hint: str = "",
    ) -> dict:
        """Generate a response and parse as JSON. Retries once on parse failure."""
        prompt = user
        if schema_hint:
            prompt = f"{user}\n\nExpected schema hint: {schema_hint}"

        raw = self.complete(system, prompt)
        try:
            return self._parse_json(raw)
        except (json.JSONDecodeError, ValueError):
            # Retry with stricter instruction appended
            retry_prompt = (
                f"{prompt}\n\nReturn only valid JSON, no markdown."
            )
            raw = self.complete(system, retry_prompt)
            return self._parse_json(raw)

    # ------------------------------------------------------------------
    # Provider-specific helpers
    # ------------------------------------------------------------------

    def _groq_complete(
        self,
        system: str,
        user: str,
        max_tokens: int,
    ) -> tuple[str, int]:
        """Call Llama via Groq SDK."""
        response = self._groq_client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
        return text, tokens

    def _gemini_complete(
        self,
        system: str,
        user: str,
        max_tokens: int,
    ) -> tuple[str, int]:
        """Call Gemini via google-genai SDK."""
        from google.genai import types  # type: ignore[import]

        response = self._gemini_client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )
        text = response.text or ""
        tokens = getattr(
            getattr(response, "usage_metadata", None), "total_token_count", 0
        ) or 0
        return text, tokens

    def _anthropic_complete(
        self,
        system: str,
        user: str,
        max_tokens: int,
    ) -> tuple[str, int]:
        """Call Claude via Anthropic SDK."""
        message = self._anthropic_client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = message.content[0].text if message.content else ""
        tokens = message.usage.input_tokens + message.usage.output_tokens
        return text, tokens

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    def _parse_json(self, raw: str) -> dict:
        """Strip markdown fences and parse JSON."""
        # Remove ```json ... ``` or ``` ... ``` fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip())
        return json.loads(cleaned)

    def _is_rate_limit(self, exc: Exception) -> bool:
        """Return True if the exception signals a rate-limit / quota error."""
        msg = str(exc).lower()
        return "429" in msg or "rate_limit" in msg or "resource_exhausted" in msg

    def _estimate_cost(self, tokens: int) -> float:
        """Rough cost estimate. Groq/Gemini free tier = $0; Haiku = $0.00025/1k."""
        if self.provider in ("groq", "gemini"):
            return 0.0
        # Haiku: $0.00025/1k input (output is 1.25x but we don't split here)
        return round(tokens / 1000 * 0.00025, 6)

    def _log_call(
        self,
        provider: str,
        model: str,
        tokens_used: int,
        duration_ms: int,
        cost_estimate: float,
    ) -> None:
        """Log LLM call to Supabase runs table via SupabaseClient."""
        if self.db is None:
            return
        try:
            self.db.log_run(
                agent_name="llm_client",
                status="success",
                input_summary=f"provider={provider} model={model}",
                output_summary=f"cost=${cost_estimate:.6f}",
                tokens_used=tokens_used,
                duration_ms=duration_ms,
            )
        except Exception:
            # Logging failure must never crash generation
            pass
