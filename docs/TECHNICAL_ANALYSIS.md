# In-Depth Technical Analysis of the AI Agent Testing Framework

## 1. Introduction

This document provides a comprehensive, step-by-step technical breakdown of the AI Agent Testing Framework. The entire testing pipeline is designed around a single, central file: `graph_output.json`. This file acts as a living document, being progressively enriched with data at each stage of the process, from initial configuration to final evaluation.

The pipeline consists of six main steps, executed in a specific temporal order. Each step is performed by a dedicated Python script and adds a new layer of information to the `graph_output.json`, providing a complete and traceable record of the entire test run.

---

## 2. The 6-Step Pipeline

The testing process flows in the following sequence:
`Snapshot` → `SOP Analysis` → `Scenario Generation` → `Simulation` → `Evaluation` → `Insights`

Below is a detailed explanation of each step and the corresponding fields that are introduced or populated in the `graph_output.json` file.

### Step 1: `snapshot.py` - Creating the Initial Worker Snapshot

This is the entry point of the testing pipeline. The `snapshot.py` script is responsible for creating the initial `graph_output.json` file.

*   **Purpose**: To capture the complete configuration of a specific AI worker from the database and store it in a structured JSON format.
*   **Process**: The script connects to the database, fetches the specified worker's configuration, and serializes it into the initial `graph_output.json`. This file is saved in a timestamped directory under `labryon/sop/<Worker_Name>/`.

#### Command Example
```bash
cd labryon
source venv/bin/activate
python src/snapshot.py --worker "Customer Service Demo"
```

#### JSON Fields Introduced:

At this stage, the `graph_output.json` contains the foundational configuration of the worker.

```json
{
  "worker_name": "Customer Service Demo",
  "worker_config": {
    "sop": "# RFQ Handling SOP...",
    "agents": [
      {
        "name": "impargo-expert",
        "description": "An integration agent for the Impargo logistics platform...",
        "prompt": "You are an Impargo Expert Agent...",
        "tool_bindings": { ... }
      }
    ],
    "knowledge_bases": [
      {
        "kb_id": "55f92e86-0194-4999-bcd0-a63904769f88",
        "document_name": "SOP_emirates_airlines_ratesheet.txt",
        "content": "Our lanes and their prices..."
      }
    ],
    "supervisor_tools": { ... }
  },
  "stakeholders": [],
  "scenarios": [],
  "evaluation_summary": null
}
```

*   `worker_name`: The name of the worker being tested.
*   `worker_config`: An object containing the entire configuration of the worker.
    *   `sop`: The full text of the Standard Operating Procedure that the agent is designed to follow.
    *   `agents`: A list of sub-agent configurations available to the supervisor agent.
    *   `knowledge_bases`: A list of knowledge base documents that the agent can access.
    *   `supervisor_tools`: The tool bindings available to the main supervisor agent.
*   `stakeholders`, `scenarios`, `evaluation_summary`: These fields are initialized as empty or null, to be populated by subsequent steps.

### Step 2: `sop_process.py` - Analyzing the SOP Structure

This step uses a Large Language Model (LLM) to deconstruct the human-readable SOP into a machine-readable workflow graph.

*   **Purpose**: To analyze the SOP text and extract the high-level workflow, including the different scenarios, the individual work partitions within those scenarios, and the stakeholders involved.
*   **Process**: The script reads the `sop` from the `worker_config` in `graph_output.json`, sends it to an LLM with a specialized prompt, and parses the response to populate the workflow structure.

#### Command Example
```bash
cd labryon
source venv/bin/activate
python src/sop_process.py --sop-graph sop/<Worker_Name>/<Timestamp>/graph_output.json
```

#### JSON Fields Populated:

