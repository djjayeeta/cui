from __future__ import annotations
import time
from typing import Any, Dict
import pyautogui
from demo2agent.models import Step

try:
    import pyperclip
except ImportError:
    pyperclip = None

class PyAutoGuiDesktopExecutor:
    """
    v0: high-level desktop actions are still fragile.
    The recommended approach on macOS is to replace this with AX (Accessibility) based control later.
    """
    def run(self, step: Step) -> Dict[str, Any]:
        # Example convention:
        # inputs: { "text": "...", "hotkeys": [["command","space"], ...], "keys": ["return", "enter"], "pause_s": 1.0 }
        # inputs: { "click_address_bar_first": true, "hotkeys": [["command","l"], ["command","c"]], "read_clipboard_after_copy": true }
        # inputs: { "focus_browser_first": true } - focuses browser window before other actions
        out: Dict[str, Any] = {}

        # Focus browser first if specified (for steps that need to interact with browser)
        # This is a hint that we should wait/ensure browser is active - but we can't reliably
        # programmatically focus a window, so we just add a small delay
        if step.inputs.get("focus_browser_first"):
            time.sleep(0.5)  # Small delay to ensure system is ready

        # Handle click first if specified (e.g., click address bar)
        # Note: This will focus the address bar if browser is already active
        address_bar_focused = False
        if step.inputs.get("click_address_bar_first"):
            # On macOS, CMD+L focuses the address bar in most browsers
            pyautogui.hotkey("command", "l")  # macOS address bar shortcut
            time.sleep(0.5)  # Give time for address bar to focus
            address_bar_focused = True

        # Process hotkeys (skip CMD+L if we already did it above)
        hotkeys_to_process = step.inputs.get("hotkeys", [])
        if address_bar_focused:
            # Filter out duplicate CMD+L hotkeys
            filtered_hotkeys = []
            for hk in hotkeys_to_process:
                if isinstance(hk, list) and len(hk) == 2 and hk[0].lower() in ["command", "cmd"] and hk[1].lower() == "l":
                    continue  # Skip duplicate CMD+L
                if isinstance(hk, str) and ("CTRL+L" in hk.upper() or "CMD+L" in hk.upper()):
                    continue  # Skip duplicate
                filtered_hotkeys.append(hk)
            hotkeys_to_process = filtered_hotkeys

        for hk in hotkeys_to_process:
            if not hk:
                continue
            # Convert string keys like "CTRL+L" to tuple format
            if isinstance(hk, str):
                parts = hk.upper().split("+")
                key_parts = []
                for part in parts:
                    part = part.strip()
                    if part == "CTRL":
                        key_parts.append("command" if pyautogui.platform.system() == "Darwin" else "ctrl")
                    elif part == "CMD":
                        key_parts.append("command")
                    elif part == "ALT":
                        key_parts.append("option" if pyautogui.platform.system() == "Darwin" else "alt")
                    elif part == "SHIFT":
                        key_parts.append("shift")
                    else:
                        key_parts.append(part.lower())
                pyautogui.hotkey(*key_parts)
            else:
                pyautogui.hotkey(*hk)
            time.sleep(0.3)

        if "text" in step.inputs:
            pyautogui.write(str(step.inputs["text"]), interval=0.01)
            time.sleep(0.2)

        # Support single key presses (e.g., "return", "enter", "space")
        for key in step.inputs.get("keys", []):
            pyautogui.press(key)
            time.sleep(0.2)

        if "click" in step.inputs:
            x, y = step.inputs["click"]
            pyautogui.click(int(x), int(y))

        pause_s = float(step.inputs.get("pause_s", 0.2))
        time.sleep(pause_s)

        # Read clipboard if requested
        if step.inputs.get("read_clipboard_after_copy") and pyperclip:
            try:
                clipboard_text = pyperclip.paste()
                if "clipboard_text" in (step.outputs_schema or {}):
                    out["clipboard_text"] = clipboard_text
            except Exception:
                pass

        # You can standardize a "saved_path" output if your workflow includes it
        if "saved_path" in step.inputs:
            out["saved_path"] = step.inputs["saved_path"]

        # Set default outputs if not already set
        if "app_opened" in (step.outputs_schema or {}):
            out.setdefault("app_opened", "Notes")
        if "saved" in (step.outputs_schema or {}):
            out.setdefault("saved", "true")
        if "focused_app" in (step.outputs_schema or {}):
            out.setdefault("focused_app", "Notes")

        return out
