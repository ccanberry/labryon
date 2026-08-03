# Testing Framework - Claude Context

This document provides guidance to Claude Code when working with the SOP-based testing framework in the `labryon/` directory.

## Overview

This is a comprehensive testing framework for evaluating AI agents that follow Standard Operating Procedures (SOPs). The framework consists of:

1. **SOP Analysis** (`sop_process.py`) - Analyzes SOPs and extracts workflow graphs
2. **Scenario Generation** (`scenario_generator.py`) - Generates test scenarios (happy/unhappy paths)
3. **Simulation** (`simulation_runner.py`) - Runs agent simulations with mock tools
4. **Evaluation** (`evaluate_results.py`) - Evaluates agent performance using Composo

## Key Files

### Configuration Files (Global)
- **`labryon/config/sop_config.yaml`** - SOP analysis configuration (LLM provider, prompts, constraints)
- **`labryon/config/llm_generator_config.yaml`** - Scenario generation configuration (prompts, temperatures)
- **`labryon/config/evaluation_criteria.yaml`** - Evaluation configuration (Composo tracing, criteria)
- **`labryon/config/mock_tool_config.yaml`** - Mock tool response definitions

### Configuration Files (Worker-Specific)
- **`partition_configs.yaml`** - Per-worker scenario generation overrides (optional)
  - Location: Same directory as `graph_output.json` (e.g., `labryon/sop/Worker_Name/20251112_1430/partition_configs.yaml`)
  - **Optional file** - if not present, defaults are used (`limit_variations: 3`)
  - Create manually to customize scenario generation behavior
  - **Important**: Configs are mapped **by index** (not by name) - Scenario 0, Partition 0, etc.
  - Structure:
    ```yaml
    defaults:
      limit_variations: 4
    redirectors_enabled: false  # Master switch for redirector examples
    scenario_configs:
      # First-level list: scenarios by index
      # Second-level list: partitions within each scenario by index
      - - limit_variations: 2  # Scenario 0, Partition 0
          redirector_examples: []
        - limit_variations: 3  # Scenario 0, Partition 1
      - - limit_variations: 5  # Scenario 1, Partition 0
    ```

### Core Scripts
- **`src/snapshot.py`** - Creates initial worker snapshot from database
- **`src/sop_process.py`** - Analyzes SOP text and generates workflow graph
- **`src/scenario_generator.py`** - Generates test scenarios from workflow graph
- **`src/simulation_runner.py`** - Runs agent through test scenarios with mocks
- **`src/evaluate_results.py`** - Evaluates simulation results with Composo
- **`src/aggregate_agent_stats.py`** - Aggregates scores and generates insights
- **`src/data_models.py`** - Pydantic data models for all structures
- **`src/mock_tools.py`** - Mock tool implementations for simulation
- **`src/agent_metadata.py`** - Static agent metadata registry
- **`src/agno_mocks.py`** - Standalone Agno agent/team implementations
- **`src/composo_wrapper.py`** - Composo tracing wrapper
- **`src/data_loader.py`** - Database connection and worker fetching
- **`src/prompt_manager.py`** - Prompt management with Langfuse/fallback
- **`src/prompt_templates.py`** - Fallback prompt templates

## Workflow

```
1. SOP Text → sop_process.py → graph_output.json (workflow graph)
2. graph_output.json → scenario_generator.py → populated graph with scenarios
3. graph_output.json → simulation_runner.py → graph with agent responses
4. graph_output.json → evaluate_results.py → graph with evaluations + summary
```

## Important Constraints & Rules

### SOP Analysis (`sop_config.yaml`)
1. **No Same Stakeholder**: `triggering_stakeholder` and `halting_stakeholder` must be different in each partition
2. **No Duplicate Scenarios**: Never create scenarios with identical stakeholder interaction patterns
3. **Generalized Stakeholders**: Use role names (e.g., "Carriers") not individual entities

### Scenario Generation (`llm_generator_config.yaml`)
1. **Message Format**: All generated messages must include:
   ```
   from: [sender_email/name]
   via: [email/teams/sms/etc]

   [message content]
   ```
2. **Message Source**: Only from `triggering_stakeholders`, never from the AI agent
3. **Scenario Structure**:
   - `scenario_happy_path`: End-to-end happy path (no pre-context)
   - `scenario_unhappy_path`: End-to-end unhappy path (no pre-context)
   - `partition_happy_path`: Per-partition happy path (has pre-context from previous partitions)
   - `partition_unhappy_path`: Per-partition unhappy path (has pre-context)
   - `unhappy_paths`: Individual unhappy variations per partition (has pre-context)

