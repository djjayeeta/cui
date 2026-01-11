from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple, Type, Union, Annotated

from pydantic import BaseModel, Field, create_model, field_validator, ConfigDict

from typing import  Set
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, model_validator


Json = Dict[str, Any]
ValueType = Literal["string", "number", "path", "boolean"]
StepType = Literal["WEB", "DESKTOP", "WAIT"]
ExecutorHint = Literal["browser_use", "desktop_ax", "auto"]

EventType = Literal["mouse_click", "key_down", "key_up", "text", "window_title", "marker"]

class RawEvent(BaseModel):
    t: float
    type: EventType
    data: Dict[str, Any] = Field(default_factory=dict)

class DemoTrace(BaseModel):
    name: str
    started_at_iso: str
    screen_size: List[int]  # [w,h]
    events: List[RawEvent]
    audio_path: Optional[str] = None
    transcript: Optional[List[Dict[str, Any]]] = None  # [{t0,t1,text}]
    transcript_file_path: Optional[str] = None

ValueType = Literal["string", "number", "path", "boolean"]


class StepPolicy(BaseModel):
    """
    Execution limits. Keep defaults sane and let compiler override.
    """
    model_config = ConfigDict(extra="forbid")
    max_actions: int = 12
    max_seconds: int = 60
    retries: int = 0


class StepEvidence(BaseModel):
    """
    Debug-only evidence for how a step was inferred from the demo.
    """
    model_config = ConfigDict(extra="forbid")

    t: float
    keyframe_path: Optional[str] = None
    context_crop_path: Optional[str] = None
    target_crop_path: Optional[str] = None

    # NOTE: OpenAI schema subset struggles with tuple schemas; keep runtime type as tuple.
    click_xy: Optional[Tuple[int, int]] = None

    window_title: Optional[str] = None
    typed_text_nearby: Optional[str] = None
    transcript_nearby: Optional[str] = None

    @field_validator("t", mode="before")
    @classmethod
    def _coerce_evidence_t(cls, v: Any) -> float:
        """
        Prevent weird values like "segment" ending up in numeric fields.
        """
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except Exception as e:
                raise ValueError(f"StepEvidence.t must be numeric, got string={v!r}") from e
        raise ValueError(f"StepEvidence.t must be numeric, got {type(v)}")


ExecutorHint = Literal["browser_use", "desktop_ax", "auto"]
StepType = Literal["WEB", "DESKTOP", "WAIT"]


class Step(BaseModel):
    """
    A single workflow step.

    inputs:
      - step-specific config and templated bindings, e.g. {"query": "{{ user_text }}"}
    outputs_schema:
      - declares keys and primitive types the executor should return (best-effort)
    """
    model_config = ConfigDict(extra="forbid")

    id: str
    type: StepType
    goal: str

    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs_schema: Dict[str, ValueType] = Field(default_factory=dict)

    postconditions: List[Dict[str, Any]] = Field(default_factory=list)
    policy: StepPolicy = Field(default_factory=StepPolicy)

    executor_hint: ExecutorHint = "auto"

    evidence: Optional[StepEvidence] = None
    fallbacks: List["Step"] = Field(default_factory=list)

    def output_model(self) -> Type[BaseModel]:
        """
        Create a pydantic model matching outputs_schema so executors can return typed outputs.
        """
        return make_output_model(self.id, self.outputs_schema)


class WorkflowSpec(BaseModel):
    """
    A workflow is:
    - name, created_at
    - input vars (orchestrator will supply user_text at run time)
    - ordered steps
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    created_at_iso: str

    # Your runtime interface: orchestrator will pass {"user_text": "..."}.
    # Keep inputs flexible so compiler can add more later if you want.
    inputs: Dict[str, ValueType] = Field(default_factory=dict)

    steps: List[Step]

_TYPE_MAP: Dict[str, tuple] = {
    "string": (str, Field(default="")),
    "number": (float, Field(default=0.0)),
    "path": (str, Field(default="")),
    "boolean": (bool, Field(default=False)),
}

def make_output_model(step_id: str, outputs_schema: Dict[str, ValueType]) -> Type[BaseModel]:
    """
    Build a Pydantic model class dynamically.

    For empty outputs_schema, return a minimal model with ok="" so verification can still work.
    """
    if not outputs_schema:
        return create_model(f"OutputModel_{step_id}", ok=(str, Field(default="")))

    fields: Dict[str, tuple] = {}
    for name, t in outputs_schema.items():
        if t in _TYPE_MAP:
            fields[name] = _TYPE_MAP[t]
        else:
            # be permissive; treat unknown as string
            fields[name] = (str, Field(default=""))
    return create_model(f"OutputModel_{step_id}", **fields)

ActionType = Literal[
    "spotlight_open",
    "spotlight_launch",
    "activate_app",
    "keystroke",
    "type_text",
    "paste_text",
    "set_focused_value",
    "press_focused",
]


class DesktopActionItem(BaseModel):
    """
    OpenAI json_schema-safe action item:
    - No oneOf/anyOf branching
    - All properties are REQUIRED but may be null
    - Conditional requirements enforced in Python validator
    """
    model_config = ConfigDict(extra="forbid")

    # MUST be required (OpenAI constraint), but allow null
    type: ActionType = Field(...)

    sleep: Optional[float] = Field(..., description="Seconds to sleep after action; null allowed.")
    query: Optional[str] = Field(..., description="For spotlight_launch; else null.")
    app: Optional[str] = Field(..., description="For activate_app; else null.")
    keys: Optional[List[str]] = Field(..., description="For keystroke; else null.")
    text: Optional[str] = Field(..., description="For type_text/paste_text; else null.")
    value: Optional[str] = Field(..., description="For set_focused_value; else null.")

    @model_validator(mode="after")
    def _enforce_required_fields_by_type(self) -> "DesktopActionItem":
        t = self.type

        def req(field_name: str):
            v = getattr(self, field_name)
            if v is None:
                raise ValueError(f"{t}: '{field_name}' must be non-null")
            if field_name == "keys":
                if not isinstance(v, list) or len(v) == 0:
                    raise ValueError(f"{t}: 'keys' must be a non-empty array")

        if t == "spotlight_open":
            # no required extras
            return self
        if t == "spotlight_launch":
            req("query")
            return self
        if t == "activate_app":
            req("app")
            return self
        if t == "keystroke":
            req("keys")
            return self
        if t in ("type_text", "paste_text"):
            req("text")
            return self
        if t == "set_focused_value":
            req("value")
            return self
        if t == "press_focused":
            return self

        return self


class DesktopActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # required-but-nullable to satisfy OpenAI “required includes all properties” rule
    app: Optional[str] = Field(..., description="Optional app name to activate; null allowed.")
    actions: List[DesktopActionItem] = Field(..., min_length=1)
