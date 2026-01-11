# Design Document

This document explains the design decisions, tradeoffs, limitations, and possible extensions for the demo2agent system.

## Design Philosophy

The system follows the "LLM compiler + specialized executors + verification loop" pattern. The core insight is that LLMs excel at high-level planning and understanding user intent, while specialized executors provide reliable, deterministic execution of specific tasks.

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
- ❌ More files to manage (trace, compiled workflow, execution results)
- ❌ No "live" compilation/execution (must complete recording first)

### 2. LLM-Based Compilation

**Decision**: Use LLMs (GPT-4o/GPT-5.2) to convert raw traces into structured workflows.

**Rationale**:
- **Flexibility**: LLMs can understand user intent from diverse interactions
- **Abstraction**: Automatically extracts high-level goals from low-level events
- **Generalization**: Can compile workflows that generalize across similar tasks
- **Evidence integration**: Can synthesize multiple evidence sources (screen, audio, events)

**Tradeoffs**:
- ✅ Handles complex, varied user interactions
- ✅ Can infer goals and structure from noisy recordings
- ❌ Requires API calls (cost, latency, network dependency)
- ❌ Non-deterministic (same trace may compile differently)
- ❌ Requires careful prompting and validation

**Alternative Considered**: Rule-based compiler
- ❌ Too brittle for diverse interaction patterns
- ❌ Would require extensive domain knowledge

### 3. Specialized Executors

**Decision**: Use different executors for different step types (WEB, DESKTOP, WAIT).

**Rationale**:
- **Best tool for the job**: Each executor is optimized for its domain
- **Reliability**: Specialized executors are more reliable than generic automation
- **Maintainability**: Changes to one executor don't affect others
- **Extensibility**: Easy to add new executor types

**Current Executors**:
- **WEB**: `browser-use` (LLM-driven browser automation)
- **DESKTOP**: macOS AX (Accessibility API) or pyautogui (fallback)
- **WAIT**: Simple time delays
- **NOTES**: AppleScript/macnotesapp for macOS Notes

**Tradeoffs**:
- ✅ Each executor can be optimized independently
- ✅ Can mix and match executors based on task requirements
- ❌ More code to maintain
- ❌ Must handle executor-specific errors and edge cases

### 4. Template-Based Input/Output

**Decision**: Use Jinja2-style templates (`{{ user_text }}`, `{{ steps.step_id.field }}`) for step inputs and goals.

**Rationale**:
- **Parameterization**: Workflows can accept different inputs
- **Composition**: Steps can reference outputs from previous steps
- **Readability**: Templates are intuitive and easy to understand
- **Standard format**: Jinja2 is a well-known templating language

**Tradeoffs**:
- ✅ Flexible and composable
- ✅ Easy to understand and debug
- ❌ Requires template rendering at runtime
- ❌ Must validate template references (fail fast if step output missing)

**Alternative Considered**: JSONPath or XPath
- ❌ Less readable
- ❌ More complex for simple use cases

### 5. Postcondition Verification

**Decision**: Each step can declare postconditions that are verified after execution.

**Rationale**:
- **Reliability**: Catches execution failures early
- **Debuggability**: Clear failure messages when postconditions fail
- **Self-healing**: Can trigger retries or repairs on failure

**Current Postcondition Types**:
- `nonempty`: Field must have a non-empty value
- `url_contains_any`: URL must contain one of the allowed strings
- `rating_range`: Numeric value must be within a range

**Tradeoffs**:
- ✅ Provides runtime validation
- ✅ Helps debug execution issues
- ❌ Requires careful design of postconditions (must be observable)
- ❌ Can be noisy if postconditions are too strict

**Limitation**: Postconditions can only check observable outputs, not side effects (e.g., "file was created").

### 6. Structured Outputs via Pydantic Models

**Decision**: Use Pydantic models to define and validate step outputs.

**Rationale**:
- **Type safety**: Catches output mismatches at runtime
- **Documentation**: Models serve as documentation for expected outputs
- **Compatibility**: browser-use supports Pydantic output models
- **Validation**: Automatic validation against schema

**Tradeoffs**:
- ✅ Type-safe and validated
- ✅ Clear contracts between steps
- ❌ Must define schemas upfront
- ❌ Schema changes require recompilation

### 7. macOS-First Design

**Decision**: Initially target macOS only.

**Rationale**:
- **Native APIs**: macOS provides robust Accessibility APIs (AX)
- **AppleScript**: Excellent integration with macOS apps (Notes, etc.)
- **Focus**: Starting with one platform allows faster iteration

**Tradeoffs**:
- ✅ Can leverage platform-specific features
- ✅ Simpler initial implementation
- ❌ Not cross-platform (would require porting)
- ❌ Limited to macOS users

## Tradeoffs

### 1. LLM Dependency

**Tradeoff**: System relies heavily on LLMs for compilation and web execution.

- **Pros**: Flexible, generalizes well, handles complex scenarios
- **Cons**: Cost, latency, non-determinism, network dependency

