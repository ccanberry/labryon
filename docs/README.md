# AI Agent Testing Framework

This is an automated testing framework for evaluating the performance of multi-agent AI systems that are designed to follow Standard Operating Procedures (SOPs). It provides a structured, six-step pipeline for taking an agent's configuration from the database, generating a comprehensive suite of test cases, simulating the agent's behavior in a mocked environment, and evaluating its performance to generate actionable insights.

## How It Works

The framework is built around a central file, `graph_output.json`, which is progressively enriched by a series of Python scripts.

1.  **`snapshot.py`**: Captures the initial agent configuration from the database.
2.  **`sop_process.py`**: Uses an LLM to analyze the SOP and build a structured workflow graph.
3.  **`scenario_generator.py`**: Uses an LLM to generate happy and unhappy path test cases.
4.  **`simulation_runner.py`**: Executes the agent against the test cases in a mocked environment.
5.  **`evaluate_results.py`**: Scores the agent's performance using the Composo evaluation framework.
6.  **`aggregate_agent_stats.py`**: Generates summary reports and AI-powered insights.

The behavior of this pipeline is controlled by a set of YAML files (`sop_config.yaml`, `llm_generator_config.yaml`, `evaluation_criteria.yaml`, `mock_tool_config.yaml`), which define everything from LLM prompts to mock tool responses.

## Core Concepts

The framework is built around a few key concepts:

*   **Scenario**: Represents a complete, linear, end-to-end workflow or user story that the AI agent is expected to handle (e.g., "Handle a customer quote request from start to finish"), one AI_Worker can have multiple linear scenarios depending on the SOP.
*   **Partition**: A single, discrete step or "work episode" within a scenario. A partition begins with a trigger from an external stakeholder and ends when the agent is waiting for a response, this could be "Collecting offers from Carriers". A scenario can have one to multiple partitions.
*   **Test Paths**: The framework generates different types of test paths to evaluate the agent's robustness:
    *   **Happy Paths**: The ideal, error-free execution of a scenario or partition.
    *   **Unhappy Paths**: Variations that introduce problems, edge cases, or missing information to test the agent's error-handling and recovery capabilities.
    *   **Transitions**: Variations that introduce Partition-wise problems where the agent shall switch back to previous partitions (e.g. updated RFQ during Carrier quote collection or Carrier cancellation during teams-approval).

These concepts allow for independent testing of both the agent's ability to follow the overall workflow (scenarios) and its performance on individual tasks (partitions) under various conditions.

## Prerequisites

> **📖 For detailed installation instructions, see [INSTALLATION.md](../INSTALLATION.md)**

### 1. System Requirements

```bash
# Install Python 3.11+ (if not already installed)
python3 --version  # Should be 3.11 or higher

# No system dependencies required! All packages are pure Python.
```

### 2. Set Up Virtual Environment

```bash
cd labryon

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On Linux/macOS
# Or on Windows: venv\Scripts\activate

# Upgrade pip
pip install --upgrade pip
```

### 3. Install Dependencies

```bash
# Make sure you're in labryon/ directory with activated venv
pip install -r requirements.txt
```

### 4. Start Infrastructure Services

Start database and redis (required for accessing worker configs from database):

```bash
# Start services from project root
cd /path/to/project-root
docker-compose up -d db redis

# Verify services are running
docker-compose ps
```

**📝 Note:** Labryon scripts can now run from the `labryon/` directory since they use their own `.env` file. You only need to be in project root for `docker-compose` commands.

### 5. Configure Environment Variables

Create `.env` file in the **labryon directory** (`labryon/.env`):

```bash
# Copy the example file
cd labryon
cp .env.example .env

# Edit with your credentials
nano .env  # or use your preferred editor
```

**Required variables:**
```bash
# Database (for fetching worker configs)
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=workflow_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password

# LLM API Keys
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...

# Composo (optional, for tracing)
COMPOSO_API_KEY=...
```

**Note**: Labryon now uses its own `.env` file in `labryon/` directory, keeping it independent from the main platform configuration.

## Quick Start

Once prerequisites are set up, you can run the complete pipeline:

```bash
# Activate virtual environment and run from labryon directory
cd labryon
source venv/bin/activate

# Run complete pipeline
./run_pipeline.sh --worker "Customer Service Demo" --partition-happy --insights --parallel 4
```

