# Design Document

This document explains the key design decisions, tradeoffs, limitations, and possible extensions for the demo2agent system.

## Design Philosophy

The system follows the "LLM compiler + specialized executors + verification loop" pattern. LLMs excel at high-level planning and understanding user intent, while specialized executors provide reliable, deterministic execution.

## Key Design Decisions

### 1. Three-Stage Pipeline: Record → Compile → Execute

**Decision**: Separate recording, compilation, and execution into distinct stages.

**Rationale**:
- **Separation of concerns**: Each stage has a clear, focused responsibility
- **Reproducibility**: Compiled workflows can be executed multiple times with different inputs
- **Debuggability**: Each stage produces inspectable outputs (trace.json, workflow.json, last_run.json)
- **Iterative development**: Can recompile or re-run workflows without re-recording

**Tradeoffs**:
- ✅ Clear separation makes debugging easier
- ✅ Workflows can be version-controlled and shared
- ❌ More files to manage
- ❌ No "live" compilation/execution

### 2. Two-Phase LLM Segmentation

**Decision**: Use two LLM phases for video segmentation (visual segmentation → executor alignment).

**Rationale**:
- **Separation of concerns**: Phase 1 focuses on user intent, Phase 2 aligns to executor capabilities
- **Better quality**: Two focused passes produce better segments than one combined pass
- **Flexibility**: Can adjust alignment independently without re-segmenting

**Tradeoffs**:
- ✅ Better segmentation quality
- ✅ Clear separation between user intent and automation requirements
- ❌ Two API calls (higher cost, latency)

### 3. LLM-Based Compilation

**Decision**: Use LLMs to convert preprocessed segments into structured workflows.

**Rationale**:
- **Flexibility**: LLMs can understand user intent from diverse interactions
- **Abstraction**: Automatically extracts high-level goals from low-level events
- **Evidence integration**: Can synthesize multiple evidence sources (screen, audio, events)

**Tradeoffs**:
- ✅ Handles complex, varied user interactions
- ✅ Can infer goals from noisy recordings
- ❌ Requires API calls (cost, latency, network dependency)
- ❌ Non-deterministic (same input may compile differently)

**Architecture Note**: All LLM calls go through `llm_json.py` layer, enabling easy evaluation module integration and future fine-tuning.

### 4. Specialized Executors

**Decision**: Use different executors for different step types (WEB, DESKTOP, WAIT).

**Rationale**:
- **Best tool for the job**: Each executor is optimized for its domain
- **Reliability**: Specialized executors are more reliable than generic automation
- **Maintainability**: Changes to one executor don't affect others

**Current Executors**:
- **WEB**: `browser-use` (LLM-driven browser automation)
- **DESKTOP**: macOS AX (Accessibility API) or pyautogui (fallback)
- **WAIT**: Simple time delays

**Tradeoffs**:
- ✅ Each executor can be optimized independently
- ✅ Can mix and match executors based on task requirements
- ❌ More code to maintain
- ❌ Must handle executor-specific errors

### 5. Template-Based Input/Output

**Decision**: Use Jinja2-style templates (`{{ user_text }}`, `{{ steps.step_id.field }}`) for step inputs and goals.

**Rationale**:
- **Parameterization**: Workflows can accept different inputs
- **Composition**: Steps can reference outputs from previous steps
- **Readability**: Templates are intuitive and easy to understand

**Tradeoffs**:
- ✅ Flexible and composable
- ✅ Easy to understand and debug
- ❌ Requires template rendering at runtime

### 6. Postcondition Verification

**Decision**: Each step can declare postconditions that are verified after execution.

**Rationale**:
- **Reliability**: Catches execution failures early
- **Debuggability**: Clear failure messages when postconditions fail
- **Self-healing**: Can trigger retries on failure

**Current Postcondition Types**:
- `nonempty`: Field must have a non-empty value
- `url_contains_any`: URL must contain one of the allowed strings

**Tradeoffs**:
- ✅ Provides runtime validation
- ✅ Helps debug execution issues
- ❌ Can only check observable outputs, not side effects

**Extension**: Orchestrator is deterministic today; could add LLM as a judge in postcondition evaluation and evidence intent checks.

### 7. Structured Outputs via Pydantic Models

