# TL;DR: AI Agent Testing Framework

## The 6-Step Pipeline

The framework tests AI agents following Standard Operating Procedures (SOPs) through a comprehensive automated pipeline.

**Workflow:**
`Snapshot` → `SOP Analysis` → `Scenario Generation` → `Simulation` → `Evaluation` → `Insights`

**Central File:**
All data flows through a single `graph_output.json` file that gets enriched at each step.

---

## The Six Steps

### Step 1: Create Worker Snapshot

Loads the worker configuration from the database (SOP, agents, tools, knowledge bases).

-   **Script**: `snapshot.py`
-   **Input**: Worker name from database
-   **Output**: `sop/<Worker_Name>/YYYYMMDD_HHMM/graph_output.json` (initial structure)
-   **Command**:
    ```bash
    python src/snapshot.py --worker "Customer Service Demo"
    # Creates: sop/Customer_Service_Demo/20251112_1430/graph_output.json
    ```

---

### Step 2: Analyze SOP Structure

Uses an LLM to analyze the SOP and extract the workflow graph (scenarios, partitions, stakeholders).

-   **Script**: `sop_process.py`
-   **Input**: `graph_output.json` (from snapshot)
-   **Output**: Enriched `graph_output.json` with workflow structure
-   **What it extracts**:
    -   **Scenarios**: High-level workflows (e.g., "Shipment In-Transit", "POD Missing")
    -   **Partitions**: Individual work episodes within scenarios (trigger → completion)
    -   **Stakeholders**: External entities that interact with the system
-   **Command**:
    ```bash
    python src/sop_process.py sop/Customer_Service_Demo/20251112_1430/graph_output.json
    ```

---

### Step 3: Generate Test Scenarios

Generates realistic test messages for happy and unhappy paths at both scenario and partition levels.

-   **Script**: `scenario_generator.py`
-   **Input**: `graph_output.json` (with workflow structure)
-   **Output**: Enriched `graph_output.json` with test messages
-   **What it generates**:
    -   **Scenario paths**: End-to-end tests from start to finish
    -   **Partition paths**: Per-partition tests with pre-context
    -   **Unhappy variations**: Individual error/edge case tests
-   **Command**:
    ```bash
    python src/scenario_generator.py --sop-graph graph_output.json --parallel 4
    ```

---

### Step 4: Run Simulations

Runs agents through test scenarios in a mocked environment with tool call tracking.

-   **Script**: `simulation_runner.py`
-   **Input**: `graph_output.json` (with test messages)
-   **Output**: Enriched `graph_output.json` with agent responses and traces
-   **Features**:
    -   Pure AGNO-based simulation (no backend dependencies)
    -   All tools replaced with mocks (configurable fake data)
    -   Optional real-time Composo tracing for tool selection and SOP adherence
    -   LLM-generated realistic mock responses (optional, per-tool)
    -   Tool calls always tracked by agent name in `tool_calls_by_agent`
-   **Command**:
    ```bash
    python src/simulation_runner.py graph_output.json --partition-happy --parallel 4
    ```

---

### Step 5: Evaluate Results

Performs comprehensive evaluation of agent performance using Composo.

-   **Script**: `evaluate_results.py`
-   **Input**: `graph_output.json` (with agent responses)
-   **Output**: Enriched `graph_output.json` with evaluation scores
-   **What it evaluates**:
    -   **Internal reasoning**: Did the agent understand the state and plan correctly?
    -   **Tool usage**: Were the right tools called with correct parameters? (extracted from `tool_calls_by_agent`)
    -   **External responses**: Were the actions/communications appropriate?
    -   **Per-agent scoring**: Individual scores for each agent at each level
-   **Command**:
    ```bash
    python src/evaluate_results.py graph_output.json --partition-happy --parallel 8
    ```

---

### Step 6: Generate AI Insights

Uses Gemini 2.5 Pro to analyze low scores and provide improvement recommendations.

-   **Script**: `aggregate_agent_stats.py`
-   **Input**: `graph_output.json` (with evaluations)
-   **Output**:
    -   `agent_stats.json` - Statistical aggregation
    -   `agent_stats_report.txt` - Human-readable summary
    -   `agent_stats_insights.md` - AI-powered analysis and recommendations
-   **What it provides**:
    -   Performance summary by agent, criterion, and scenario
    -   Root cause analysis of failures
    -   Prioritized recommendations (SOP/Prompt/Tool/Architecture)
    -   Quick wins for immediate improvements
-   **Command**:
    ```bash
    python src/aggregate_agent_stats.py graph_output.json --insights --report
    ```

---

## Output Structure

```
sop/Customer_Service_Demo/
├── 20251112_1430/                     # Timestamp: YYYYMMDD_HHMM
│   ├── graph_output.json              # Complete test data (enriched at each step)
│   ├── agent_stats.json               # Aggregated statistics
│   ├── agent_stats_report.txt         # Performance summary
│   └── agent_stats_insights.md        # AI recommendations
└── 20251112_0900/                     # Previous run
    └── ...
```

**Key Point**: All data lives in the single `graph_output.json` file, which gets progressively enriched through the pipeline.

---

## Configuration Files

-   **`config/sop_config.yaml`**: SOP analysis prompts and LLM settings
-   **`config/llm_generator_config.yaml`**: Scenario generation prompts and temperatures
-   **`config/evaluation_criteria.yaml`**: Evaluation criteria (runtime + post-simulation)
-   **`config/mock_tool_config.yaml`**: Mock tool responses and LLM generation settings

---

## Quick Start

```bash
cd labryon
source venv/bin/activate

# One command to run the full pipeline:
python src/snapshot.py --worker "Customer Service Demo"
GRAPH="sop/Customer_Service_Demo/$(ls -t sop/Customer_Service_Demo/ | head -1)/graph_output.json"
python src/sop_process.py "$GRAPH"
python src/scenario_generator.py --sop-graph "$GRAPH" --parallel 4
python src/simulation_runner.py "$GRAPH" --partition-happy --parallel 4
python src/evaluate_results.py "$GRAPH" --partition-happy --parallel 8
python src/aggregate_agent_stats.py "$GRAPH" --insights --report
```

Or use the convenience script:
```bash
cd labryon
source venv/bin/activate
./run_pipeline.sh --worker "Customer Service Demo" --partition-happy --insights
```

---

## What Does It Test?

The framework evaluates AI agents across multiple dimensions:

1.  **Tool Selection & Usage** (Runtime Tracing)
    -   Did the agent call the right tools?
    -   Were parameters correct and complete?
    -   Were tool calls tracked by agent name?

2.  **SOP Adherence** (Runtime Tracing)
    -   Did the agent follow the procedural sequence?
    -   Were state transitions correct?
    -   Were trigger conditions properly identified?

3.  **Reasoning Quality** (Post-Simulation)
    -   Did internal reasoning accurately assess the situation?
    -   Were decisions properly justified?

4.  **Response Quality** (Post-Simulation)
    -   Were external communications appropriate?
    -   Did responses align with SOP requirements?
    -   Were stakeholders addressed correctly?

5.  **Per-Agent Performance**
    -   Individual scoring for orchestrator vs. specialist agents
    -   Agent-level breakdowns at partition/scenario/SOP levels
    -   Identification of which agents need improvement

By combining runtime tracing with post-simulation evaluation, the framework provides a complete picture of multi-agent system performance.