```json
{
  ...
  "stakeholders": [
    {
      "name": "Customer",
      "description": "The entity that initiates the process..."
    }
  ],
  "scenarios": [
    {
      "scenario_name": "Standard RFQ Handling",
      "description": "The complete, successful workflow for handling an RFQ...",
      "stakeholders": ["Customer", "Carriers", "Internal Team"],
      "partitions": [
        {
          "partition_name": "Triage and Carrier Sourcing",
          "task": "Receive the RFQ email from the customer...",
          "triggering_stakeholder": "Customer",
          "halting_stakeholder": "Carriers",
          "triggering_event_description": "An RFQ email is received...",
          "expected_agent_output": "The agent sends emails containing the RFQ details..."
        }
      ]
    }
  ],
  "ambiguities": [
    "The SOP does not specify what to do if a carrier does not respond."
  ]
}
```

*   `stakeholders`: A list of all external entities (people, teams, or systems) that interact with the AI agent.
*   `scenarios`: A list of distinct, end-to-end successful workflows identified in the SOP.
    *   `scenario_name` & `description`: Human-readable identifiers for the workflow.
    *   `partitions`: A sequence of work segments that make up the scenario. Each partition represents a phase of work that starts with a trigger and ends when the agent is waiting for an external response.
        *   `partition_name` & `task`: Description of the work to be done in this phase.
        *   `triggering_stakeholder`: The stakeholder that initiates this partition.
        *   `halting_stakeholder`: The stakeholder that the agent must wait for before this partition can be considered complete.
        *   `triggering_event_description` & `expected_agent_output`: Descriptions of the start and end states of the partition.
*   `ambiguities`: A list of any unclear aspects of the SOP that the LLM identified during analysis.

### Step 3: `scenario_generator.py` - Generating Test Scenarios

This step uses an LLM to generate a rich set of test cases based on the workflow structure created in the previous step.

*   **Purpose**: To create realistic, conversational test messages for both "happy paths" (where everything goes as expected) and "unhappy paths" (where problems are introduced).
*   **Process**: For each partition, the script uses an LLM to generate a variety of test messages. These include end-to-end scenarios and partition-specific tests with pre-context from previous steps.

#### Command Example
```bash
cd labryon
source venv/bin/activate
python src/scenario_generator.py --sop-graph sop/<Worker_Name>/<Timestamp>/graph_output.json --parallel 4
```

#### JSON Fields Populated:

```json
{
  ...
  "scenarios": [
    {
      ...
      "scenario_happy_path": [
        "from: alice.johnson@example.com\nvia: email\n\nHello, I need a quote..."
      ],
      "scenario_unhappy_path": [
        "from: bob.smith@example.com\nvia: email\n\nHi, I need a quote but I forgot the details..."
      ],
      "partitions": [
        {
          ...
          "happy_path": ["from: alice.johnson@example.com..."],
          "partition_unhappy_path": ["from: bob.smith@example.com..."],
          "unhappy_paths": [
            {
              "description": "RFQ missing cargo weight",
              "scenario": ["from: charlie.brown@example.com..."]
            }
          ],
          "context": [
            "Previous step completed (Triage): The agent has received the RFQ..."
          ],
          "transitions": [ ... ]
        }
      ]
    }
  ]
}
```

*   `scenario_happy_path` / `scenario_unhappy_path`: Lists of messages for end-to-end tests that span the entire scenario from start to finish.
*   Inside `partitions`:
    *   `happy_path`: A list of messages for testing the successful execution of this specific partition.
    *   `partition_unhappy_path`: A combined scenario for this partition that includes multiple problems.
    *   `unhappy_paths`: A list of individual, isolated test cases, each focusing on a single potential failure mode.
    *   `context`: A list of descriptions of the work completed in previous partitions. This is provided to the agent during partition-level tests to give it the necessary background.
    *   `transitions`: Scenarios for testing the transition between partitions.

### Step 4: `simulation_runner.py` - Running Simulations

This is where the agent is actually executed against the generated test cases in a controlled, mocked environment.

*   **Purpose**: To simulate the agent's behavior in response to the test messages and record its internal reasoning, external communications, and any tool calls it makes.
*   **Process**: The script iterates through the generated test paths. For each message, it runs the agent and captures the output. All external dependencies, such as APIs and databases, are replaced with mock tools that provide deterministic responses defined in `mock_tool_config.yaml`.