## The Pipeline
### One Command
```bash
# Activate venv and run from labryon directory
cd labryon
source venv/bin/activate

# Run complete pipeline
./run_pipeline.sh --worker "Customer Service Demo" --partition-happy --insights --parallel 4

# Resume from a specific timestamped directory
./run_pipeline.sh --dir sop/Customer_Service_Demo/20251112_1131 --start simulation --scenario-happy --insights

# Resume from latest run for a worker
./run_pipeline.sh --worker "Customer Service Demo" --start evaluation --scenario-happy --insights
```

### Step-by-Step

```bash
# Activate venv and run from labryon directory
cd labryon
source venv/bin/activate

# Each run of the pipeline on the common JSON will erase or reset the data filled by subsequent modules
# (for example, simulation_runner.py resets evaluation scores, and sop_process clears created scenarios)

# 1. Snapshot: Load worker from database → creates initial graph_output.json
python3 src/snapshot.py --override # Display available workers and select by index
python3 src/snapshot.py --worker "Customer Service Demo" --override
# Output: sop/Customer_Service_Demo/graph_output.json (with --override)
# Or: sop/Customer_Service_Demo/YYYYMMDD_HHMM/graph_output.json (without --override)
# Populates: worker_config {sop, agents[], knowledge_bases[], tools}

# 2. SOP Analysis: Extract workflow structure → enriches graph_output.json
python3 src/sop_process.py --sop-graph sop/Customer_Service_Demo/graph_output.json
# Populates: stakeholders[], scenarios[] with partitions[]

# 3. Scenario Generation: Create test messages → enriches graph_output.json
python3 src/scenario_generator.py --sop-graph sop/Customer_Service_Demo/graph_output.json
# Populates: scenarios with test messages

# 4. Simulation: Run agents with mocks → enriches graph_output.json
python3 src/simulation_runner.py --sop-graph sop/Customer_Service_Demo/graph_output.json --scenario-happy --parallel 8
# Populates: scenario.happy_path_response[] and unhappy_path[] with agent responses + traces (optional)

# 5. Evaluation: Score performance → enriches graph_output.json
python3 src/evaluate_results.py --sop-graph sop/Customer_Service_Demo/graph_output.json --scenario-happy --scenario-unhappy --parallel 8
# Populates: scenario.evaluation_summary with scores by agent

# 6. Insights: AI recommendations → generates separate files
python3 src/aggregate_agent_stats.py sop/Customer_Service_Demo/graph_output.json --insights --report
# Creates: agent_stats.json, agent_stats_report.txt, agent_stats_insights.md
```

## Data Structure

Everything flows through a single **`graph_output.json`** file:

```
SOPGraph (root)
├── worker_name: "Customer Service Demo"
├── worker_config: {sop, agents[], knowledge_bases[], tools}  ← Step 1
├── stakeholders: []                                            ← Step 2
└── scenarios: []                                               ← Step 2
    └── Scenario
        ├── scenario_name: "Shipment is In-Transit"
        ├── scenario_happy_path: ["message1", "message2"]       ← Step 3 (scenario-level)
        ├── scenario_happy_path_response: [AgentResponse]       ← Step 4 (scenario-level)
        └── partitions: []
            └── Partition
                ├── partition_name: "Receive Query"
                ├── triggering_stakeholder: "Customers"
                ├── halting_stakeholder: "System"
                ├── happy_path: ["user message"]                ← Step 3 (partition-level)
                ├── partition_unhappy_path: ["error message"]   ← Step 3 (partition-level)
                ├── unhappy_paths: [...]                        ← Step 3 (individual variations)
                ├── happy_path_response: []                     ← Step 4
                │   └── AgentResponse
                │       ├── response: ["email sent to..."]     (external communication)
                │       ├── internal: ["retrieved status..."]  (agent reasoning)
                │       └── trace: {by_agent: {...}}           (runtime evaluation)
                ├── partition_unhappy_path_response: []         ← Step 4
                ├── unhappy_path_responses: [[...]]             ← Step 4
                └── evaluation_summary: {...}                   ← Step 5
                    └── by_agent: {
                        "orchestrator": {
                            by_criterion: {"Tool selection": 0.85},
                            overall: 0.85
                        }
                    }
```

## Test Path Types