**Mitigation**: 
- Use structured outputs to reduce non-determinism
- Cache compiled workflows
- Provide local fallbacks where possible

### 2. Compile-Time vs Runtime Validation

**Tradeoff**: Some validation happens at compile time (schema), some at runtime (postconditions).

- **Pros**: Catches errors early (compile) and late (runtime)
- **Cons**: Errors may appear late in the pipeline

**Mitigation**:
- Strict schema validation at compile time
- Comprehensive postconditions at runtime
- Test individual steps in isolation (`test_step.py`)

### 3. Recording Fidelity

**Tradeoff**: Recording captures user interactions but may miss context.

- **Pros**: Captures what user actually did
- **Cons**: May miss implicit context (user's mental model, background state)

**Mitigation**:
- Include screen recordings for visual context
- Support audio transcription for narration
- Allow manual annotation via `--text` flag

### 4. Executor Complexity

**Tradeoff**: Different executors have different capabilities and limitations.

- **Pros**: Best tool for each job
- **Cons**: Must understand each executor's constraints

**Mitigation**:
- Document executor capabilities in `executor_specs.py`
- Provide clear error messages
- Support fallback executors (e.g., pyautogui → AX)

## Limitations

### 1. macOS-Only

**Current**: System only works on macOS.

**Why**: Uses macOS-specific APIs (AX, AppleScript).

**Extension**: Could support Linux/Windows by:
- Using platform-agnostic libraries (e.g., pyautogui)
- Implementing platform-specific executors
- Abstracting platform differences

### 2. Limited Desktop Automation

**Current**: Desktop automation is basic (click, type, hotkeys).

**Why**: Desktop automation is inherently fragile and platform-dependent.

**Extension**: Could improve by:
- Better element detection (computer vision, AX queries)
- More sophisticated interaction patterns
- Error recovery and retry strategies

### 3. Single-User Workflows

**Current**: Workflows assume single-user, single-machine execution.

**Why**: Simplifies initial implementation.

**Extension**: Could support:
- Multi-user workflows
- Distributed execution
- Collaborative editing

### 4. No Workflow Composition

**Current**: Workflows are monolithic (no sub-workflows or libraries).

**Why**: Simpler compilation and execution model.

**Extension**: Could support:
- Workflow libraries/modules
- Sub-workflow calls
- Workflow versioning and dependencies

### 5. Limited Error Recovery

**Current**: Retries are bounded and basic.

**Why**: Complex error recovery is difficult to generalize.

**Extension**: Could support:
- Repair hooks (partially implemented)
- Adaptive retry strategies
- Workflow patching/mutation

### 6. No Workflow Validation

**Current**: No validation that workflow will work before execution.

**Why**: Would require simulating execution.

**Extension**: Could support:
- Dry-run mode
- Workflow testing/sandboxing
- Static analysis of workflows

## Possible Extensions

### 1. Cross-Platform Support

**Extension**: Support Linux and Windows.

**Approach**:
- Abstract platform-specific code
- Implement platform-specific executors
- Use platform-agnostic libraries where possible

**Challenges**: 
- Different automation APIs per platform
- Different app integration mechanisms
- Testing across platforms

### 2. Workflow Libraries

**Extension**: Enable reuse of common workflow patterns.

**Approach**:
- Define workflow modules/components
- Support import/include statements
- Version workflow libraries

**Challenges**:
- Template parameterization
- Dependency management
- Version compatibility

### 3. Interactive Debugging

**Extension**: Support step-by-step execution with inspection.

**Approach**:
- Add breakpoints
- Inspect step inputs/outputs
- Allow manual intervention

**Challenges**:
- State management
- Resuming after intervention
- UI for debugging

### 4. Workflow Optimization

**Extension**: Optimize workflows for speed/reliability.

**Approach**:
- Parallel execution of independent steps
- Caching step outputs
- Adaptive timeouts/retries

**Challenges**:
- Detecting dependencies
- Handling side effects
- Ensuring correctness

### 5. Visual Workflow Editor

**Extension**: GUI for creating/editing workflows.

**Approach**:
- Visual representation of steps
- Drag-and-drop editing
- Live preview

**Challenges**:
- UI design
- Schema editing
- Template visualization

### 6. Workflow Learning

**Extension**: Learn workflows from multiple examples.

**Approach**:
- Multi-example compilation
- Generalization across examples
- Active learning

**Challenges**:
- Example selection
- Generalization quality
- Handling variations

### 7. Better Desktop Automation

**Extension**: Improve desktop automation reliability.

**Approach**:
- Computer vision for element detection
- AX queries for semantic UI access
- Robust error handling

**Challenges**:
- Performance (CV is slow)
- Reliability (UI changes)
- Portability (platform-specific)

## Conclusion

The current design prioritizes flexibility and ease of use over performance and determinism. This is appropriate for a tool that aims to democratize workflow automation by allowing users to record demos and convert them into executable workflows.

The main limitations (macOS-only, basic desktop automation, no workflow composition) are intentional tradeoffs that allow rapid iteration. Future work could address these limitations while maintaining the core "LLM compiler + specialized executors" pattern.
