# demo2agent (macOS)

Record a real demo (desktop + browser), compile it into a typed workflow using an LLM,
then execute it using:
- **browser-use** for WEB steps (LLM-driven web automation)
- **macOS AX** (Accessibility API) or **pyautogui** for DESKTOP steps(not used, experimented)
- **AppleScript** for macOS Notes integration

This package implements the "LLM compiler + specialized executors + verification loop" pattern.

## Architecture Summary

The system follows a three-stage pipeline:

### 1. Recorder
Captures user interactions during a demo:
- Mouse clicks and keyboard events (via `pynput`)
- Screen recordings (optional)
- Audio recordings (optional, with local ASR via faster-whisper)
- Active window titles (best-effort)

**Output**: `runs/<demo>/trace.json` (+ `screen.mp4`, `audio.m4a` if enabled)

### 2. Segmentation (LLM) - Two-Phase
Uses OpenAI API in two phases to segment the screen recording:

**Phase 1: Visual Segmentation** (`llm_segmenter.py`)
- Analyzes sampled frames from the video recording
- Identifies where user intent changes or interaction surface changes
- Produces high-level task chunks (4–12 segments for a ~3 minute demo)
- Focuses on user goals, not automation executors

**Phase 2: Align to Executors** (`llm_segmenter.py`)
- Takes visual segments from Phase 1
- Aligns segments to executor capabilities (WEB, DESKTOP, WAIT)
- Merges adjacent segments when a single executor can handle them
- Assigns surface types (WEB | DESKTOP | WAIT | AUTO) to each segment
- Optimizes for executor-aligned steps (typically 4–10 final segments)

**Output**: `runs/<demo>/segments.json`

### 3. Preprocessing (`compiler_preprocess.py`)
Extracts evidence and prepares data for compilation:
- Extracts keyframes from video at segment timestamps
- Generates thumbnails for LLM context
- Combines segments with transcript text (if available)
- Extracts window titles, typed text, and other metadata
- Builds `compile_input.json` with all evidence for the compiler

**Output**: `runs/<demo>/compiled/compile_input.json`

### 4. Compiler (LLM) (`compiler_llm.py`)
Uses OpenAI API to convert preprocessed segments into a structured `WorkflowSpec`:
- Consumes `compile_input.json` with segments and evidence
- Generates typed steps: `WEB` / `DESKTOP` / `WAIT`
- Defines input/output schemas per step
- Adds postconditions for verification
- Sets retry policies and time budgets
- Creates template variables (e.g., `{{ user_text }}`, `{{ steps.step_id.field }}`)

**Output**: `runs/<demo>/compiled/workflow.json`

### 5. Orchestrator (Runtime)
Executes workflow steps using specialized executors:
- **WEB steps** → `browser-use` Agent (LLM-driven browser automation)
- **DESKTOP steps** → macOS AX executor 
(desktop automation)
- **WAIT steps** → Simple time delays

After each step:
- Template rendering (substitutes variables)
- Output validation against schema
- Postcondition verification
- Bounded retries on failure

**Output**: `runs/<demo>/last_run.json` (execution results)

## Setup (macOS)

### Prerequisites

1. **macOS Permissions** (required):
   - System Settings → Privacy & Security → Accessibility
   - Enable your Terminal / Python environment
   - Also allow Input Monitoring if prompted

2. **Python 3.11+**

3. **Environment Variables**:
   - `OPENAI_API_KEY` (required for compiler)
   - `BROWSER_USE_API_KEY` (required for browser-use executor)

### Installation

```bash
# Clone repository
cd /path/to/cui

# Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -U pip
pip install -r requirements.txt

# Install package (editable mode)
pip install -e .
```

## Running

### 1. Record a Demo

Record your desktop interactions:

```bash
python demo2agent/cli.py record --out runs/my_demo --seconds 180
```

Options:
- `--out`: Output directory (default: `runs/demo1`)
- `--seconds`: Recording duration (default: 180)
- `--audio`: Enable audio recording
- `--transcribe`: Enable transcription (requires `--audio`)

**Output**: `runs/my_demo/trace.json`, `runs/my_demo/screen.mp4`

### 2. Compile to Workflow

Compile the recorded trace into a workflow:

```bash
python demo2agent/cli.py compile --run runs/my_demo
```

This performs (in order):
1. **Segmentation** (LLM-based, two-phase):
   - Phase 1: Visual segmentation (identifies task chunks)
   - Phase 2: Alignment to executors (assigns surface types)
2. **Preprocessing** (evidence extraction):
   - Extracts keyframes from video at segment timestamps
   - Generates thumbnails and compiles evidence
   - Combines with transcript and metadata
3. **Compilation** (LLM generates `WorkflowSpec`):
   - Converts segments + evidence into structured workflow
   - Defines steps, schemas, postconditions, and templates

**Outputs**: 
- `runs/my_demo/segments.json` (two-phase segmentation results)
- `runs/my_demo/compiled/compile_input.json` (preprocessed evidence)
- `runs/my_demo/compiled/workflow.json` (final workflow)

Options:
- `--workflow-name`: Custom workflow name
- `--skip-segment`: Skip video segmentation step
- `--text`: Optional narration/intent text for better compilation

### 3. Run Workflow

Execute the compiled workflow:

```bash
python demo2agent/cli.py run --run runs/my_demo --text "your search query here"
```

The `--text` argument is substituted into `{{ user_text }}` templates throughout the workflow.

**Output**: `runs/my_demo/last_run.json` (step outputs and execution results)

### 4. Test Individual Steps (Optional)

Test individual workflow steps in isolation:

```bash
# List all steps
python test_step.py --run runs/my_demo --list

# Test a specific step
python test_step.py --run runs/my_demo --step step_3_search --text "pizza restaurants"

# Test with previous outputs for template substitution
python test_step.py --run runs/my_demo --step step_8_save_to_notes --prev-outputs outputs.json
```

## Entry Point

The main entry point is `demo2agent/cli.py` with three commands:

- `record`: Record desktop interactions
- `compile`: Compile trace into workflow
- `run`: Execute compiled workflow

All commands use `--run <directory>` to specify the run directory (e.g., `runs/demo1`).

## Project Structure

```
cui/
├── demo2agent/          # Main package
│   ├── cli.py          # CLI entry point
│   ├── recorder.py     # Recording logic
│   ├── compiler_llm.py # LLM compiler
│   ├── orchestrator.py # Workflow execution
│   ├── models.py       # Pydantic models (WorkflowSpec, Step, etc.)
│   └── executors/      # Step executors
│       ├── web_browser_use.py
│       ├── macos_ax_desktop_executor.py
│       ├── desktop_pyautogui.py
│       └── desktop_macos_notes.py
├── runs/               # Demo recordings and workflows
│   └── <demo_name>/
│       ├── trace.json
│       ├── screen.mp4
│       ├── compiled/
│       │   └── workflow.json
│       └── last_run.json
├── test_step.py        # Test individual workflow steps
└── requirements.txt    # Python dependencies