### Simulation (`simulation_runner.py`)
1. **Mock Tools**: All real tools are replaced with mocks (defined in `mock_tool_config.yaml`)
2. **Pure AGNO**: Uses standalone `agno_mocks.py` without backend dependencies
3. **Context Management**:
   - **Clean slate** for scenario_happy/unhappy (no pre-context)
   - **Pre-context** for partition paths (previous partitions' descriptions)
   - **Conversation history** builds up turn-by-turn within each path
4. **API Retry**: Fibonacci backoff with max 5 retries for API errors
5. **Tool Call Tracking**: `ToolCallRecorder` captures all tool calls by agent name, stored in `tool_calls_by_agent`

### Evaluation (`evaluate_results.py`)
1. **Dual Field Evaluation**: Evaluates both `internal` (reasoning) and `response` (external action)
2. **Tool Call Context**: Extracts tool calls from `trace.tool_calls_by_agent` and includes in evaluation
3. **Conversation History**: Builds up with `user_msg` + `assistant_internal` only (omits response)
4. **Context Structure**:
   - First message includes: pre-context + SOP + knowledge base
   - Subsequent messages: only user query (history provides context)
5. **Composo Integration**:
   - `e2e_evaluation_source: "both"` → sends `[Internal Reasoning]` + `[Tool Calls]` + `[External Response]`
   - Retry: 20 attempts with exponential backoff (min=1s, max=60s)
   - Response structure: `eval_response.results` list contains criterion results
6. **Single Comprehensive Criterion**: One criterion evaluates reasoning, tool usage, and action quality

## Data Models Key Fields

### SOPGraph
- `stakeholders`: List of stakeholder definitions
- `scenarios`: List of test scenarios
- `worker_config`: Agent configuration (SOP, tools, knowledge bases)
- `evaluation_summary`: Overall evaluation results

### Scenario (per-scenario storage)
- `scenario_happy_path`: List[str] - End-to-end happy messages
- `scenario_unhappy_path`: List[str] - End-to-end unhappy messages
- `scenario_happy_path_response`: List[AgentResponse] - Agent responses
- `scenario_unhappy_path_response`: List[AgentResponse] - Agent responses
- `partitions`: List of partition objects

### Partition (per-partition storage)
- `happy_path`: List[str] - Partition happy messages
- `partition_unhappy_path`: List[str] - Combined unhappy messages
- `unhappy_paths`: List[UnhappyPath] - Individual variations
- `happy_path_response`: List[AgentResponse]
- `partition_unhappy_path_response`: List[AgentResponse]
- `unhappy_path_responses`: List[List[AgentResponse]]
- `transitions`: List[Transition] - Transition scenarios
- `context`: List[str] - Previous partitions' descriptions

### AgentResponse
- `response`: List[str] - External communication (emails, messages)
- `internal`: List[str] - Internal reasoning/thoughts
- `trace`: Dict with structure:
  - `by_agent`: Optional[Dict] - Composo trace evaluations by agent (None when tracing disabled)
  - `tool_calls_by_agent`: Dict[str, List] - Tool calls organized by agent name
  - `turn_index`: int - Turn number
- `evaluation`: Optional turn-level evaluation

## LLM Provider Configuration

### Supported Providers
- **Anthropic Claude**: `llm_provider: "anthropic"` (default model: `claude-sonnet-4-20250514`)
- **Google Gemini**: `llm_provider: "google"` (default model: `gemini-2.5-pro`)

### Environment Variables Required
```bash
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...
COMPOSO_API_KEY=...     # For evaluation tracing
```

### Model Usage in Codebase
- **Test Framework**: Uses provider from `sop_config.yaml` or `llm_generator_config.yaml`

## Running the Pipeline

### Full End-to-End Pipeline
```bash
cd labryon
source venv/bin/activate

# 1. Analyze SOP and generate workflow graph
python src/sop_process.py --sop-graph /path/to/graph_output.json

# 2. Generate test scenarios
python src/scenario_generator.py --sop-graph /path/to/graph_output.json --config config/llm_generator_config.yaml

# 3. Run simulations (choose flags based on what to test)
python src/simulation_runner.py --sop-graph /path/to/graph_output.json \
  --scenario-happy --scenario-unhappy \
  --partition-happy --partition-unhappy \
  --unhappy \
  --parallel 4

# 4. Evaluate results
python src/evaluate_results.py --sop-graph /path/to/graph_output.json \
  --scenario-happy --scenario-unhappy \
  --partition-happy --partition-unhappy \
  --unhappy \
  --parallel 8
```

### Script-Specific Options

#### `snapshot.py`
- `--worker`: Name of the worker to snapshot
- `--override`: Overwrite existing snapshot without creating timestamped folder

#### `sop_process.py`
- `--sop-graph`: Path to graph_output.json file
- **Behavior**: Automatically cleans all generated fields before re-analysis

#### `scenario_generator.py`
- `--sop-graph`: Path to graph_output.json
- `--parallel N`: Number of parallel processes (default: 1)
- **Behavior**: Always clears existing scenario results before generation. Loads config from `config/llm_generator_config.yaml`.

#### `simulation_runner.py`
- `--sop-graph`: Path to graph_output.json
- `--parallel N`: Number of parallel processes
- `--debug`: Enable debug logging
- `--scenario <ID>`: Run specific scenario index or "all"
- Flags: `--all`, `--scenario-happy`, `--scenario-unhappy`, `--partition-happy` (alias `--happy`), `--partition-unhappy`, `--unhappy`

#### `evaluate_results.py`
- `--sop-graph`: Path to graph_output.json
- `--parallel N`: Number of parallel processes (recommended: 8)
- Flags: `--all`, `--scenario-happy`, `--scenario-unhappy`, `--partition-happy` (alias `--happy`), `--partition-unhappy`, `--unhappy`

## Common Issues & Solutions

### 1. "GEMINI_API_KEY not set"
**Solution**: Add to `.env` file:
```bash
GEMINI_API_KEY=...
```

### 2. Memory tool validation errors during simulation
**Solution**: Ensure `simulation_runner.py` line 170 patches the correct path:
```python
patch('src.engine.workers_v2.tools.memory.get_memory_tools', new=fake_memory_tools)
```

### 3. Composo evaluation returns 0.00 scores
**Solution**: Check `evaluate_results.py` handles `EvaluationResponse` correctly:
```python
eval_response = composo_client.evaluate(...)
# Access: eval_response.score and eval_response.explanation (not eval_response.results)
```

### 4. Triggering and halting stakeholders are the same
**Solution**: Re-run `sop_process.py` with updated `sop_config.yaml` (constraint added at line 60)

### 5. Duplicate scenarios generated
**Solution**: Re-run `sop_process.py` with updated `sop_config.yaml` (constraint added at line 61)

## Clean-Up Behavior

### `sop_process.py` (line 12-44)
Automatically cleans before re-analysis:
- Clears all `scenario_happy_path`, `scenario_unhappy_path`, `*_response` fields
- Clears all `happy_path`, `partition_unhappy_path`, `unhappy_paths` in partitions
- Clears all `transitions`, `context`, `evaluation_summary` fields
- Preserves: `worker_config`, stakeholder structure, partition definitions

### `scenario_generator.py` (with `--clear-results`)
Clears scenario-related fields only:
- Clears scenario paths and partition paths
- Preserves: responses, evaluations, transitions

## Evaluation Criteria Structure

### Trace Criteria (Runtime evaluation during simulation)
```yaml
trace_criteria:
  - "Reward agents that follow the procedural sequence outlined in the SOP."
  - "Reward agents that select the appropriate tool with all required parameters."
  - "Reward agents that base responses on retrieved information without fabricating details."
```

### E2E Criteria (Post-simulation evaluation)
```yaml
e2e_evaluation_source: "both"  # Options: "both", "internal_dialog", "user_facing"
e2e_criteria:
  - "Reward responses whose internal reasoning accurately identifies the current SOP state and trigger conditions, and whose external response (or lack thereof) correctly aligns with what the SOP requires..."
```

## Architecture Notes

### Conversation History Management
- **During Simulation** (`simulation_runner.py`):
  - AGNO's `team` object maintains history internally
  - Each `run_episode()` call creates a new team instance (fresh history)
  - History builds up across turns within the same episode

- **During Evaluation** (`evaluate_results.py`):
  - Manually builds `conversation_history` list
  - Appends `user_msg` + `assistant_internal` after each turn
  - Passes history to next turn's evaluation
  - History resets for each new path (partition/scenario)

### Context vs History
- **Context** (`partition_context`): Pre-computed descriptions of previous partitions (static)
- **History** (`conversation_history`): Accumulated messages within current conversation (dynamic)

### Parallel Execution
- ✅ **Can parallelize**: Different scenarios, different partitions, different unhappy paths
- ❌ **Cannot parallelize**: Turns within a single path (must be sequential for history)

## Testing Best Practices

1. **Start Small**: Test with `--scenario-happy` first, then expand
2. **Use Parallel**: `--parallel 4` for simulation, `--parallel 8` for evaluation
3. **Check Constraints**: Verify no same-stakeholder partitions or duplicate scenarios
4. **Monitor Tokens**: Watch Composo evaluation token usage (can be expensive)
5. **Iterate**: Re-run `sop_process.py` if SOP changes or constraints violated

## File Output Structure

```
sop/Customer_Service_Demo/
├── 20251111_0236/                    # Timestamp-based run directory
│   ├── graph_output.json             # All-in-one: graph + scenarios + responses + evaluations
│   └── evaluation_summary.json       # (Generated by evaluate_results.py)
└── sop.txt                           # Original SOP text
```

## Key Design Decisions

1. **Per-scenario storage**: Paths stored in each scenario object (not at graph level)
2. **Dual field evaluation**: Both `internal` reasoning and `response` action evaluated together
3. **Single comprehensive criterion**: One criterion for both reasoning and action quality
4. **Mock-based simulation**: All real tools replaced to avoid side effects
5. **Conversation history with internal only**: History uses reasoning, not external responses
6. **Pre-context for partitions**: Context shows previous work completed
7. **Clean slate for scenarios**: End-to-end paths start fresh without pre-context

## Recent Changes Summary

### Pipeline Script Enhancements (2025-01-12)
- Added: `--start` flag to resume pipeline from specific step (snapshot|sop|scenarios|simulation|evaluation|report)
- Added: `--dir` flag to specify custom directory for graph_output.json (useful for resuming from timestamped runs)
- Fixed: Proper flag filtering - each script only receives flags it accepts (scenario_generator only gets --parallel, simulation/evaluation get test path flags)
- Fixed: `simulation_runner.py` converts dict to string when communication tool has no body/message parameter
- Changed: `run_pipeline.sh` converts relative paths to absolute paths for --dir flag

### Backend Independence & Tool Call Tracking (2025-01-12)
- **Breaking**: Removed all backend dependencies from test framework
- Added: `agno_mocks.py` - Standalone AGNO agent/team implementation
- Added: Pure AGNO-based simulation without backend imports
- Added: Tool call tracking by agent name (`tool_calls_by_agent`)
- Added: Consistent trace structure with/without tracing enabled
- Changed: Tool calls included in e2e evaluation context (fixes low Composo scores)
- Changed: `trace` field now always contains `tool_calls_by_agent` (even when tracing disabled)
- Fixed: `aggregate_agent_stats.py` handles None values for trace and evaluation_summary
- Fixed: `aggregate_agent_stats.py` now extracts scores from `evaluation.composo_scores` when `evaluation_summary` doesn't exist
- Fixed: `evaluate_results.py` only uses e2e scores for orchestrator when tracing is disabled (no subagent score inference)
- Changed: Reports work with any combination of test runs (scenario/partition/happy/unhappy)

### Agent-Level Evaluation & Insights (2025-01-12)
- Added: Agent-level score aggregation at all hierarchical levels (partition/scenario/SOP)
- Added: `aggregate_agent_stats.py` - Non-LLM statistical aggregation script
- Added: AI-powered insights generation using Gemini 2.5 Pro
- Added: Agent-centric trace organization (`by_agent` in trace data)
- Added: LLM-generated mock tool responses with Gemini JSON mode
- Added: Per-tool `use_llm_generation` configuration flag
- Changed: Evaluation summaries now include `by_agent` breakdown

### SOP Analysis Constraints (2025-01-11)
- Added: No same triggering/halting stakeholder rule
- Added: No duplicate scenarios rule
- Added: Auto-cleanup of generated fields before re-analysis

### Scenario Generation Format (2025-01-11)
- Required: `from:` and `via:` prefixes on all messages
- Required: Messages only from triggering_stakeholders
- Refined: Prompts condensed to remove duplication

### Evaluation Enhancement (2025-01-11)
- Changed: Dual field evaluation (internal + response combined)
- Changed: Conversation history building with internal dialog only
- Changed: Retry mechanism to 20 attempts with exponential backoff
- Fixed: Composo response parsing (`eval_response.score` not `.results`)
- Added: Pre-context and conversation history management

### Mock Tool Patching (2025-01-11)
- Fixed: Memory tools patch path to `src.engine.workers_v2.tools.memory.get_memory_tools`