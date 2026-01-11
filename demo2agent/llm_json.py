from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from openai import OpenAI

JsonDict = Dict[str, Any]
ContentPart = Dict[str, Any]
UserContent = Union[str, Sequence[ContentPart]]


class JSONGuardrailError(RuntimeError):
    pass


def _as_input(system: str, user_content: UserContent) -> List[Dict[str, Any]]:
    """
    Build Responses API input messages.
    user_content can be:
      - a plain string
      - a list of content parts [{"type":"input_text",...}, {"type":"input_image",...}]
    """
    if isinstance(user_content, str):
        user_msg = {"role": "user", "content": user_content}
    else:
        user_msg = {"role": "user", "content": list(user_content)}
    return [{"role": "system", "content": system}, user_msg]


@dataclass
class JSONCallConfig:
    model: str
    retries: int = 2  # retry twice on validation failure
    strict_schema: bool = True

    # Optional tuning knobs (match your request structure style)
    max_output_tokens: Optional[int] = None
    top_p: Optional[float] = None
    store: Optional[bool] = None


class LLMJsonCaller:
    """
    JSON guardrail wrapper for OpenAI Responses API (SDK 2.x).

    Uses the request structure:
      responses.create(
        model=...,
        input=[...],
        text={
          "format": {
            "type": "json_schema",
            "name": "...",
            "strict": true,
            "schema": {...}
          }
        }
      )

    Guardrails:
    - validates parsed json via validator
    - on failure retries twice with validation error and previous output
    """

    def __init__(self, client: Optional[OpenAI] = None):
        self.client = client or OpenAI()

    def _create_with_json_schema(
        self,
        *,
        cfg: JSONCallConfig,
        system: str,
        user_content: UserContent,
        schema_name: str | None = None,
        schema: JsonDict | None = None,
    ):
        kwargs: Dict[str, Any] = {
            "model": cfg.model,
            "input": _as_input(system, user_content)
        }
        if schema:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": bool(cfg.strict_schema),
                    "schema": schema,
                }
            }

        # Optional args
        if cfg.max_output_tokens is not None:
            kwargs["max_output_tokens"] = int(cfg.max_output_tokens)
        if cfg.top_p is not None:
            kwargs["top_p"] = float(cfg.top_p)
        if cfg.store is not None:
            kwargs["store"] = bool(cfg.store)

        return self.client.responses.create(**kwargs)

    def call_json(
        self,
        *,
        cfg: JSONCallConfig,
        system: str,
        user_content: UserContent,
        schema_name: str | None = None,
        json_schema: JsonDict | None = None,
        validator: Callable[[JsonDict], Any],
        extra_repair_instructions: Optional[str] = None,
    ) -> Any:
        last_err: Optional[Exception] = None
        last_text: Optional[str] = None

        for attempt in range(cfg.retries + 1):
            repair_prefix = ""
            if attempt > 0:
                repair_prefix = (
                    "Your previous output failed validation.\n"
                    f"Validation error:\n{str(last_err)}\n\n"
                    "Return ONLY corrected JSON that matches the schema.\n"
                )
                if extra_repair_instructions:
                    repair_prefix += f"\nExtra constraints:\n{extra_repair_instructions}\n"
                if last_text:
                    repair_prefix += f"\nPrevious invalid output:\n{last_text}\n"

            # Prepend repair instructions
            if isinstance(user_content, str):
                uc: UserContent = repair_prefix + user_content
            else:
                parts: List[ContentPart] = []
                if repair_prefix:
                    parts.append({"type": "input_text", "text": repair_prefix})
                parts.extend(list(user_content))
                uc = parts

            resp = self._create_with_json_schema(
                cfg=cfg,
                system=system,
                user_content=uc,
                schema_name=schema_name,
                schema=json_schema,
            )

            # Prefer parsed output if SDK provides it (may be None in some builds)
            parsed = getattr(resp, "output_parsed", None)
            last_text = (getattr(resp, "output_text", "") or "").strip()

            if parsed is None:
                # Fallback: parse output_text
                try:
                    parsed = json.loads(last_text)
                except Exception as e:
                    last_err = JSONGuardrailError(f"JSON parse failed: {e}")
                    continue

            if not isinstance(parsed, dict):
                last_err = JSONGuardrailError(f"Expected JSON object, got {type(parsed)}")
                continue

            try:
                return validator(parsed)
            except Exception as e:
                last_err = e
                continue

        raise JSONGuardrailError(
            f"Failed to produce valid JSON after {cfg.retries + 1} attempts. Last error: {last_err}"
        )
