from __future__ import annotations

import asyncio
from typing import Any, Dict

from demo2agent.models import Step


class BrowserUseWebExecutor:
    """
    Runs a WEB step via browser-use.

    Contract:
    - Expects step.inputs.task (string).
    - Returns dict compatible with step.output_model().
    """

    def __init__(self, use_cloud: bool = False):
        self.use_cloud = use_cloud

    async def _run_async(self, step: Step) -> Dict[str, Any]:
        from browser_use import Agent, Browser, ChatBrowserUse

        browser = Browser()
        llm = ChatBrowserUse()

        OutputModel = step.output_model()

        task_text = str(step.inputs.get("task", "")).strip()
        if not task_text:
            raise ValueError(f"{step.id}: step.inputs.task is required for WEB steps")

        # Provide both goal + task; task is the main instruction (more detailed).
        task = f"""
        Goal:
        {step.goal}
        """
        task += f"Step task (follow precisely):\n{task_text}\n"

        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            output_model_schema=OutputModel,  # browser-use enforces this format
        )

        result = await agent.run(max_steps=15)
        return result.structured_output.model_dump()
        # history = getattr(result, "history", None)
        # if not history:
        #     raise RuntimeError(f"{step.id}: browser-use returned no history")

        # for item in reversed(history):
        #     model_output = item.get("model_output")
        #     if not model_output:
        #         continue

        #     actions = model_output.get("action") or []
        #     if not isinstance(actions, list):
        #         actions = [actions]

        #     for action in actions:
        #         if "done" in action:
        #             data = action["done"].get("data")
        #             if isinstance(data, dict):
        #                 return data

        # raise RuntimeError(
        #     f"{step.id}: browser-use did not produce a DONE action with structured data"
        # )

    def run(self, step: Step) -> Dict[str, Any]:
        return asyncio.run(self._run_async(step))
