from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from demo2agent.llm_json import JSONCallConfig, LLMJsonCaller
from demo2agent.models import Step, DesktopActionPlan

# PyObjC / AX (optional on some setups; keep imports inside try if needed)
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXUIElementSetAttributeValue,
    AXUIElementPerformAction,
    kAXFocusedUIElementAttribute,
    kAXPressAction,
)


# ---------------------------------------------------------------------
# AppleScript helpers (Spotlight, keystrokes, paste)
# ---------------------------------------------------------------------

def _osascript(script: str) -> str:
    
    script = script.encode("utf-8").decode("unicode_escape")
    print(script)
    p = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(f"osascript failed: {p.stderr.strip()}")
    return p.stdout.strip()


def _spotlight_open() -> None:
    # Cmd+Space
    _osascript('tell application "System Events" to key code 49 using {command down}')
    # allow spotlight UI to appear
    time.sleep(0.2)


def _spotlight_launch(query: str) -> None:
    # Type query then Enter
    _osascript(
        'tell application "System Events"\n'
        f'  keystroke {json.dumps(query)}\n'
        "  key code 36\n"  # Enter
        "end tell"
    )
    time.sleep(0.6)


def _activate_app(app: str) -> None:
    _osascript(f'tell application {json.dumps(app)} to activate')
    time.sleep(0.3)


def _keystroke(keys: List[str]) -> None:
    """
    AppleScript/System Events gotcha:
      keystroke "N" with {command down} becomes Cmd+Shift+N.
    Therefore: letter keys must be lowercase unless SHIFT is explicitly intended.

    keys example: ["CMD","N"] or ["CMD","SHIFT","n"]
    We'll normalize letters to lowercase unless user specified SHIFT.
    """
    mods: List[str] = []
    key: Optional[str] = None

    want_shift = any(k.upper() == "SHIFT" for k in keys)

    for k in keys:
        ku = k.upper()
        if ku in ("CMD", "COMMAND"):
            mods.append("command down")
        elif ku in ("CTRL", "CONTROL"):
            mods.append("control down")
        elif ku in ("ALT", "OPTION"):
            mods.append("option down")
        elif ku == "SHIFT":
            mods.append("shift down")
        else:
            # assume this is the "key"
            key = k

    if key is None:
        raise ValueError("keystroke requires a non-modifier key")

    # normalize single-letter alpha keys
    if len(key) == 1 and key.isalpha() and not want_shift:
        key = key.lower()

    mods_part = ""
    if mods:
        mods_part = " using {" + ", ".join(mods) + "}"

    # Use "keystroke" for characters; supports cmd combos well.
    _osascript(f'tell application "System Events" to keystroke {json.dumps(key)}{mods_part}')
    time.sleep(0.1)


def _type_text(text: str) -> None:
    # For short strings. For multiline, prefer paste_text.
    _osascript(f'tell application "System Events" to keystroke {json.dumps(text)}')
    time.sleep(0.1)


def _paste_text(text: str) -> None:
    # Put text on clipboard then paste (Cmd+V)
    _osascript(f'set the clipboard to {json.dumps(text)}')
    time.sleep(0.05)
    _keystroke(["CMD", "v"])
    time.sleep(0.15)


def _frontmost_app_name() -> str:
    return _osascript(
        'tell application "System Events" to get name of first application process whose frontmost is true'
    )


def _front_window_title(app_name: str) -> str:
    # Best-effort: some apps do not expose titles this way
    try:
        return _osascript(
            f'tell application "System Events" to tell process {json.dumps(app_name)} to get title of front window'
        )
    except Exception:
        return ""


# ---------------------------------------------------------------------
# AX helpers (focused element value/press)
# ---------------------------------------------------------------------

class AXError(RuntimeError):
    pass


def _focused_ui_element(app_name: str):
    # Find app pid then use AX
    pid = int(
        _osascript(
            f'tell application "System Events" to get unix id of first process whose name is {json.dumps(app_name)}'
        )
    )
    app = AXUIElementCreateApplication(pid)

    err, focused = AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute, None)
    if err != 0 or focused is None:
        return None
    return focused


def _ax_set_value(elem, value: str) -> None:
    err = AXUIElementSetAttributeValue(elem, "AXValue", value)
    if err != 0:
        raise AXError(f"AX set value failed: {err}")


