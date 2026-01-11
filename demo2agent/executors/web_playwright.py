from __future__ import annotations
from typing import Any, Dict
from demo2agent.models import Step
from playwright.sync_api import sync_playwright

class PlaywrightWebExecutor:
    def run(self, step: Step) -> Dict[str, Any]:
        """
        Minimal: supports a common pattern:
        inputs: { "url": "...", "extract_css": {"field":"selector", ...} }
        """
        url = step.inputs.get("url")
        extract_css = step.inputs.get("extract_css", {})

        if not url:
            raise ValueError("PlaywrightWebExecutor requires inputs.url")

        out: Dict[str, Any] = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded")

            for field, selector in extract_css.items():
                el = page.locator(selector).first
                out[field] = (el.inner_text(timeout=5000) or "").strip()

            browser.close()
        return out
