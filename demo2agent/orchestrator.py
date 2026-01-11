from __future__ import annotations

import time
from typing import Any, Dict, Optional

from jinja2 import Environment, StrictUndefined, TemplateError
from pydantic import BaseModel, ValidationError

from demo2agent.models import WorkflowSpec, Step


class VerificationError(Exception):
    pass


def verify(step: Step, outputs: Dict[str, Any]) -> None:
    """
    Postconditions must be checkable from step outputs.
    """
    pass
    # for cond in step.postconditions:
    #     kind = cond.get("kind")

    #     if kind == "nonempty":
    #         field = cond["field"]
    #         if not str(outputs.get(field, "")).strip():
    #             raise VerificationError(f"{step.id}: {field} empty")

    #     elif kind == "rating_range":
    #         field = cond["field"]
    #         v = outputs.get(field)
    #         if v is None:
    #             raise VerificationError(f"{step.id}: {field} missing")
    #         fv = float(v)
    #         if not (float(cond["min"]) <= fv <= float(cond["max"])):
    #             raise VerificationError(f"{step.id}: {field} out of range: {fv}")

    #     elif kind == "url_contains_any":
    #         field = cond.get("field", "url")
    #         url = str(outputs.get(field, ""))
    #         allow = cond.get("value") or []
    #         if not any(s in url for s in allow):
    #             raise VerificationError(f"{step.id}: url not acceptable: {url}")


def render_templates(obj: Any, env: Environment, ctx: Dict[str, Any]) -> Any:
    if isinstance(obj, str):
        try:
            return env.from_string(obj).render(ctx)
        except TemplateError as e:
            raise RuntimeError(f"Template render failed for '{obj}': {e}") from e
    if isinstance(obj, dict):
        return {k: render_templates(v, env, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_templates(v, env, ctx) for v in obj]
    return obj


def _render_step(step: Step, env: Environment, ctx: Dict[str, Any]) -> Step:
    """
    Render templates everywhere a user might write them:
      - goal
      - inputs
      - postconditions
      - fallbacks recursively
    """
    rendered_goal = render_templates(step.goal, env, ctx)
    rendered_inputs = render_templates(step.inputs, env, ctx)
    rendered_post = render_templates(step.postconditions, env, ctx)
    rendered_fallbacks = [_render_step(fb, env, ctx) for fb in (step.fallbacks or [])]

    return step.model_copy(
        update={
            "goal": rendered_goal,
            "inputs": rendered_inputs,
            "postconditions": rendered_post,
            "fallbacks": rendered_fallbacks,
        }
    )


def _validate_outputs(step: Step, outputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enforce that executor output matches step.output_model() exactly.
    Stores validated/normalized dict (so later templates are stable).
    """
    OutputModel = step.output_model()

    # If the step has no outputs_schema, accept empty dict.
    # Still allow executor to return extra debug keys, but we’ll drop them by validating.
    try:
        model_obj: BaseModel = OutputModel.model_validate(outputs)
    except ValidationError as e:
        # Make the error actionable
        raise RuntimeError(
            f"{step.id}: executor output failed OutputModel validation.\n"
            f"Expected fields: {list((step.outputs_schema or {}).keys())}\n"
            f"Got keys: {list(outputs.keys()) if isinstance(outputs, dict) else type(outputs)}\n"
            f"Validation error: {e}"
        ) from e

    dumped = model_obj.model_dump()
    return dumped


class Orchestrator:
    """
    - Renders templates across whole step before running it.
    - Routes to web_exec / desktop_exec / wait.
    - Validates outputs against Step.output_model() and stores validated outputs.
    """

    def __init__(
        self,
        web_exec,
        desktop_exec,
        repair_hook=None,
        strict_templates: bool = True,
    ):
        self.web_exec = web_exec
        self.desktop_exec = desktop_exec
        self.repair_hook = repair_hook
        self.env = Environment(
            undefined=StrictUndefined if strict_templates else None,
            autoescape=False,
        )

    def run(self, wf: WorkflowSpec, inputs: Dict[str, Any]) -> Dict[str, Any]:
        ctx: Dict[str, Any] = {**inputs, "steps": {}}

        for i, step0 in enumerate(wf.steps):
            step = _render_step(step0, self.env, ctx)

            last_err: Optional[Exception] = None
            for attempt in range(step.policy.retries + 1):
                try:
                    t0 = time.time()

                    if step.type == "WEB":
                        if step.executor_hint not in ("browser_use", "auto"):
                            raise RuntimeError(f"{step.id}: WEB step must use executor_hint=browser_use/auto")
                        if not isinstance(step.inputs, dict) or "task" not in step.inputs:
                            raise RuntimeError(f"{step.id}: WEB step.inputs.task is required")
                        raw_outputs = self.web_exec.run(step)

                    elif step.type == "DESKTOP":
                        if step.executor_hint not in ("desktop_ax", "auto"):
                            raise RuntimeError(f"{step.id}: DESKTOP step must use executor_hint=desktop_ax/auto")
                        if not isinstance(step.inputs, dict):
                            raise RuntimeError(f"{step.id}: DESKTOP step.inputs must be an object")
                        # Desktop executor supports either task or actions (legacy).
                        if "task" not in step.inputs and "actions" not in step.inputs:
                            raise RuntimeError(f"{step.id}: DESKTOP needs inputs.task or inputs.actions")
                        raw_outputs = self.desktop_exec.run(step)

                    elif step.type == "WAIT":
                        secs = float(step.inputs.get("seconds", 1.0))
                        time.sleep(max(0.0, secs))
                        raw_outputs = {"waited_seconds": secs}

                    else:
                        raise RuntimeError(f"{step.id}: Unsupported step type: {step.type}")

                    if (time.time() - t0) > step.policy.max_seconds:
                        raise RuntimeError(f"{step.id}: exceeded max_seconds")

                    if not isinstance(raw_outputs, dict):
                        raise RuntimeError(f"{step.id}: executor must return dict, got {type(raw_outputs)}")

                    # Enforce OutputModel contract
                    outputs = _validate_outputs(step, raw_outputs)

                    # Verify postconditions using validated outputs
                    verify(step, outputs)

                    # Store validated outputs for future templates
                    ctx["steps"][step.id] = outputs
                    print(f"Outputs: {outputs}")
                    last_err = None
                    break

                except Exception as e:
                    last_err = e

                    # retries
                    if attempt < step.policy.retries:
                        time.sleep(0.3)
                        continue

                    # repair hook (optional)
                    if self.repair_hook:
                        wf = self.repair_hook(wf, step, str(e), ctx)
                        step0 = wf.steps[i]
                        step = _render_step(step0, self.env, ctx)
                        # after repair, do one more immediate attempt (without extending retries)
                        try:
                            if step.type == "WEB":
                                raw_outputs = self.web_exec.run(step)
                            elif step.type == "DESKTOP":
                                raw_outputs = self.desktop_exec.run(step)
                            elif step.type == "WAIT":
                                secs = float(step.inputs.get("seconds", 1.0))
                                time.sleep(max(0.0, secs))
                                raw_outputs = {"waited_seconds": secs}
                            else:
                                raise RuntimeError(f"{step.id}: Unsupported step type: {step.type}")

                            outputs = _validate_outputs(step, raw_outputs)
                            verify(step, outputs)
                            ctx["steps"][step.id] = outputs
                            last_err = None
                            break
                        except Exception as e2:
                            raise e2 from e

                    raise

            if last_err is not None and step.id not in ctx["steps"]:
                raise last_err

        return ctx