#### Command Example
```bash
cd labryon
source venv/bin/activate
python src/simulation_runner.py --sop-graph sop/<Worker_Name>/<Timestamp>/graph_output.json --partition-happy
```

#### JSON Fields Populated:

```json
{
  ...
  "scenarios": [
    {
      ...
      "scenario_happy_path_response": [
        {
          "response": ["Hello Alice, here is your quote..."],
          "internal": ["The customer has requested a quote. I will now calculate the price..."],
          "trace": { "by_agent": { ... } }
        }
      ],
      ...
      "partitions": [
        {
          ...
          "happy_path_response": [ ... ],
          "partition_unhappy_path_response": [ ... ],
          "unhappy_path_responses": [ [ ... ] ]
        }
      ]
    }
  ]
}
```

*   The `*_response` fields (`scenario_happy_path_response`, `happy_path_response`, etc.) are populated with a list of `AgentResponse` objects, one for each turn in the conversation.
*   **`AgentResponse` Object**:
    *   `response`: A list of strings representing the agent's external communications (e.g., emails sent, Teams messages posted).
    *   `internal`: A list of strings representing the agent's internal monologue or "thought process".
    *   `trace`: An optional object containing detailed runtime evaluation data from Composo, if tracing is enabled. This includes scores for criteria like SOP adherence and tool selection, broken down by agent.

### Step 5: `evaluate_results.py` - Evaluating Results

After the simulation, this step performs a comprehensive, post-hoc evaluation of the agent's performance.

*   **Purpose**: To score the quality of the agent's reasoning and responses against a set of predefined criteria using Composo.
*   **Process**: The script iterates through the `AgentResponse` objects in the `graph_output.json`. For each turn, it sends the agent's internal reasoning and external response to Composo for evaluation.

#### Command Example
```bash
cd labryon
source venv/bin/activate
python src/evaluate_results.py --sop-graph sop/<Worker_Name>/<Timestamp>/graph_output.json --partition-happy
```

#### JSON Fields Populated:

```json
{
  ...
  "scenarios": [
    {
      ...
      "partitions": [
        {
          ...
          "happy_path_response": [
            {
              ...
              "evaluation": {
                "composo_scores": [
                  {
                    "criterion": "Reward responses whose internal reasoning...",
                    "score": 0.95,
                    "analysis": "The agent correctly identified the SOP state..."
                  }
                ],
                "latency_seconds": 15.4
              }
            }
          ],
          "evaluation_summary": {
            "by_agent": {
              "orchestrator": { "overall": 0.92, "by_criterion": { ... } }
            },
            "by_criterion": { "Tool selection": 0.95, ... },
            "overall": 0.93
          }
        }
      ],
      "evaluation_summary": { ... }
    }
  ],
  "evaluation_summary": { ... }
}
```

*   `evaluation` (inside `AgentResponse`): An object containing the turn-level evaluation scores from Composo.
*   `evaluation_summary`: An object that aggregates scores at the Partition, Scenario, and SOP Graph levels. It provides a hierarchical breakdown of performance, including overall scores and scores broken down by agent and by evaluation criterion.

### Step 6: `aggregate_agent_stats.py` - Generating AI Insights

This is the final step, which synthesizes all the collected data into human-readable reports and AI-powered recommendations.

*   **Purpose**: To provide a clear summary of the agent's performance and generate actionable insights for improvement.
*   **Process**: This script performs a statistical aggregation of all the scores in the `graph_output.json`. Optionally, it can use an LLM to analyze the low-scoring areas and generate a detailed report with root cause analysis and specific recommendations.

#### Command Example
```bash
cd labryon
source venv/bin/activate
python src/aggregate_agent_stats.py sop/<Worker_Name>/<Timestamp>/graph_output.json --insights --report
```

*   **Output**: This script does **not** modify the `graph_output.json`. Instead, it generates three new files in the same directory:
    *   `agent_stats.json`: A file containing the raw statistical aggregations.
    *   `agent_stats_report.txt`: A human-readable performance summary.
    *   `agent_stats_insights.md`: An AI-generated report with detailed analysis and recommendations for improvement.