| Flag | What It Tests | Example |
|------|--------------|---------|
| `--scenario-happy` | End-to-end happy flow (no pre-context) | Start → Finish smoothly |
| `--partition-happy` | Per-partition happy (with pre-context) | Each step works correctly |
| `--partition-unhappy` | Per-partition combined errors | Missing data, tool failures |
| `--unhappy` | Individual error variations | 5+ different failure modes |

**Pre-context**: Previous partitions' descriptions (what work was already done)

## Output Files

```
labryon/sop/Customer_Service_Demo/
├── graph_output.json              # Complete test data (all steps)
├── agent_stats.json               # Statistical aggregation
├── agent_stats_report.txt         # Performance summary
└── agent_stats_insights.md        # AI recommendations
```

**Note**: Example output files are committed in `@labryon/sop/Customer_Service_Demo/` (using `--override` flag).
When using timestamps, outputs go to `labryon/sop/Customer_Service_Demo/YYYYMMDD_HHMM/`.

**Note**: Reports work with any combination of test runs. You don't need to run all test types to get insights:
- Run only `--scenario-happy`? Reports show orchestrator scores from scenario-level evaluations
- Run only `--partition-happy`? Reports show scores from partition-level evaluations
- Mix different test types? Reports aggregate all available data
- Tracing enabled? Get per-agent breakdowns; disabled? Get overall orchestrator scores

## Evaluation Hierarchy

Performance is scored at 3 levels:

```
SOP Level
├── by_agent: {agent_name: {overall: 0.85, by_criterion: {...}, by_scenario: {...}}}
└── scenarios[]
    └── Scenario Level
        ├── by_agent: {agent_name: {overall: 0.87, by_criterion: {...}, by_partition: {...}}}
        └── partitions[]
            └── Partition Level
                └── by_agent: {agent_name: {overall: 0.90, by_criterion: {
                    "Tool selection": 0.92,
                    "Procedural sequence": 0.88,
                    "Information accuracy": 0.90
                }}}
```

Each agent gets individual scores for each evaluation criterion.

## Common Flags

```bash
--worker "Worker Name"      # Required: Worker from database
--partition-happy           # Test partition-level happy paths (recommended)
--partition-unhappy         # Test partition-level unhappy paths
--scenario-happy            # Test end-to-end happy path
--unhappy                   # Test individual error variations
--all                       # Test everything
--parallel 4                # Number of parallel processes
--insights                  # Generate AI recommendations
--threshold 0.7             # Score threshold for insights (0.0-1.0)
--override                  # Skip timestamp, save to worker directory
--start <step>              # Start from specific step (snapshot|sop|scenarios|simulation|evaluation|report)
--dir <path>                # Use specific directory (e.g., labryon/sop/Worker/20251112_1131)
```

## Parallel Execution

The framework is designed to significantly speed up test runs by executing independent tests in parallel using the `--parallel <N>` flag, where `N` is the number of processes, scripts will automatically decide parallelization strategy considering current number of scenarios and partitions.

*   **What Can Be Parallelized**: Different scenarios, different partitions within a scenario, and the various test paths (happy, unhappy) for each partition are all treated as independent tasks that can be run concurrently.
*   **What Cannot Be Parallelized**: The sequence of conversational turns *within* a single test path must be executed sequentially, as the agent's state and conversation history are dependent on the previous turn.
*   **Context Handling**: For partition-level tests, the framework automatically passes the required `pre-context` (a summary of the work done in previous partitions) to each parallel process. This ensures that even though partitions are run concurrently, the agent has the necessary background information to perform its task correctly.

## Quick Examples

```bash
# Basic test run
./run_pipeline.sh --worker "Customer Service Demo" --partition-happy

# Full test with insights
./run_pipeline.sh --worker "Customer Service Demo" --all --parallel 8 --insights

# Fast iteration (no timestamp) - run from labryon directory with venv activated
cd labryon
source venv/bin/activate

python src/snapshot.py --worker "Customer Service Demo"
GRAPH="sop/Customer_Service_Demo/$(ls -t sop/Customer_Service_Demo/ | head -1)/graph_output.json"
python src/sop_process.py --sop-graph "$GRAPH"
python src/scenario_generator.py --sop-graph "$GRAPH" --parallel 4
python src/simulation_runner.py --sop-graph "$GRAPH" --partition-happy --parallel 4
python src/evaluate_results.py --sop-graph "$GRAPH" --partition-happy --parallel 8
python src/aggregate_agent_stats.py "$GRAPH" --insights --report
```