def _ax_press(elem) -> None:
    err = AXUIElementPerformAction(elem, kAXPressAction)
    if err != 0:
        raise AXError(f"AX press failed: {err}")


# ---------------------------------------------------------------------
# LLM planner (JSON-only)
# ---------------------------------------------------------------------

@dataclass
class DesktopPlannerConfig:
    model: str = "gpt-5.2"
    max_actions: int = 14


PLANNER_SYSTEM = """You are a macOS desktop action planner.

You translate a natural language step task into a short JSON plan of primitive actions.

Return ONLY JSON:
{
  "app": "<optional app name to activate>" | null,
  "actions": [
    { "type": "spotlight_open", "sleep": 0.1 },
    { "type": "spotlight_launch", "query": "Notes", "sleep": 0.4 },
    { "type": "activate_app", "app": "Notes", "sleep": 0.2 },
    { "type": "keystroke", "keys": ["CMD","n"], "sleep": 0.2 },
    { "type": "paste_text", "text": "Title\\n\\nBody...", "sleep": 0.2 }
  ]
}

Allowed action types:
- spotlight_open
- spotlight_launch               (requires query)
- activate_app                   (requires app)
- keystroke                      (requires keys: [..])
- type_text                      (requires text)  # short strings only
- paste_text                     (requires text)  # preferred for multi-line content
- set_focused_value              (requires value)  # requires inputs.app or app hint
- press_focused                  (requires inputs.app or app hint

Rules:
- Keep the plan short (<= max_actions).
- Prefer keyboard shortcuts over clicking.
- For multi-line structured content, use paste_text.
- CRITICAL KEYBOARD RULE (AppleScript/System Events):
  AppleScript treats uppercase letters as if SHIFT is held.
  Therefore: ALWAYS output letter keys as lowercase single characters ("n", "v", "t", etc.)
  unless SHIFT is explicitly required (include "SHIFT" in keys).
- If the task specifies a title AND separate body content, produce paste_text that places:
  Title on the first line, then a blank line, then body text. (Generic rule; not app-specific.)
- Output JSON only. No markdown.
"""




def _validate_planner_output(data: Dict[str, Any], max_actions: int) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Planner output must be an object")
    actions = data.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("Planner output must include non-empty actions list")
    if len(actions) > max_actions:
        raise ValueError(f"Too many actions: {len(actions)} > {max_actions}")

    # Enforce lowercase letter keys unless SHIFT explicitly requested
    for a in actions:
        if not isinstance(a, dict):
            raise ValueError("Each action must be an object")
        t = a.get("type")
        if t == "keystroke":
            keys = a.get("keys")
            if not isinstance(keys, list) or not keys:
                raise ValueError("keystroke requires keys array")
            want_shift = any(str(k).upper() == "SHIFT" for k in keys)
            # find final non-modifier key
            for k in keys:
                ku = str(k).upper()
                if ku in ("CMD", "COMMAND", "CTRL", "CONTROL", "ALT", "OPTION", "SHIFT"):
                    continue
                if len(str(k)) == 1 and str(k).isalpha() and not want_shift and str(k) != str(k).lower():
                    raise ValueError("Letter keystroke keys must be lowercase unless SHIFT is explicitly included")

    return data


# ---------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------

