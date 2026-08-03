import os
import json
import yaml
import argparse
import time
import sys
import multiprocessing
from collections import defaultdict
from statistics import mean
from typing import List, Dict, Any, Optional, Tuple
from dotenv import load_dotenv
from composo import Composo
from concurrent.futures import ThreadPoolExecutor, as_completed

from tenacity import retry, stop_after_attempt, wait_exponential

# --- Environment Setup ---
labryon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
dotenv_path = os.path.join(labryon_root, '.env')
load_dotenv(dotenv_path=dotenv_path, override=True)

# Import SOPGraph models
from data_models import (
    SOPGraph, Scenario, Partition, UnhappyPath, Transition,
    EvaluationScore, TurnEvaluation, PathEvaluationSummary,
    UnhappyPathEvaluationSummary, PartitionEvaluationSummary,
    ScenarioEvaluationSummary, SOPEvaluationSummary, AgentResponse
)

# --- Constants ---
MAX_RETRIES = 20
RETRY_DELAY_SECONDS = 10
RESULTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'results')
REPORTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports')
EVALUATION_CRITERIA_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'evaluation_criteria.yaml')

# --- Helper Functions ---

def load_sop_graph(sop_graph_path: str) -> SOPGraph:
    """Loads the enriched SOPGraph JSON file."""
    try:
        with open(sop_graph_path, 'r') as f:
            data = json.load(f)
        return SOPGraph.model_validate(data)
    except FileNotFoundError:
        print(f"Error: SOPGraph file not found at {sop_graph_path}")
        raise
    except Exception as e:
        print(f"Error: Could not parse SOPGraph from {sop_graph_path}: {e}")
        raise