## Troubleshooting

**"Worker not found"**: Check database, run `python src/snapshot.py` to list workers

**"Redis connection refused"**: Run `docker-compose up -d db redis` in project root

**"GEMINI_API_KEY not set"**: Add to `.env` in labryon/

**Low scores**: Check SOP clarity, mock tool data, evaluation criteria

## Documentation

-   **This file**: Quick usage guide
-   **`docs/TLDR.md`**: Ultra-short reference
-   **`docs/TESTING_FRAMEWORK_DOCUMENTATION.md`**: Complete technical details
-   **`docs/CLAUDE.md`**: Developer/AI assistant context

## Scripts & Configuration

This section details the individual scripts that make up the testing pipeline and the YAML files that configure their behavior.

### `snapshot.py`

*   **Command**:
    ```bash
    python src/snapshot.py --worker "Customer Service Demo"
    # Optional: --override to skip timestamp folder creation
    # If --worker is omitted, an interactive list is shown.
    ```
*   **Arguments**:
    *   `--worker <name>`: The name of the worker to snapshot.
    *   `--override`: Overwrite the latest snapshot instead of creating a new timestamped directory.
*   **Input**: Database connection (via `data_loader.py`).
*   **Output**: `sop/<Worker_Name>/YYYYMMDD_HHMM/graph_output.json`.
*   **YAML Configuration**: `config/sop_config.yaml` (for default worker name), `config/mock_tool_config.yaml` (for hardcoded tool bindings).

### `sop_process.py`

*   **Purpose**: Analyzes the SOP text within `graph_output.json` using an LLM to extract the workflow structure. It populates the file with stakeholders, scenarios, and partitions.
*   **Command**:
    ```bash
    python src/sop_process.py --sop-graph sop/Customer_Service_Demo/20251112_1430/graph_output.json
    ```
*   **Arguments**:
    *   `--sop-graph <path>`: Path to the `graph_output.json` file.
*   **YAML Configuration**: `sop_config.yaml`
    *   `llm_provider`: Specifies the LLM provider to use for analysis (e.g., "google", "anthropic").
    *   `anthropic_model_name` / `google_model_name`: The specific model to use.
    *   `graph_analysis_prompt`: The prompt used by the LLM to analyze the SOP.

### `scenario_generator.py`

*   **Command**:
    ```bash
    python src/scenario_generator.py --sop-graph sop/Customer_Service_Demo/20251112_1430/graph_output.json --parallel 4
    ```
*   **Arguments**:
    *   `--sop-graph <path>`: Path to the `graph_output.json` file.
    *   `--parallel <N>`: Number of parallel processes to use for generation (default: 1).
*   **Input**: `graph_output.json` (with SOP graph).
*   **Output**: Updates `graph_output.json` with generated test messages.
*   **YAML Configuration**:
    *   `config/llm_generator_config.yaml`: Prompts and LLM settings.
    *   `partition_configs.yaml` (optional, in graph directory): Per-partition generation settings.
        *   Location: Same directory as the `graph_output.json` file (e.g., `labryon/sop/Customer_Service_Demo/20251112_1430/partition_configs.yaml`)
        *   This file is **optional** - if not present, defaults are used (`limit_variations: 3`)
        *   Create this file manually to customize scenario generation per partition
        *   `defaults`: Default settings for scenario generation (e.g., `limit_variations: 5`).
        *   `redirectors_enabled`: Master switch to enable/disable redirector examples (default: true).
        *   `scenario_configs`: A nested list structure mapping scenarios and partitions **by index** (not by name):
          - First level: list of scenarios (Scenario 0, Scenario 1, etc.)
          - Second level: list of partition configs within each scenario (Partition 0, Partition 1, etc.)
        *   Example structure:
          ```yaml
          defaults:
            limit_variations: 4
          redirectors_enabled: false
          scenario_configs:
            # Configs for Scenario 0 (first scenario)
            - - limit_variations: 2  # Partition 0 of Scenario 0
                redirector_examples: []
              - limit_variations: 3  # Partition 1 of Scenario 0
                redirector_examples: ["example1"]
            # Configs for Scenario 1 (second scenario)
            - - limit_variations: 5  # Partition 0 of Scenario 1
          ```

### `simulation_runner.py`

*   **Command**:
    ```bash
    python src/simulation_runner.py --sop-graph sop/Customer_Service_Demo/20251112_1430/graph_output.json --partition-happy --parallel 4
    ```