**Decision**: Use Pydantic models to define and validate step outputs.

**Rationale**:
- **Type safety**: Catches output mismatches at runtime
- **Documentation**: Models serve as documentation for expected outputs
- **Validation**: Automatic validation against schema

**Tradeoffs**:
- ✅ Type-safe and validated
- ✅ Clear contracts between steps
- ❌ Schema changes require recompilation

### 8. macOS-First Design

**Decision**: Initially target macOS only.

**Rationale**:
- **Native APIs**: macOS provides robust Accessibility APIs (AX)
- **Focus**: Starting with one platform allows faster iteration

**Tradeoffs**:
- ✅ Can leverage platform-specific features
- ✅ Simpler initial implementation
- ❌ Not cross-platform

## Tradeoffs

### LLM Dependency

**Tradeoff**: System relies heavily on LLMs for segmentation, compilation, and web execution.

- **Pros**: Flexible, generalizes well, handles complex scenarios
- **Cons**: Cost, latency, non-determinism, network dependency

**Mitigation**: 
- All LLM calls go through `llm_json.py` layer (enables evaluation modules and fine-tuning)
- Cache compiled workflows
- Use structured outputs to reduce non-determinism

### Compile-Time vs Runtime Validation

**Tradeoff**: Some validation happens at compile time (schema), some at runtime (postconditions).

- **Pros**: Catches errors early (compile) and late (runtime)
- **Cons**: Errors may appear late in the pipeline

**Mitigation**:
- Strict schema validation at compile time
- Test individual steps in isolation (`test_step.py`)

## Limitations

### 1. macOS-Only

**Current**: System only works on macOS (uses macOS-specific APIs).

**Extension**: Could support Linux/Windows with platform-specific executors.

### 2. Limited Desktop Automation

**Current**: Desktop automation is basic (click, type, hotkeys).

**Extension**: Could improve with better element detection (computer vision, AX queries).

### 3. No Workflow Composition

**Current**: Workflows are monolithic (no sub-workflows or libraries).

**Extension**: Could support workflow modules, sub-workflow calls, versioning.

### 4. Deterministic Postconditions

**Current**: Postconditions are deterministic (regex, string checks).

**Extension**: Could add LLM judge for complex postcondition evaluation and evidence intent checks.

## Possible Extensions

### 1. Task Workflow Generation Module for Evaluations

**Extension**: Add a module for generating task workflows for evaluation purposes.

**Approach**:
- All LLM calls go through `llm_json.py` layer, making it easy to inject evaluation modules
- Can intercept/modify LLM calls for testing and fine-tuning
- Can fine-tune this component separately if needed

**Benefits**:
- Easy integration of evaluation frameworks
- Can generate synthetic workflows for testing
- Enables A/B testing of different compilation strategies

### 2. LLM Judge for Postconditions

**Extension**: Use LLM as a judge for postcondition evaluation and evidence intent checks.

**Approach**:
- Current orchestrator is deterministic (string checks, regex)
- Could add LLM judge for complex postconditions (e.g., "does this screenshot show the expected result?")
- Could use LLM to verify evidence matches intent (e.g., "does this step output match the goal?")

**Benefits**:
- More flexible postcondition checking
- Can handle complex, subjective checks
- Better error detection for ambiguous outputs

### 3. Executor Screenshots

**Extension**: Executors can return a screenshot as the last state of the task.

**Approach**:
- Each executor captures a screenshot after task completion
- Screenshots stored in execution results
- Can be used for debugging, validation, and evidence

**Benefits**:
- Visual debugging of execution results
- Evidence for postcondition verification
- Better understanding of task outcomes

### 4. Cross-Platform Support

**Extension**: Support Linux and Windows.

**Approach**: Abstract platform-specific code, implement platform-specific executors.

**Challenges**: Different automation APIs per platform.

### 5. Workflow Libraries

**Extension**: Enable reuse of common workflow patterns.

**Approach**: Define workflow modules/components, support import/include statements.

**Challenges**: Template parameterization, dependency management.

## Conclusion

The current design prioritizes flexibility and ease of use over performance and determinism. The architecture (especially the `llm_json.py` abstraction layer) makes it easy to add evaluation modules, fine-tune components, and extend the system with LLM judges and executor improvements.
