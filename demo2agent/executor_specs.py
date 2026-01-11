from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal

JSONType = Literal["string", "number", "path", "boolean"]


@dataclass(frozen=True)
class ExecutorSpec:
    """Machine-readable description of an executor's contract.

    The compiler/segmenter should treat this as source-of-truth:
    - which step types it supports
    - required/optional inputs
    - what outputs are realistic to produce/verify
    - operational limits (suggested)
    - examples
    """

    key: str  # e.g. "browser_use" | "desktop_ax" | "wait"
    supports_step_types: List[str]  # e.g. ["WEB"]

    # Inputs: names + primitive JSON types
    inputs_required: Dict[str, JSONType]
    inputs_optional: Dict[str, JSONType]

    # Guidance text for the compiler/segmenter
    inputs_notes: List[str]

    # Allowed primitive types for outputs_schema fields (compiler should stay within these)
    output_field_types: List[JSONType]
    outputs_notes: List[str]

    # Suggested limits for planning/segmentation (not enforced automatically)
    max_actions_hint: int
    max_seconds_hint: int

    # Short examples the compiler can imitate
    examples: List[Dict[str, Any]]


def get_executor_specs() -> List[ExecutorSpec]:
    """Return executor capability catalog.

    Keep this in sync with actual executors; the compiler and segmenter
    will be conditioned on these descriptions.
    """
    return [
        ExecutorSpec(
            key="browser_use",
            supports_step_types=["WEB"],
            inputs_required={"task": "string"},
            inputs_optional={},
            inputs_notes=[
                "Executes in a browser using a browsing agent; best for navigation + extraction.",
                "Change to English if required.",
                # browser-use docs guidance (your text, placed where it belongs: executor spec)
                "Task length: better suited for short, bounded tasks. Avoid long browsing sessions or workflows involving navigating dozens of pages.",
                "Complexity: effective for structured action sequences (filling forms, multi-step flows like shopping/job apps, data extraction into structured format).",
                # chunking/planning guidance
                "Prefer combining adjacent web micro-actions (search → open result → extract) into ONE bounded WEB step when feasible.",
                "If multiple WEB steps are required, prefer reusing the same browser session (do not force close/reopen between steps).",
                "Should always return structured outputs matching outputs_schema via a DONE.data object.",
            ],
            output_field_types=["string", "number", "path", "boolean"],
            outputs_notes=[
                "Can reliably return structured fields extracted from the web page.",
                "Prefer simple, flat outputs_schema unless necessary.",
                "Do not invent data. If uncertain, choose a more reliable source or keep the task scoped; avoid hallucinated fields.",
            ],
            max_actions_hint=15,
            max_seconds_hint=90,
            examples=[
                {
                    "type": "WEB",
                    "inputs": {
                        "task": "Search for {{ user_text }}, open the most relevant result, and extract required fields into outputs."
                    },
                    "outputs_schema": {"opened_url": "string", "page_title": "string"},
                },
                {
                    "type": "WEB",
                    "inputs": {"task": "Find latitude and longitude for {{ user_text }} from a reliable source."},
                    "outputs_schema": {"lat": "number", "lng": "number"},
                },
            ],
        ),
        ExecutorSpec(
            key="desktop_ax",
            supports_step_types=["DESKTOP"],
            inputs_required={"task": "string"},
            inputs_optional={"app": "string", "app_name": "string"},
            inputs_notes=[
                "Executes on macOS desktop via AppleScript/System Events plus limited AX actions.",
                "Best for launching apps, keyboard-driven UI actions, and pasting text blocks.",
                "Verification is constrained unless you add OCR/AX readback; avoid requiring unobservable outputs.",
                "Prefer combining adjacent desktop micro-actions (open app → create doc/note → paste/save) into ONE DESKTOP step when feasible.",
                "For tasks with title + body text, prefer paste_text convention: TITLE first line, blank line, then BODY.",
            ],
            output_field_types=["string", "number", "path", "boolean"],
            outputs_notes=[
                "Only output fields that are realistically observable (e.g., front_window_title or front_app only).",
                "Do not require postconditions on unobservable UI state unless you implement additional sensing.",
            ],
            max_actions_hint=12,
            max_seconds_hint=75,
            examples=[
                {
                    "type": "DESKTOP",
                    "inputs": {
                        "task": "Open an app via Spotlight and create a new document/note, then paste a formatted block."
                    },
                    "outputs_schema": {"front_app": "string"},
                }
            ],
        ),
        ExecutorSpec(
            key="wait",
            supports_step_types=["WAIT"],
            inputs_required={"seconds": "number"},
            inputs_optional={},
            inputs_notes=[
                "Pauses execution for a fixed duration.",
                "Used to absorb UI latency (page loads, app launch, animations).",
                "Does not interact with browser or desktop; should be used sparingly.",
            ],
            output_field_types=["number"],
            outputs_notes=[
                "Can only report how long it waited.",
                "No UI or web state is observed.",
            ],
            max_actions_hint=1,
            max_seconds_hint=30,
            examples=[
                {
                    "type": "WAIT",
                    "inputs": {"seconds": 1.5},
                    "outputs_schema": {"waited_seconds": "number"},
                }
            ],
        ),
    ]