class MacOSAXDesktopExecutor:
    """
    macOS desktop executor.

    Supported inputs:
    - step.inputs.actions: deterministic actions (list of dicts)
    - step.inputs.task: natural language; LLM planner generates actions (JSON)

    No workflow-specific hardcoding.
    """

    def __init__(self, planner_cfg: DesktopPlannerConfig = DesktopPlannerConfig()):
        self.planner_cfg = planner_cfg
        self.caller = LLMJsonCaller()

    def _plan_actions_from_task(self, step: Step, app_hint: Optional[str]) -> Dict[str, Any]:
        task = ""
        if isinstance(step.inputs, dict):
            task = str(step.inputs.get("task") or "").strip()
        if not task:
            task = str(step.goal or "").strip()

        payload = {
            "task": task,
            "app_hint": app_hint,
            "expected_outputs": step.outputs_schema or {},
            "postconditions": step.postconditions or [],
            "max_actions": self.planner_cfg.max_actions,
        }

        return self.caller.call_json(
            cfg=JSONCallConfig(model=self.planner_cfg.model, retries=2, strict_schema=True),
            system=PLANNER_SYSTEM,
            user_content=json.dumps(payload, ensure_ascii=False),
            schema_name="DesktopActionPlan",
            json_schema=DesktopActionPlan.model_json_schema(),
            validator=lambda d: _validate_planner_output(d, self.planner_cfg.max_actions),
            extra_repair_instructions=(
                "Return ONLY JSON. Keep action count <= max_actions. "
                "All letter keys in keystroke must be lowercase unless SHIFT is explicitly included."
            ),
        )

    def _make_outputs(self, step: Step) -> Dict[str, Any]:
        """
        Best-effort outputs. Only produce what we can reasonably observe.
        """
        out: Dict[str, Any] = {}
        schema = step.outputs_schema or {}
        front_app = _frontmost_app_name()
        for k, typ in schema.items():
            if k in ("front_app", "frontmost_app"):
                out[k] = front_app
            elif k in ("window_title", "front_window_title"):
                out[k] = _front_window_title(front_app)
            else:
                # we can't reliably read arbitrary UI state without OCR/AX traversal
                if typ == "number":
                    out[k] = 0.0
                elif typ == "boolean":
                    out[k] = False
                else:
                    out[k] = ""
        return out

    def run(self, step: Step) -> Dict[str, Any]:
        inputs = step.inputs or {}
        app_hint = None
        if isinstance(inputs, dict):
            app_hint = inputs.get("app") or inputs.get("app_name")

        actions: List[Dict[str, Any]] = []
        if isinstance(inputs, dict) and isinstance(inputs.get("actions"), list):
            actions = list(inputs["actions"])
        else:
            planned = self._plan_actions_from_task(step=step, app_hint=app_hint)
            if planned.get("app") and not app_hint:
                app_hint = str(planned["app"])
            actions = planned.get("actions") or []

        for a in actions:
            self._run_action(a, app_hint)

        return self._make_outputs(step)

    def _run_action(self, action: Dict[str, Any], app_name: Optional[str]) -> None:
        t = action.get("type")
        sleep_s = float(action.get("sleep", 0.05))

        if t == "spotlight_open":
            _spotlight_open()
            time.sleep(max(0.0, sleep_s))
            return

        if t == "spotlight_launch":
            q = action.get("query")
            if not q:
                raise ValueError("spotlight_launch requires query")
            _spotlight_launch(str(q))
            time.sleep(max(0.0, sleep_s))
            return

        if t == "activate_app":
            app = action.get("app") or app_name
            if not app:
                raise ValueError("activate_app requires app")
            _activate_app(str(app))
            time.sleep(max(0.0, sleep_s))
            return

        if t == "keystroke":
            keys = action.get("keys")
            if not isinstance(keys, list) or not keys:
                raise ValueError("keystroke requires keys array")
            _keystroke([str(k) for k in keys])
            time.sleep(max(0.0, sleep_s))
            return

        if t == "type_text":
            txt = action.get("text")
            if txt is None:
                raise ValueError("type_text requires text")
            _type_text(str(txt))
            time.sleep(max(0.0, sleep_s))
            return

        if t == "paste_text":
            txt = action.get("text")
            if txt is None:
                raise ValueError("paste_text requires text")
            _paste_text(str(txt))
            time.sleep(max(0.0, sleep_s))
            return

        if t == "set_focused_value":
            val = action.get("value")
            if val is None:
                raise ValueError("set_focused_value requires value")
            if not app_name:
                raise ValueError("set_focused_value requires an app (inputs.app or planner app)")
            focused = _focused_ui_element(app_name)
            if focused is None:
                raise AXError("No focused UI element")
            _ax_set_value(focused, str(val))
            time.sleep(max(0.0, sleep_s))
            return

        if t == "press_focused":
            if not app_name:
                raise ValueError("press_focused requires an app (inputs.app or planner app)")
            focused = _focused_ui_element(app_name)
            if focused is None:
                raise AXError("No focused UI element")
            _ax_press(focused)
            time.sleep(max(0.0, sleep_s))
            return

        raise ValueError(f"Unsupported action type: {t}")