def load_simulation_results(results_path: str) -> dict:
    """Loads the JSON artifact from the simulation run."""
    try:
        with open(results_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: Simulation results file not found at {results_path}")
        raise
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {results_path}")
        raise

def load_evaluation_criteria() -> list:
    """Loads the evaluation criteria from the YAML file."""
    try:
        with open(EVALUATION_CRITERIA_PATH, 'r') as f:
            config = yaml.safe_load(f)
            return config.get('e2e_criteria', [])
    except FileNotFoundError:
        print(f"Error: Evaluation criteria file not found at {EVALUATION_CRITERIA_PATH}")
        raise

def load_e2e_evaluation_source() -> str:
    """
    Loads the e2e_evaluation_source from the YAML file.
    Defaults to 'internal_dialog' for backward compatibility.
    """
    try:
        with open(EVALUATION_CRITERIA_PATH, 'r') as f:
            config = yaml.safe_load(f)
            return config.get('e2e_evaluation_source', 'internal_dialog')
    except Exception:
        return 'internal_dialog'


def calculate_averages(scores: list) -> float:
    """Calculates the average of a list of scores, handling empty lists."""
    return mean(scores) if scores else 0.0

# --- Helper Functions for Evaluation ---

def extract_sim_result(resp: AgentResponse, include_latency: bool = False) -> Dict[str, Any]:
    """Extract simulation result from AgentResponse."""
    result = {
        "assistant_response": "\n\n---\n\n".join(resp.response) if resp.response else None,
        "assistant_internal": resp.internal[0] if resp.internal else None,
        "trace_analysis": resp.trace
    }
    if include_latency and hasattr(resp, 'evaluation') and resp.evaluation:
        result["latency_seconds"] = resp.evaluation.latency_seconds
    return result

def append_to_conversation_history(
    history: List[Dict[str, str]],
    user_msg: str,
    assistant_internal: Optional[str]
) -> None:
    """Append user message and assistant internal to conversation history."""
    history.append({"role": "user", "content": user_msg})
    if assistant_internal:
        history.append({"role": "assistant", "content": assistant_internal})

def evaluate_message_sequence(
    messages: List[str],
    responses: List[AgentResponse],
    criteria: List[str],
    composo_client: Any,
    sop: str,
    knowledge_bases: List[str],
    partition_context: Optional[List[str]] = None,
    evaluation_source: str = "both"
) -> Tuple[List[TurnEvaluation], List[float]]:
    """
    Evaluate a sequence of messages and responses.

    Returns:
        Tuple of (turn evaluations, latencies)
    """
    turn_evals = []
    all_latencies = []
    conversation_history = []

    for turn_idx, user_msg in enumerate(messages):
        sim_result = {}
        if turn_idx < len(responses):
            resp = responses[turn_idx]
            sim_result = extract_sim_result(resp)

        turn_eval = evaluate_conversation_turn(
            user_msg, sim_result, criteria, composo_client,
            sop, knowledge_bases, partition_context=partition_context,
            evaluation_source=evaluation_source, conversation_history=conversation_history
        )
        turn_evals.append(turn_eval)

        # Update response with evaluation
        if turn_idx < len(responses):
            responses[turn_idx].evaluation = turn_eval

        if turn_eval.latency_seconds is not None:
            all_latencies.append(turn_eval.latency_seconds)

        # Build conversation history for next turn
        append_to_conversation_history(
            conversation_history,
            user_msg,
            sim_result.get("assistant_internal")
        )

    return turn_evals, all_latencies

# --- SOPGraph-based Evaluation Functions ---

@retry(stop=stop_after_attempt(20), wait=wait_exponential(multiplier=1, min=1, max=60))
def evaluate_conversation_turn(
    user_message: str,
    simulation_result: Dict[str, Any],
    criteria: List[str],
    composo_client: Any,
    sop: str,
    knowledge_bases: List[str],
    partition_context: List[str] = None,
    evaluation_source: str = "both",
    conversation_history: List[Dict[str, str]] = None
) -> TurnEvaluation:
    """
    Evaluates a single conversation turn and returns the evaluation results as TurnEvaluation.

    Args:
        evaluation_source: "both", "internal_dialog", or "user_facing"
        conversation_history: Previous conversation turns for context

    Returns:
        TurnEvaluation object with composo_scores and latency_seconds
    """
    composo_scores = []
    latency_seconds = simulation_result.get("latency_seconds")

    # Add Composo evaluation if available
    if composo_client and criteria:
        try:
            internal = simulation_result.get("assistant_internal", "")
            response = simulation_result.get("assistant_response", "")
            trace_data = simulation_result.get("trace_analysis")

            # Extract tool calls from trace (simulation_runner stores tool_calls_by_agent in trace)
            tool_calls_summary = None
            if trace_data and isinstance(trace_data, dict) and "tool_calls_by_agent" in trace_data:
                tool_calls_by_agent = trace_data.get("tool_calls_by_agent", {})
                if tool_calls_by_agent:
                    # Flatten and format tool calls for Composo
                    tool_calls = []
                    for agent_name, calls in tool_calls_by_agent.items():
                        tool_calls.extend(calls)

                    if tool_calls:
                        tool_calls_summary = "[Tool Calls]\n" + "\n".join([
                            f"- {call.get('agent_name', 'unknown')}: {call.get('tool_name', 'unknown')}({', '.join(f'{k}={v}' for k, v in call.get('parameters', {}).items())})"
                            for call in tool_calls
                        ])

            # Determine assistant content based on evaluation source
            if evaluation_source == "both":
                # Combine both internal reasoning and external response for comprehensive evaluation
                content_parts = []
                if internal:
                    content_parts.append(f"[Internal Reasoning]\n{internal}")
                if tool_calls_summary:
                    content_parts.append(tool_calls_summary)
                if response:
                    content_parts.append(f"[External Response]\n{response}")
                assistant_content = "\n\n".join(content_parts) if content_parts else ""
            elif evaluation_source == "internal_dialog":
                # Use only internal dialog
                content_parts = []
                if internal:
                    content_parts.append(internal)
                if tool_calls_summary:
                    content_parts.append(tool_calls_summary)
                assistant_content = "\n\n".join(content_parts) if content_parts else ""
            elif evaluation_source == "user_facing":
                # Use only external response
                assistant_content = response
            else:
                # Fallback to internal dialog for backward compatibility
                assistant_content = internal or response

            # Build context for the FIRST user message
            context_parts = []
            if partition_context:
                context_parts.append(f"Previous Completed Steps:\n" + "\n".join(partition_context))
            if sop:
                context_parts.append(f"Standard Operating Procedure (SOP):\n{sop}")
            if knowledge_bases:
                kb_content = "\n\n---\n\n".join([kb.get('content', '') for kb in knowledge_bases])
                context_parts.append(f"Knowledgebase:\n{kb_content}")

            # Build messages: conversation_history + current turn
            messages = []

            # Add conversation history if available
            if conversation_history:
                messages.extend(conversation_history)

            # Add current turn
            messages.append({"role": "user", "content": user_message})
            messages.append({"role": "assistant", "content": assistant_content})

            # Ensure context is added to the very first message of the conversation
            if context_parts and messages:
                # Create a copy of the first message to avoid mutating conversation_history
                first_msg = messages[0].copy()
                context_str = '\n\n'.join(context_parts)
                first_msg['content'] = f"{first_msg['content']}\n\nContext:\n{context_str}"
                messages[0] = first_msg

            eval_response = composo_client.evaluate(messages=messages, tools=[], criteria=criteria)

            # Extract score and explanation directly from the response
            # Composo returns a single EvaluationResponse with score and explanation attributes
            composo_scores = []
            if hasattr(eval_response, 'score'):
                # Since we pass multiple criteria, we get one aggregated score
                # Map it to the first criterion (or create one score per criterion if needed)
                for crit in criteria:
                    composo_scores.append(
                        EvaluationScore(
                            criterion=str(crit),
                            score=float(eval_response.score) if isinstance(eval_response.score, (int, float)) else 0.0,
                            analysis=getattr(eval_response, 'explanation', "N/A")
                        )
                    )
        except Exception as e:
            print(f"    [WARN] Composo evaluation failed: {e}")

    return TurnEvaluation(composo_scores=composo_scores, latency_seconds=latency_seconds)


def evaluate_partition(
    partition: Partition,
    partition_idx: int,
    scenario_name: str,
    sop_graph: SOPGraph,
    criteria: List[str],
    composo_client: Any,
    args: argparse.Namespace,
    evaluation_source: str = "both"
) -> Dict[str, Any]:
    """
    Evaluate a single partition and return its evaluation summary and contribution to scenario scores.
    """
    import threading
    thread_id = threading.current_thread().name
    print(f"  - [Thread {thread_id}] START Partition {partition_idx}: {partition.partition_name}")

    # Lists to collect turn evaluations for each path type
    happy_path_evals = []
    partition_unhappy_evals = []
    unhappy_paths_evals = []  # List of tuples (description, evals)
    all_latencies = []

    sop_text = sop_graph.worker_config.sop if sop_graph.worker_config else ""
    knowledge_bases = sop_graph.worker_config.knowledge_bases if sop_graph.worker_config else []

    # Evaluate happy path
    if args.partition_happy and partition.happy_path and hasattr(partition, 'happy_path_response') and partition.happy_path_response:
        print(f"    - Evaluating happy path ({len(partition.happy_path)} turns)")
        happy_path_evals, happy_latencies = evaluate_message_sequence(
            partition.happy_path, partition.happy_path_response, criteria, composo_client,
            sop_text, knowledge_bases, partition_context=partition.context,
            evaluation_source=evaluation_source
        )
        all_latencies.extend(happy_latencies)

    # Evaluate partition unhappy path
    if args.partition_unhappy and partition.partition_unhappy_path and hasattr(partition, 'partition_unhappy_path_response') and partition.partition_unhappy_path_response:
        print(f"    - Evaluating partition unhappy path ({len(partition.partition_unhappy_path)} turns)")
        partition_unhappy_evals, unhappy_latencies = evaluate_message_sequence(
            partition.partition_unhappy_path, partition.partition_unhappy_path_response, criteria, composo_client,
            sop_text, knowledge_bases, partition_context=partition.context,
            evaluation_source=evaluation_source
        )
        all_latencies.extend(unhappy_latencies)

    # Evaluate individual unhappy paths
    if args.unhappy:
        for unhappy_idx, unhappy_path in enumerate(partition.unhappy_paths):
            print(f"    - Evaluating unhappy path {unhappy_idx}: {unhappy_path.description}")
            unhappy_responses = []
            if hasattr(partition, 'unhappy_path_responses') and unhappy_idx < len(partition.unhappy_path_responses):
                unhappy_responses = partition.unhappy_path_responses[unhappy_idx]

            unhappy_turn_evals, unhappy_latencies = evaluate_message_sequence(
                unhappy_path.path, unhappy_responses, criteria, composo_client,
                sop_text, knowledge_bases, partition_context=partition.context,
                evaluation_source=evaluation_source
            )
            all_latencies.extend(unhappy_latencies)
            unhappy_paths_evals.append((unhappy_path.description, unhappy_turn_evals))

    # Calculate partition-level evaluation summary
    happy_path_summary = calculate_path_evaluation_summary(happy_path_evals) if happy_path_evals else None
    partition_unhappy_summary = calculate_path_evaluation_summary(partition_unhappy_evals) if partition_unhappy_evals else None

    unhappy_paths_summaries = []
    for description, turn_evals in unhappy_paths_evals:
        path_summary = calculate_path_evaluation_summary(turn_evals)
        unhappy_paths_summaries.append({
            "description": description,
            "by_criterion": path_summary.by_criterion,
            "overall": path_summary.overall
        })

    # Collect all AgentResponse objects from all evaluated paths
    all_agent_responses = []
    if hasattr(partition, 'happy_path_response') and partition.happy_path_response:
        all_agent_responses.extend(partition.happy_path_response)
    if hasattr(partition, 'partition_unhappy_path_response') and partition.partition_unhappy_path_response:
        all_agent_responses.extend(partition.partition_unhappy_path_response)
    if hasattr(partition, 'unhappy_path_responses') and partition.unhappy_path_responses:
        for response_group in partition.unhappy_path_responses:
            all_agent_responses.extend(response_group)

    by_agent = extract_agent_scores_from_responses(all_agent_responses)

    partition_latencies = [
        resp.evaluation.latency_seconds
        for resp in all_agent_responses
        if resp and resp.evaluation and resp.evaluation.latency_seconds is not None
    ]
    avg_latency = calculate_averages(partition_latencies) if partition_latencies else None

    partition.evaluation_summary = {
        "happy_path_avg": happy_path_summary.model_dump() if happy_path_summary else None,
        "partition_unhappy_path_avg": partition_unhappy_summary.model_dump() if partition_unhappy_summary else None,
        "unhappy_paths_avg": unhappy_paths_summaries,
        "by_agent": by_agent,
        "avg_latency_seconds": avg_latency
    }

    print(f"  - [Thread {thread_id}] DONE Partition {partition_idx}: {partition.partition_name}")

    return {
        "partition_name": partition.partition_name,
        "happy_path_summary": happy_path_summary,
        "all_latencies": all_latencies
    }


def extract_agent_scores_from_responses(agent_responses: List[AgentResponse]) -> Dict[str, Dict[str, Any]]:
    """Extract agent-level criterion scores from agent responses with trace data AND composo_scores."""
    agent_scores = defaultdict(lambda: defaultdict(list))

    for resp in agent_responses:
        if not resp:
            continue
        trace = resp.trace
        turn_eval = resp.evaluation

        # 1. Extract from trace data (runtime tracing with per-agent breakdown)
        if trace and isinstance(trace, dict) and 'by_agent' in trace and trace['by_agent'] is not None:
            for agent_name, agent_data in trace['by_agent'].items():
                if isinstance(agent_data, dict) and 'evaluations' in agent_data:
                    for eval_item in agent_data['evaluations']:
                        if isinstance(eval_item, dict):
                            criterion = eval_item.get('criterion', 'unknown')
                            score = eval_item.get('score')
                            if score is not None:
                                agent_scores[agent_name][criterion].append(score)

        # 2. Extract from composo_scores (e2e evaluation) only if NO trace data
        if turn_eval and turn_eval.composo_scores:
            use_e2e_scores = True
            if trace and isinstance(trace, dict) and 'by_agent' in trace and trace['by_agent'] is not None:
                use_e2e_scores = False  # We have trace scores, don't duplicate with e2e

            if use_e2e_scores:
                for score_item in turn_eval.composo_scores:
                    criterion = score_item.criterion
                    score = score_item.score
                    if score is not None:
                        # Attribute e2e scores to "orchestrator" when no trace data
                        agent_scores['orchestrator'][criterion].append(score)

    # Calculate averages for each agent
    by_agent = {}
    for agent_name, criterion_scores in agent_scores.items():
        by_criterion = {crit: calculate_averages(scores) for crit, scores in criterion_scores.items()}
        overall = calculate_averages(list(by_criterion.values())) if by_criterion else 0.0
        by_agent[agent_name] = {
            "by_criterion": by_criterion,
            "overall": overall
        }

    return by_agent


def calculate_path_evaluation_summary(turn_evaluations: List[TurnEvaluation]) -> PathEvaluationSummary:
    """Calculate average scores by criterion and overall for a path."""
    if not turn_evaluations:
        return PathEvaluationSummary()

    criterion_scores = defaultdict(list)
    for turn_eval in turn_evaluations:
        for score in turn_eval.composo_scores:
            criterion_scores[score.criterion].append(score.score)

    by_criterion = {crit: calculate_averages(scores) for crit, scores in criterion_scores.items()}
    overall = calculate_averages(list(by_criterion.values())) if by_criterion else 0.0

    return PathEvaluationSummary(by_criterion=by_criterion, overall=overall)


def evaluate_scenario(
    scenario: Scenario,
    sop_graph: SOPGraph,
    criteria: List[str],
    composo_client: Any,
    partition_workers: int,
    args: argparse.Namespace
) -> Dict[str, Any]:
    """
    Evaluate a single scenario with parallel partition evaluation.
    Returns aggregated scenario-level data.
    """
    print(f"\n--- Processing Scenario: {scenario.scenario_name} ---")

    scenario_partition_scores = {}
    all_criterion_scores = defaultdict(list)
    all_latencies = []

    with ThreadPoolExecutor(max_workers=partition_workers) as executor:
        futures = {
            executor.submit(evaluate_partition, p, p_idx, scenario.scenario_name, sop_graph, criteria, composo_client, args): (p_idx, p)
            for p_idx, p in enumerate(scenario.partitions)
        }

        for future in as_completed(futures):
            p_idx, p = futures[future]
            try:
                result = future.result()
                if result["happy_path_summary"]:
                    scenario_partition_scores[result["partition_name"]] = result["happy_path_summary"]
                    for crit, score in result["happy_path_summary"].by_criterion.items():
                        all_criterion_scores[crit].append(score)
                all_latencies.extend(result["all_latencies"])
            except Exception as exc:
                print(f"   [ERROR] Partition {p_idx} '{p.partition_name}' failed: {exc}", file=sys.stderr)

    # Calculate scenario-level evaluation summary
    scenario_by_criterion = {
        crit: calculate_averages(scores)
        for crit, scores in all_criterion_scores.items()
    }
    scenario_overall = calculate_averages(list(scenario_by_criterion.values()))
    scenario_latency = calculate_averages([p.evaluation_summary.get("avg_latency_seconds") for p in scenario.partitions if p.evaluation_summary and p.evaluation_summary.get("avg_latency_seconds") is not None])

    # Aggregate agent-level scores across all partitions in this scenario
    agent_partition_scores = defaultdict(lambda: defaultdict(list))
    for partition in scenario.partitions:
        if partition.evaluation_summary and 'by_agent' in partition.evaluation_summary:
            for agent_name, agent_data in partition.evaluation_summary['by_agent'].items():
                if isinstance(agent_data, dict) and 'by_criterion' in agent_data:
                    for criterion, score in agent_data['by_criterion'].items():
                        agent_partition_scores[agent_name][criterion].append(score)

    # Calculate averages for each agent at scenario level
    scenario_by_agent = {}
    for agent_name, criterion_scores in agent_partition_scores.items():
        by_criterion = {crit: calculate_averages(scores) for crit, scores in criterion_scores.items()}
        overall = calculate_averages(list(by_criterion.values())) if by_criterion else 0.0

        # Also aggregate by partition for this agent
        by_partition = {}
        for partition in scenario.partitions:
            if partition.evaluation_summary and 'by_agent' in partition.evaluation_summary:
                if agent_name in partition.evaluation_summary['by_agent']:
                    by_partition[partition.partition_name] = partition.evaluation_summary['by_agent'][agent_name]

        scenario_by_agent[agent_name] = {
            "by_criterion": by_criterion,
            "overall": overall,
            "by_partition": by_partition
        }

    scenario.evaluation_summary = {
        "by_partition": {name: summary.model_dump() for name, summary in scenario_partition_scores.items()},
        "by_criterion": scenario_by_criterion,
        "overall": scenario_overall,
        "by_agent": scenario_by_agent,
        "avg_latency_seconds": scenario_latency if scenario_latency > 0 else None
    }

    return {
        "scenario_name": scenario.scenario_name,
        "overall": scenario_overall,
        "criterion_scores": dict(all_criterion_scores),
        "latencies": all_latencies
    }

def evaluate_scenario_level_path(scenario: Scenario, path_type: str, criteria: List[str], composo_client: Any, sop_graph: SOPGraph, evaluation_source: str = "both"):
    """Evaluates the scenario-level scenario_happy_path or scenario_unhappy_path."""
    print(f"\n--- Evaluating Scenario-level Path: {path_type} for {scenario.scenario_name} ---")

    path = getattr(scenario, f"{path_type}_path", [])
    responses = getattr(scenario, f"{path_type}_path_response", [])

    if not path or not responses:
        print(f"    - No data found for {path_type}. Skipping.")
        return None, []

    turn_evals = []
    all_latencies = []
    conversation_history = []

    for turn_idx, user_msg in enumerate(path):
        sim_result = {}
        if turn_idx < len(responses):
            resp = responses[turn_idx]
            sim_result = {
                "assistant_response": "\n\n---\n\n".join(resp.response) if resp.response else None,
                "assistant_internal": resp.internal[0] if resp.internal else None,
                "trace_analysis": resp.trace,
                "latency_seconds": resp.evaluation.latency_seconds if resp.evaluation else None
            }

        turn_eval = evaluate_conversation_turn(
            user_msg, sim_result, criteria, composo_client,
            sop_graph.worker_config.sop if sop_graph.worker_config else "",
            sop_graph.worker_config.knowledge_bases if sop_graph.worker_config else [],
            partition_context=None, evaluation_source=evaluation_source, conversation_history=conversation_history
        )
        turn_evals.append(turn_eval)

        if turn_idx < len(responses):
            responses[turn_idx].evaluation = turn_eval

        if turn_eval.latency_seconds is not None:
            all_latencies.append(turn_eval.latency_seconds)

        # Build conversation history for next turn (user + internal dialog only)
        conversation_history.append({"role": "user", "content": user_msg})
        if sim_result.get("assistant_internal"):
            conversation_history.append({"role": "assistant", "content": sim_result.get("assistant_internal")})

    summary = calculate_path_evaluation_summary(turn_evals)
    print(f"    - Evaluation complete for {path_type}. Overall score: {summary.overall:.2f}")
    return summary, all_latencies


def main():
    parser = argparse.ArgumentParser(description="Evaluate simulation results in an SOP Graph.")
    parser.add_argument('--sop-graph', type=str, required=True, help='Path to the SOP Graph JSON file.')
    parser.add_argument('--parallel', type=int, nargs='?', const=multiprocessing.cpu_count(), default=1, help='Number of parallel workers.')
    
    # New granular flags
    parser.add_argument('--all', action='store_true', help='Evaluate all available simulation paths.')
    parser.add_argument('--scenario-happy', action='store_true', help='Evaluate the end-to-end happy path for the whole SOP.')
    parser.add_argument('--scenario-unhappy', action='store_true', help='Evaluate the end-to-end unhappy path for the whole SOP.')
    parser.add_argument('--partition-happy', action='store_true', help='Evaluate the happy path for each partition.')
    parser.add_argument('--partition-unhappy', action='store_true', help='Evaluate the combined unhappy path for each partition.')
    parser.add_argument('--happy', action='store_true', help='Alias for --partition-happy.')
    parser.add_argument('--unhappy', action='store_true', help='Evaluate all individual unhappy paths for each partition.')

    args = parser.parse_args()

    if args.all:
        args.scenario_happy = True
        args.scenario_unhappy = True
        args.partition_happy = True
        args.partition_unhappy = True
        args.unhappy = True

    if not any([args.scenario_happy, args.scenario_unhappy, args.partition_happy, args.partition_unhappy, args.happy, args.unhappy]):
        print("No evaluation flags provided. Exiting.")
        sys.exit(0)

    if args.happy:
        args.partition_happy = True

    sop_graph = load_sop_graph(args.sop_graph)
    criteria = load_evaluation_criteria()
    evaluation_source = load_e2e_evaluation_source()
    composo_client = Composo(api_key=os.getenv("COMPOSO_API_KEY")) if os.getenv("COMPOSO_API_KEY") else None

    print(f"\n{'='*80}\n--- Starting Evaluation ---\n{'='*80}")
    print(f"[INFO] Evaluation source: {evaluation_source}")

    # --- Scenario-level Path Evaluation ---
    all_scenario_happy_summaries = []
    all_scenario_unhappy_summaries = []
    scenario_level_latencies = []

    if args.scenario_happy or args.scenario_unhappy:
        for scenario in sop_graph.scenarios:
            if args.scenario_happy:
                summary, latencies = evaluate_scenario_level_path(scenario, "scenario_happy", criteria, composo_client, sop_graph, evaluation_source)
                if summary:
                    all_scenario_happy_summaries.append(summary)
                    scenario_level_latencies.extend(latencies)

            if args.scenario_unhappy:
                summary, latencies = evaluate_scenario_level_path(scenario, "scenario_unhappy", criteria, composo_client, sop_graph, evaluation_source)
                if summary:
                    all_scenario_unhappy_summaries.append(summary)
                    scenario_level_latencies.extend(latencies)

    # --- Partition-level Evaluation ---
    all_scenario_scores = {}
    all_criterion_scores = defaultdict(list)
    all_latencies = scenario_level_latencies

    if args.partition_happy or args.partition_unhappy or args.unhappy:
        num_scenarios = len(sop_graph.scenarios)
        max_partitions = max(len(s.partitions) for s in sop_graph.scenarios) if sop_graph.scenarios else 1
        scenario_workers = 1 if num_scenarios == 1 else min(num_scenarios, max(1, int(args.parallel ** 0.5)))
        partition_workers = max(1, args.parallel // scenario_workers)

        print(f"[INFO] Parallelism: {scenario_workers} scenario workers × {partition_workers} partition workers")

        with ThreadPoolExecutor(max_workers=scenario_workers) as executor:
            futures = {executor.submit(evaluate_scenario, s, sop_graph, criteria, composo_client, partition_workers, args): s for s in sop_graph.scenarios}
            for future in as_completed(futures):
                s = futures[future]
                try:
                    result = future.result()
                    all_scenario_scores[result["scenario_name"]] = result["overall"]
                    for crit, scores in result["criterion_scores"].items():
                        all_criterion_scores[crit].extend(scores)
                    all_latencies.extend(result["latencies"])
                except Exception as exc:
                    print(f"   [ERROR] Scenario '{s.scenario_name}' failed: {exc}", file=sys.stderr)

    # --- Final Aggregation ---
    overall_score = calculate_averages(list(all_scenario_scores.values()))
    if not all_scenario_scores and (all_scenario_happy_summaries or all_scenario_unhappy_summaries):
        scenario_scores = []
        for summary in all_scenario_happy_summaries:
            if summary and summary.overall is not None:
                scenario_scores.append(summary.overall)
        for summary in all_scenario_unhappy_summaries:
            if summary and summary.overall is not None:
                scenario_scores.append(summary.overall)
        overall_score = calculate_averages(scenario_scores)

    by_criterion = {crit: calculate_averages(scores) for crit, scores in all_criterion_scores.items()}

    performance_metrics = {}
    if all_latencies:
        performance_metrics = {
            "avg_latency_seconds": calculate_averages(all_latencies),
            "min_latency_seconds": min(all_latencies),
            "max_latency_seconds": max(all_latencies)
        }

    # Aggregate agent-level scores across all scenarios
    agent_scenario_scores = defaultdict(lambda: defaultdict(list))
    for scenario in sop_graph.scenarios:
        if scenario.evaluation_summary and 'by_agent' in scenario.evaluation_summary:
            for agent_name, agent_data in scenario.evaluation_summary['by_agent'].items():
                if isinstance(agent_data, dict) and 'by_criterion' in agent_data:
                    for criterion, score in agent_data['by_criterion'].items():
                        agent_scenario_scores[agent_name][criterion].append(score)

    # Calculate SOP-level averages for each agent
    sop_by_agent = {}
    for agent_name, criterion_scores in agent_scenario_scores.items():
        by_criterion_agent = {crit: calculate_averages(scores) for crit, scores in criterion_scores.items()}
        overall_agent = calculate_averages(list(by_criterion_agent.values())) if by_criterion_agent else 0.0

        # Also aggregate by scenario for this agent
        by_scenario = {}
        for scenario in sop_graph.scenarios:
            if scenario.evaluation_summary and 'by_agent' in scenario.evaluation_summary:
                if agent_name in scenario.evaluation_summary['by_agent']:
                    by_scenario[scenario.scenario_name] = scenario.evaluation_summary['by_agent'][agent_name]

        sop_by_agent[agent_name] = {
            "by_criterion": by_criterion_agent,
            "overall": overall_agent,
            "by_scenario": by_scenario
        }

    sop_graph.evaluation_summary = {
        "overall_score": overall_score,
        "by_criterion": by_criterion,
        "by_scenario": all_scenario_scores,
        "by_agent": sop_by_agent,
        "performance_metrics": performance_metrics,
    }

    print(f"\n[SUCCESS] Evaluation complete. Overall score: {overall_score:.2f}")

    try:
        with open(args.sop_graph, 'w', encoding='utf-8') as f:
            f.write(sop_graph.model_dump_json(indent=2, by_alias=True))
        print(f"\n[SUCCESS] Enriched SOPGraph with evaluations saved to: {args.sop_graph}")
    except Exception as e:
        print(f"\n[ERROR] Failed to save enriched SOPGraph: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()