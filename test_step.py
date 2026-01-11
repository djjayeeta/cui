#!/usr/bin/env python3
"""
Test individual workflow steps in isolation.

Usage:
    python test_step.py --run runs/demo1 --step step_3_search --text "best pizza restaurants in San Jose"
    python test_step.py --run runs/demo1 --step step_8_save_to_notes
    python test_step.py --run runs/demo1 --step step_7_open_notes
    python test_step.py --run runs/demo1 --list  # List all steps
"""

import argparse
import json
import sys
from pathlib import Path
import asyncio
from dotenv import load_dotenv

from demo2agent.models import WorkflowSpec, Step
from demo2agent.util import read_json, write_json
from demo2agent.executors.web_browser_use import BrowserUseWebExecutor
from demo2agent.executors.web_playwright import PlaywrightWebExecutor
from demo2agent.executors.desktop_pyautogui import PyAutoGuiDesktopExecutor
from demo2agent.executors.desktop_macos_notes import MacOSNotesExecutor
from demo2agent.orchestrator import render_templates

try:
    from demo2agent.executors.macos_ax_desktop_executor import MacOSAXDesktopExecutor
    AX_EXECUTOR_AVAILABLE = True
except ImportError:
    AX_EXECUTOR_AVAILABLE = False


def load_workflow(run_dir: Path) -> WorkflowSpec:
    """Load workflow from run directory."""
    workflow_path = run_dir / "compiled" / "workflow.json"
    if not workflow_path.exists():
        raise FileNotFoundError(f"Workflow not found: {workflow_path}")
    return WorkflowSpec(**read_json(workflow_path))


def list_steps(wf: WorkflowSpec):
    """List all steps in the workflow."""
    print("\n" + "=" * 80)
    print("WORKFLOW STEPS:")
    print("=" * 80)
    for i, step in enumerate(wf.steps, 1):
        print(f"\n{i}. {step.id}")
        print(f"   Type: {step.type}")
        print(f"   Goal: {step.goal[:100]}..." if len(step.goal) > 100 else f"   Goal: {step.goal}")
        print(f"   Executor: {step.executor_hint}")
        if step.inputs:
            print(f"   Inputs: {list(step.inputs.keys())}")


def get_step_by_id(wf: WorkflowSpec, step_id: str) -> Step:
    """Get a step by its ID."""
    for step in wf.steps:
        if step.id == step_id:
            return step
    raise ValueError(f"Step '{step_id}' not found in workflow")


def build_runtime_inputs(user_inputs: dict, wf: WorkflowSpec) -> dict:
    """Build runtime inputs from user inputs and workflow defaults."""
    runtime = {}
    # wf.inputs is a Dict[str, ValueType] like {"user_text": "string"}
    for key, value_type in wf.inputs.items():
        if key in user_inputs:
            runtime[key] = user_inputs[key]
        else:
            # No defaults in WorkflowSpec inputs, just use None
            runtime[key] = None
    return runtime