*   **Arguments**:
    *   `--sop-graph <path>`: Path to the `graph_output.json` file.
    *   `--parallel <N>`: Number of parallel processes (default: 1).
    *   `--all`: Run all available simulation paths.
    *   `--scenario-happy`: Run end-to-end happy path.
    *   `--scenario-unhappy`: Run end-to-end unhappy path.
    *   `--partition-happy` (or `--happy`): Run per-partition happy paths.
    *   `--partition-unhappy`: Run per-partition unhappy paths.
    *   `--unhappy`: Run individual unhappy path variations.
    *   `--scenario <ID>`: Run only a specific scenario (0-indexed) or "all".
    *   `--debug`: Enable debug logging.
*   **Input**: `graph_output.json` (with scenarios).
*   **Output**: Updates `graph_output.json` with agent responses and traces.
*   **YAML Configuration**: `config/mock_tool_config.yaml`, `config/evaluation_criteria.yaml` (for tracing settings).
    *   `default_success_rate`, `default_use_llm_generation`: Default behavior for mock tools.
    *   `mock_tool_responses`: Defines static success and failure responses for each tool, along with their success rate and whether to use an LLM for dynamic response generation.
    *   `enable_tracing`: Enables or disables real-time tracing with Composo.
    *   `trace_criteria`: The criteria used for runtime evaluation during the simulation.

### `evaluate_results.py`

*   **Purpose**: Evaluates the agent's performance based on the simulation results recorded in `graph_output.json`. It uses Composo to score the agent's reasoning and responses against predefined criteria.
*   **Command-line Arguments**:
    *   `--sop-graph <path-to-graph_output.json>`: The path to the `graph_output.json` file.
    *   `--parallel <N>`: The number of parallel processes to use for evaluation.
    *   Flags to specify which test paths to evaluate.
*   **YAML Configuration**: `evaluation_criteria.yaml`
    *   `e2e_evaluation_source`: Specifies what part of the agent's output to use for evaluation ("both", "internal_dialog", "user_facing").
    *   `e2e_criteria`: The criteria used for end-to-end evaluation after the simulation is complete.

### `aggregate_agent_stats.py`

*   **Purpose**: Aggregates the evaluation scores from `graph_output.json` and generates summary reports.
*   **Command-line Arguments**:
    *   `graph_file`: The path to the `graph_output.json` file (positional argument).
    *   `--output <output-file>`: The path to save the aggregated statistics in JSON format (default: `agent_stats.json`).
    *   `--report`: If specified, generates a human-readable summary report (`.txt`).
    *   `--insights`: If specified, uses an LLM to generate AI-powered insights and recommendations (`.md`).
    *   `--threshold <N>`: The score threshold below which evaluations are considered "low-scoring" for the insights generation (default: 0.7).
*   **YAML Configuration**: None.

### Helper Modules

*   **`agent_metadata.py`**: Static registry of agent descriptions and prompts, used to mock backend agent definitions without dependencies.
*   **`agno_mocks.py`**: Standalone implementations of Agno agents and teams for testing, allowing the framework to run without importing the actual backend codebase.
*   **`composo_wrapper.py`**: A wrapper for the Composo tracing library, handling initialization and context management for tracing agent execution.
*   **`data_loader.py`**: Handles database connections and fetches worker configurations (SOPs, agents, tools) from the database to create the initial snapshot.
*   **`data_models.py`**: Defines Pydantic models for the `graph_output.json` structure, ensuring data consistency across the pipeline.
*   **`mock_tools.py`**: Creates and manages mock versions of tools. It supports both static responses and LLM-generated dynamic responses for realistic simulation.
*   **`prompt_manager.py`**: Manages system prompts, attempting to fetch them from Langfuse first and falling back to local templates if necessary.
*   **`prompt_templates.py`**: Contains local fallback templates for system prompts to ensure the framework runs even without external prompt management services.

## Contributing

To contribute to the testing framework, please follow these guidelines:

1.  **Run Linting**: Ensure your code adheres to the project's style guidelines by running the linter.
2.  **Follow Conventional Commits**: Structure your commit messages according to the Conventional Commits specification.
3.  **Submit a Pull Request**: Create a pull request with a clear description of your changes.

## License

This project is licensed under the terms of the MIT license. See the `LICENSE` file in the root directory for more information.

