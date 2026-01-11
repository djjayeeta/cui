from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple

from pydantic import ValidationError

from .executor_specs import get_executor_specs
from .llm_json import JSONCallConfig, LLMJsonCaller
from .models import WorkflowSpec
from .util import iso_now, ensure_dir


def _executor_catalog_text() -> str:
    specs = get_executor_specs()
    lines = []
    for s in specs:
        lines.append(f"Executor '{s.key}': supports step types {s.supports_step_types}")
        lines.append(f"  Required inputs: {s.inputs_required}")
        if s.inputs_optional:
            lines.append(f"  Optional inputs: {s.inputs_optional}")
        for n in s.inputs_notes:
            lines.append(f"  Note: {n}")
        lines.append(f"  Allowed output field types: {s.output_field_types}")
        for n in s.outputs_notes:
            lines.append(f"  Output note: {n}")
        lines.append(f"  Suggested limits: max_actions<={s.max_actions_hint}, max_seconds<={s.max_seconds_hint}")
        if s.examples:
            lines.append("  Examples:")
            for ex in s.examples[:2]:
                lines.append(f"    {json.dumps(ex, ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines).strip()


SYSTEM = """You are a workflow compiler.

You MUST output ONLY a JSON object that validates against the provided WorkflowSpec JSON Schema.

You will be given (in the user message):
- workflow_spec_json_schema: JSON Schema for WorkflowSpec (source of truth)
- executor_catalog_text: executor capabilities/constraints (source of truth for executor_hint + realistic inputs/outputs)
- compile_input: preprocessed demo evidence (segments, keyframes, window titles, typed text, transcript, transcript_text, etc)
- workflow_name and created_at_iso

CRITICAL INPUT BINDING RULE:
- The workflow MUST declare a top-level input named "user_text" with type "string".
- At runtime, orchestrator provides exactly one user text input: inputs.user_text.

CRITICAL JINJA2 TEMPLATE RULE:
- Placeholders MUST use Jinja2 double braces: {{ ... }}.
- Valid: {{ user_text }}, {{ steps.step_01.some_field }}
- Invalid: { user_text }, ${user_text}, ${{user_text}}
- Only placeholders allowed:
  - {{ user_text }}
  - {{ steps.<step_id>.<field> }}

PLANNING RULES:
- Prefer fewer executor-aligned steps (typically 5–12 for a 3-minute demo).
- Combine adjacent micro-actions into one bounded executor task when feasible (per executor_catalog_text).
- Postconditions must be checkable from step outputs (do not require checks on unobservable fields).
- evidence must be either a single object or null (never a list).
- Return JSON only (no markdown, no commentary, no extra top-level keys).
"""


_SINGLE_BRACE_TOKEN = re.compile(
    r"""
    (?<!\{)
    \{
    \s*
    (user_text|steps\.[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)+)
    \s*
    \}
    (?!\})
    """,
    re.VERBOSE,
)


def _normalize_templates_in_str(s: str) -> str:
    def repl(m: re.Match) -> str:
        inner = m.group(1).strip()
        return "{{ " + inner + " }}"
    return _SINGLE_BRACE_TOKEN.sub(repl, s)


def _walk_and_normalize(obj: Any) -> Any:
    if isinstance(obj, str):
        return _normalize_templates_in_str(obj)
    if isinstance(obj, list):
        return [_walk_and_normalize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _walk_and_normalize(v) for k, v in obj.items()}
    return obj


class Compiler:
    def __init__(self, model: str = "gpt-5.2"):
        self.model = model
        self.caller = LLMJsonCaller()

    def _validate_workflow(self, data: Dict[str, Any]) -> WorkflowSpec:
        # Final deterministic normalization
        data = _walk_and_normalize(data)
        spec = WorkflowSpec.model_validate(data)
        if not spec.created_at_iso or spec.created_at_iso == "1970-01-01T00:00:00Z":
            spec.created_at_iso = iso_now()
        if not spec.name:
            spec.name = "workflow"
        return spec

    def compile_from_preprocessed(
        self,
        compile_input: Dict[str, Any],
        workflow_name: str,
        debug_dir: str | None = None,
    ) -> WorkflowSpec:
        if debug_dir:
            ensure_dir(__import__("pathlib").Path(debug_dir))

        user_payload: Dict[str, Any] = {
            "workflow_name": workflow_name,
            "created_at_iso": iso_now(),
            "workflow_spec_json_schema": WorkflowSpec.model_json_schema(),
            "executor_catalog_text": _executor_catalog_text(),
            "compile_input": compile_input,
        }

        def validator(parsed: Dict[str, Any]) -> WorkflowSpec:
            try:
                spec = self._validate_workflow(parsed)
                spec.name = workflow_name
                return spec
            except ValidationError as e:
                # Raise to trigger retry with detailed error
                raise e
        spec: WorkflowSpec = self.caller.call_json(
            cfg=JSONCallConfig(model=self.model, retries=2, strict_schema=True),
            system=SYSTEM,
            user_content=json.dumps(user_payload, ensure_ascii=False),
            validator=validator,
            extra_repair_instructions=(
                "Ensure Jinja2 placeholders use double braces {{ ... }} only. "
                "Do not use single braces { ... }."
            ),
        )

        return spec