def test_step(
    wf: WorkflowSpec,
    step_id: str,
    user_inputs: dict,
    previous_outputs: dict = None,
):
    """Test a single step with optional previous outputs for template substitution."""
    step = get_step_by_id(wf, step_id)
    
    # Build context for template rendering
    runtime_inputs = build_runtime_inputs(user_inputs, wf)
    ctx = {
        "inputs": runtime_inputs,
        "steps": previous_outputs or {},
    }
    from jinja2 import Environment, StrictUndefined, TemplateError
    env = Environment(
            undefined=StrictUndefined,
            autoescape=False,
        )
    # Render templates in step
    step = step.model_copy(
        update={
            "goal": render_templates(step.goal, env, ctx),
            "inputs": render_templates(step.inputs, env, ctx),
        }
    )
    
    print("\n" + "=" * 80)
    print(f"TESTING STEP: {step.id}")
    print("=" * 80)
    print(f"Type: {step.type}")
    print(f"Goal: {step.goal}")
    print(f"Executor: {step.executor_hint}")
    print(f"\nInputs:")
    print(json.dumps(step.inputs, indent=2))
    print("\n" + "-" * 80)
    
    # Execute step based on type
    outputs = None
    try:
        if step.type == "WEB":
            hint = step.executor_hint or "auto"
            if hint in ("auto", "browser_use"):
                executor = BrowserUseWebExecutor(use_cloud=False)
                outputs = executor.run(step)
            elif hint == "playwright":
                executor = PlaywrightWebExecutor()
                outputs = executor.run(step)
            else:
                executor = BrowserUseWebExecutor(use_cloud=False)
                outputs = executor.run(step)
                
        elif step.type == "DESKTOP":
            # if step.executor_hint == "desktop_macos_notes":
            #     executor = MacOSNotesExecutor()
            #     outputs = executor.run(step)
            # elif step.executor_hint == "desktop_macos_ax" and AX_EXECUTOR_AVAILABLE:
            executor = MacOSAXDesktopExecutor()
            outputs = executor.run(step)
            # else:
            #     executor = PyAutoGuiDesktopExecutor()
            #     outputs = executor.run(step)
                
        elif step.type == "WAIT":
            import time
            seconds = float(step.inputs.get("seconds", 1.0))
            print(f"Waiting {seconds} seconds...")
            time.sleep(seconds)
            outputs = {"waited_seconds": seconds}
        else:
            raise ValueError(f"Unknown step type: {step.type}")
        
        print("\n" + "=" * 80)
        print("OUTPUTS:")
        print("=" * 80)
        print(json.dumps(outputs, indent=2))
        print("\n✅ Step completed successfully!")
        return outputs
        
    except Exception as e:
        print("\n" + "=" * 80)
        print("ERROR:")
        print("=" * 80)
        print(f"❌ Step failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Test individual workflow steps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--run",
        type=Path,
        required=True,
        help="Path to run directory (e.g., runs/demo1)"
    )
    parser.add_argument(
        "--step",
        type=str,
        help="Step ID to test (use --list to see all steps)"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all steps in the workflow"
    )
    parser.add_argument(
        "--text",
        type=str,
        help="Text input for the workflow (maps to 'user_text' input)"
    )
    parser.add_argument(
        "--prev-outputs",
        type=Path,
        help="JSON file containing previous step outputs for template substitution"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Save outputs to JSON file"
    )
    
    args = parser.parse_args()
    
    # Load environment variables
    load_dotenv()
    
    # Load workflow
    try:
        wf = load_workflow(args.run)
    except Exception as e:
        print(f"❌ Error loading workflow: {e}", file=sys.stderr)
        sys.exit(1)
    
    # List steps if requested
    if args.list:
        list_steps(wf)
        return
    
    # Get step ID
    if not args.step:
        print("❌ Error: --step is required (or use --list to see all steps)", file=sys.stderr)
        sys.exit(1)
    
    # Build user inputs
    user_inputs = {}
    if args.text:
        user_inputs["user_text"] = args.text
    
    # Load previous outputs if provided
    previous_outputs = {}
    if args.prev_outputs:
        try:
            previous_outputs = read_json(args.prev_outputs)
        except Exception as e:
            print(f"⚠️  Warning: Could not load previous outputs: {e}", file=sys.stderr)
    
    # Test the step
    outputs = test_step(wf, args.step, user_inputs, previous_outputs)
    
    # Save outputs if requested
    if args.output and outputs:
        with open(args.output, "w") as f:
            json.dump(outputs, f, indent=2)
        print(f"\n💾 Outputs saved to: {args.output}")
    
    # Exit with error code if step failed
    if outputs is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
    # async def example() :
    #     from browser_use import Agent, Browser, ChatBrowserUse 
    #     from browser_use.llm.openai.chat import ChatOpenAI # type: ignore

    #     browser = Browser()
    #     llm = ChatBrowserUse()
    #     # llm = ChatOpenAI(api_key="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", model="gpt-4o-mini")

    #     # Keep the task short + unambiguous. Let output_model_schema enforce structure.
    #     task = "Search in the google for best pizza restaurants in San Jose and capture the top result. Change language to english if required."


    #     agent = Agent(
    #         task=task,
    #         llm=llm,
    #         browser=browser,
    #     )

    #     result = await agent.run()
    # print(asyncio.run(example()))
