#!/usr/bin/env python3
"""
Aggregate Agent Statistics Script

Reads the graph_output.json file and generates summary statistics showing
agent performance across all dimensions:
- By agent → criterion → scenario → partition

This is a non-LLM script that purely performs statistical aggregation.
"""

import json
import argparse
import os
import sys
from collections import defaultdict
from typing import Dict, Any, List, Optional
from statistics import mean

# --- Configuration ---
from dotenv import load_dotenv
labryon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); dotenv_path = os.path.join(labryon_root, ".env"); load_dotenv(dotenv_path=dotenv_path, override=True)


def calculate_avg(scores: List[float]) -> float:
    """Calculate average, handling empty lists."""
    return mean(scores) if scores else 0.0


def extract_agent_stats_from_graph(graph_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract comprehensive agent statistics from the evaluated graph.

    Returns structure:
    {
        "agent_name": {
            "overall": float,
            "by_criterion": {
                "criterion_name": {
                    "overall": float,
                    "by_scenario": {
                        "scenario_name": {
                            "overall": float,
                            "by_partition": {
                                "partition_name": float
                            }
                        }
                    }
                }
            }
        }
    }
    """
    agent_stats = defaultdict(lambda: {
        "criterion_scores": defaultdict(lambda: {
            "scenario_scores": defaultdict(lambda: {
                "partition_scores": []
            }),
            "all_scores": []
        }),
        "all_scores": []
    })

    # Process each scenario
    for scenario in graph_data.get("scenarios", []):
        scenario_name = scenario.get("scenario_name", "unknown")

        # Extract from scenario-level responses if available
        for response_field in ["scenario_happy_path_response", "scenario_unhappy_path_response"]:
            responses = scenario.get(response_field, [])
            for response in responses:
                if not isinstance(response, dict):
                    continue

                # Extract from trace.by_agent (runtime tracing scores)
                if "trace" in response:
                    trace = response["trace"]
                    if isinstance(trace, dict) and "by_agent" in trace and trace["by_agent"] is not None:
                        for agent_name, agent_data in trace["by_agent"].items():
                            if isinstance(agent_data, dict) and "evaluations" in agent_data:
                                for eval_item in agent_data["evaluations"]:
                                    if isinstance(eval_item, dict):
                                        criterion = eval_item.get("criterion", "unknown")
                                        score = eval_item.get("score")
                                        if score is not None:
                                            agent_stats[agent_name]["all_scores"].append(score)
                                            agent_stats[agent_name]["criterion_scores"][criterion]["all_scores"].append(score)
                                            agent_stats[agent_name]["criterion_scores"][criterion]["scenario_scores"][scenario_name]["partition_scores"].append(score)

                # Extract from evaluation.composo_scores (e2e evaluation scores)
                if "evaluation" in response:
                    evaluation = response["evaluation"]
                    if isinstance(evaluation, dict) and "composo_scores" in evaluation:
                        composo_scores = evaluation.get("composo_scores", [])
                        if composo_scores:
                            # Attribute to orchestrator when no trace data
                            for score_item in composo_scores:
                                if isinstance(score_item, dict):
                                    criterion = score_item.get("criterion", "unknown")
                                    score = score_item.get("score")
                                    if score is not None:
                                        agent_stats["orchestrator"]["all_scores"].append(score)
                                        agent_stats["orchestrator"]["criterion_scores"][criterion]["all_scores"].append(score)
                                        agent_stats["orchestrator"]["criterion_scores"][criterion]["scenario_scores"][scenario_name]["partition_scores"].append(score)

        # Process each partition
        for partition in scenario.get("partitions", []):
            partition_name = partition.get("partition_name", "unknown")
            evaluation_summary = partition.get("evaluation_summary") or {}

            # Extract agent-level scores from partition evaluation_summary
            by_agent = evaluation_summary.get("by_agent", {}) if isinstance(evaluation_summary, dict) else {}
            for agent_name, agent_data in by_agent.items():
                if not isinstance(agent_data, dict):
                    continue

                agent_overall = agent_data.get("overall")
                if agent_overall is not None:
                    agent_stats[agent_name]["all_scores"].append(agent_overall)

                # Process by criterion
                by_criterion = agent_data.get("by_criterion", {})
                for criterion, score in by_criterion.items():
                    if score is not None:
                        agent_stats[agent_name]["criterion_scores"][criterion]["all_scores"].append(score)
                        agent_stats[agent_name]["criterion_scores"][criterion]["scenario_scores"][scenario_name]["partition_scores"].append(score)

            # Also extract from partition-level response traces/evaluations if evaluation_summary is empty
            if not by_agent:
                for response_field in ["happy_path_response", "partition_unhappy_path_response", "unhappy_path_responses"]:
                    responses = partition.get(response_field, [])
                    if not isinstance(responses, list):
                        continue
                    for response in responses:
                        if not isinstance(response, dict):
                            continue

                        # Extract from trace.by_agent (runtime tracing scores)
                        if "trace" in response:
                            trace = response["trace"]
                            if isinstance(trace, dict) and "by_agent" in trace and trace["by_agent"] is not None:
                                for agent_name, agent_data in trace["by_agent"].items():
                                    if isinstance(agent_data, dict) and "evaluations" in agent_data:
                                        for eval_item in agent_data["evaluations"]:
                                            if isinstance(eval_item, dict):
                                                criterion = eval_item.get("criterion", "unknown")
                                                score = eval_item.get("score")
                                                if score is not None:
                                                    agent_stats[agent_name]["all_scores"].append(score)
                                                    agent_stats[agent_name]["criterion_scores"][criterion]["all_scores"].append(score)
                                                    agent_stats[agent_name]["criterion_scores"][criterion]["scenario_scores"][scenario_name]["partition_scores"].append(score)

                        # Extract from evaluation.composo_scores (e2e evaluation scores)
                        if "evaluation" in response:
                            evaluation = response["evaluation"]
                            if isinstance(evaluation, dict) and "composo_scores" in evaluation:
                                composo_scores = evaluation.get("composo_scores", [])
                                if composo_scores:
                                    # Attribute to orchestrator when no trace data
                                    for score_item in composo_scores:
                                        if isinstance(score_item, dict):
                                            criterion = score_item.get("criterion", "unknown")
                                            score = score_item.get("score")
                                            if score is not None:
                                                agent_stats["orchestrator"]["all_scores"].append(score)
                                                agent_stats["orchestrator"]["criterion_scores"][criterion]["all_scores"].append(score)
                                                agent_stats["orchestrator"]["criterion_scores"][criterion]["scenario_scores"][scenario_name]["partition_scores"].append(score)

    # Calculate aggregations
    result = {}
    for agent_name, agent_data in agent_stats.items():
        agent_result = {
            "overall": calculate_avg(agent_data["all_scores"]),
            "by_criterion": {}
        }

        for criterion, crit_data in agent_data["criterion_scores"].items():
            criterion_result = {
                "overall": calculate_avg(crit_data["all_scores"]),
                "by_scenario": {}
            }

            for scenario_name, scenario_data in crit_data["scenario_scores"].items():
                scenario_result = {
                    "overall": calculate_avg(scenario_data["partition_scores"]),
                    "by_partition": {}
                }

                # For partition-level, we need to recalculate from the graph
                # Find partitions for this scenario
                for scenario in graph_data.get("scenarios", []):
                    if scenario.get("scenario_name") == scenario_name:
                        for partition in scenario.get("partitions", []):
                            partition_name = partition.get("partition_name", "unknown")
                            eval_summary = partition.get("evaluation_summary") or {}
                            by_agent = eval_summary.get("by_agent", {}) if isinstance(eval_summary, dict) else {}

                            if agent_name in by_agent:
                                agent_partition_data = by_agent[agent_name]
                                by_criterion_partition = agent_partition_data.get("by_criterion", {})
                                if criterion in by_criterion_partition:
                                    scenario_result["by_partition"][partition_name] = by_criterion_partition[criterion]

                criterion_result["by_scenario"][scenario_name] = scenario_result

            agent_result["by_criterion"][criterion] = criterion_result

        result[agent_name] = agent_result

    return result


def extract_criterion_stats_from_graph(graph_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract criterion statistics showing agent performance by criterion.

    Returns structure:
    {
        "criterion_name": {
            "overall": float,
            "by_agent": {
                "agent_name": {
                    "overall": float,
                    "by_scenario": {
                        "scenario_name": {
                            "overall": float,
                            "by_partition": {
                                "partition_name": float
                            }
                        }
                    }
                }
            }
        }
    }
    """
    criterion_stats = defaultdict(lambda: {
        "agent_scores": defaultdict(lambda: {
            "scenario_scores": defaultdict(lambda: {
                "partition_scores": []
            }),
            "all_scores": []
        }),
        "all_scores": []
    })

    # Process each scenario
    for scenario in graph_data.get("scenarios", []):
        scenario_name = scenario.get("scenario_name", "unknown")

        # Extract from scenario-level responses if available
        for response_field in ["scenario_happy_path_response", "scenario_unhappy_path_response"]:
            responses = scenario.get(response_field, [])
            for response in responses:
                if isinstance(response, dict) and "trace" in response:
                    trace = response["trace"]
                    if isinstance(trace, dict) and "by_agent" in trace and trace["by_agent"] is not None:
                        for agent_name, agent_data in trace["by_agent"].items():
                            if isinstance(agent_data, dict) and "evaluations" in agent_data:
                                for eval_item in agent_data["evaluations"]:
                                    if isinstance(eval_item, dict):
                                        criterion = eval_item.get("criterion", "unknown")
                                        score = eval_item.get("score")
                                        if score is not None:
                                            criterion_stats[criterion]["all_scores"].append(score)
                                            criterion_stats[criterion]["agent_scores"][agent_name]["all_scores"].append(score)
                                            criterion_stats[criterion]["agent_scores"][agent_name]["scenario_scores"][scenario_name]["partition_scores"].append(score)

        # Process each partition
        for partition in scenario.get("partitions", []):
            partition_name = partition.get("partition_name", "unknown")
            evaluation_summary = partition.get("evaluation_summary") or {}

            # Extract agent-level scores from partition
            by_agent = evaluation_summary.get("by_agent", {}) if isinstance(evaluation_summary, dict) else {}
            for agent_name, agent_data in by_agent.items():
                if not isinstance(agent_data, dict):
                    continue

                # Process by criterion
                by_criterion = agent_data.get("by_criterion", {})
                for criterion, score in by_criterion.items():
                    if score is not None:
                        criterion_stats[criterion]["all_scores"].append(score)
                        criterion_stats[criterion]["agent_scores"][agent_name]["all_scores"].append(score)
                        criterion_stats[criterion]["agent_scores"][agent_name]["scenario_scores"][scenario_name]["partition_scores"].append(score)

            # Also extract from partition-level response traces if evaluation_summary is empty
            if not by_agent:
                for response_field in ["happy_path_response", "partition_unhappy_path_response", "unhappy_path_responses"]:
                    responses = partition.get(response_field, [])
                    if not isinstance(responses, list):
                        continue
                    for response in responses:
                        if isinstance(response, dict) and "trace" in response:
                            trace = response["trace"]
                            if isinstance(trace, dict) and "by_agent" in trace and trace["by_agent"] is not None:
                                for agent_name, agent_data in trace["by_agent"].items():
                                    if isinstance(agent_data, dict) and "evaluations" in agent_data:
                                        for eval_item in agent_data["evaluations"]:
                                            if isinstance(eval_item, dict):
                                                criterion = eval_item.get("criterion", "unknown")
                                                score = eval_item.get("score")
                                                if score is not None:
                                                    criterion_stats[criterion]["all_scores"].append(score)
                                                    criterion_stats[criterion]["agent_scores"][agent_name]["all_scores"].append(score)
                                                    criterion_stats[criterion]["agent_scores"][agent_name]["scenario_scores"][scenario_name]["partition_scores"].append(score)

    # Calculate aggregations
    result = {}
    for criterion, crit_data in criterion_stats.items():
        criterion_result = {
            "overall": calculate_avg(crit_data["all_scores"]),
            "by_agent": {}
        }

        for agent_name, agent_data in crit_data["agent_scores"].items():
            agent_result = {
                "overall": calculate_avg(agent_data["all_scores"]),
                "by_scenario": {}
            }

            for scenario_name, scenario_data in agent_data["scenario_scores"].items():
                scenario_result = {
                    "overall": calculate_avg(scenario_data["partition_scores"]),
                    "by_partition": {}
                }

                # Find partitions for this scenario
                for scenario in graph_data.get("scenarios", []):
                    if scenario.get("scenario_name") == scenario_name:
                        for partition in scenario.get("partitions", []):
                            partition_name = partition.get("partition_name", "unknown")
                            eval_summary = partition.get("evaluation_summary") or {}
                            by_agent_partition = eval_summary.get("by_agent", {}) if isinstance(eval_summary, dict) else {}

                            if agent_name in by_agent_partition:
                                agent_partition_data = by_agent_partition[agent_name]
                                by_criterion_partition = agent_partition_data.get("by_criterion", {})
                                if criterion in by_criterion_partition:
                                    scenario_result["by_partition"][partition_name] = by_criterion_partition[criterion]

                agent_result["by_scenario"][scenario_name] = scenario_result

            criterion_result["by_agent"][agent_name] = agent_result

        result[criterion] = criterion_result

    return result


def extract_scenario_stats_from_graph(graph_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract scenario statistics showing agent performance by scenario.

    Returns structure:
    {
        "scenario_name": {
            "overall": float,
            "by_agent": {
                "agent_name": {
                    "overall": float,
                    "by_criterion": {
                        "criterion_name": {
                            "overall": float,
                            "by_partition": {
                                "partition_name": float
                            }
                        }
                    }
                }
            }
        }
    }
    """
    result = {}

    for scenario in graph_data.get("scenarios", []):
        scenario_name = scenario.get("scenario_name", "unknown")
        eval_summary = scenario.get("evaluation_summary") or {}

        scenario_result = {
            "overall": eval_summary.get("overall", 0.0) if isinstance(eval_summary, dict) else 0.0,
            "by_criterion": eval_summary.get("by_criterion", {}) if isinstance(eval_summary, dict) else {},
            "by_agent": {}
        }

        # Extract agent stats from scenario evaluation_summary
        by_agent = eval_summary.get("by_agent", {}) if isinstance(eval_summary, dict) else {}
        for agent_name, agent_data in by_agent.items():
            if not isinstance(agent_data, dict):
                continue

            agent_result = {
                "overall": agent_data.get("overall", 0.0),
                "by_criterion": agent_data.get("by_criterion", {}),
                "by_partition": agent_data.get("by_partition", {})
            }

            scenario_result["by_agent"][agent_name] = agent_result

        result[scenario_name] = scenario_result

    return result


def extract_low_score_examples(graph_data: Dict[str, Any], threshold: float = 0.7) -> List[Dict[str, Any]]:
    """
    Extract examples of low-scoring evaluations with their explanations.

    Returns list of examples with context about what went wrong.
    """
    examples = []

    for scenario in graph_data.get("scenarios", []):
        scenario_name = scenario.get("scenario_name", "unknown")

        # Check scenario-level responses
        for response_field in ["scenario_happy_path_response", "scenario_unhappy_path_response"]:
            responses = scenario.get(response_field, [])
            for turn_idx, response in enumerate(responses):
                if isinstance(response, dict) and "trace" in response:
                    trace = response["trace"]
                    if isinstance(trace, dict) and "by_agent" in trace and trace["by_agent"] is not None:
                        for agent_name, agent_data in trace["by_agent"].items():
                            if isinstance(agent_data, dict) and "evaluations" in agent_data:
                                for eval_item in agent_data["evaluations"]:
                                    if isinstance(eval_item, dict):
                                        score = eval_item.get("score")
                                        if score is not None and score < threshold:
                                            examples.append({
                                                "agent": agent_name,
                                                "scenario": scenario_name,
                                                "partition": "scenario_level",
                                                "turn": turn_idx,
                                                "criterion": eval_item.get("criterion", "unknown"),
                                                "score": score,
                                                "explanation": eval_item.get("explanation", "No explanation"),
                                                "response_type": response_field
                                            })

        # Check partition-level responses
        for partition in scenario.get("partitions", []):
            partition_name = partition.get("partition_name", "unknown")

            for response_field in ["happy_path_response", "partition_unhappy_path_response", "unhappy_path_responses"]:
                responses = partition.get(response_field, [])
                if not isinstance(responses, list):
                    continue

                for turn_idx, response in enumerate(responses):
                    if isinstance(response, dict) and "trace" in response:
                        trace = response["trace"]
                        if isinstance(trace, dict) and "by_agent" in trace and trace["by_agent"] is not None:
                            for agent_name, agent_data in trace["by_agent"].items():
                                if isinstance(agent_data, dict) and "evaluations" in agent_data:
                                    for eval_item in agent_data["evaluations"]:
                                        if isinstance(eval_item, dict):
                                                                                    score = eval_item.get("score")
                                                                                    if score is not None and score < threshold:
                                                                                        examples.append({
                                                                                            "agent": agent_name,
                                                                                            "scenario": scenario_name,
                                                                                            "partition": partition_name,
                                                                                            "turn": turn_idx,
                                                                                            "criterion": eval_item.get("criterion", "unknown"),
                                                                                            "score": score,
                                                                                            "explanation": eval_item.get("explanation", "No explanation"),
                                                                                            "response_type": response_field
                                                                                        })
    return examples


def generate_llm_insights(
    stats: Dict[str, Any],
    graph_data: Dict[str, Any],
    threshold: float = 0.7
) -> str:
    """
    Use Gemini 2.5 to analyze low scores and provide improvement recommendations.

    Args:
        stats: Aggregated statistics
        graph_data: Original graph data with trace explanations
        threshold: Score threshold for identifying problems (default 0.7)

    Returns:
        LLM-generated insights report
    """
    try:
        from agno.agent import Agent
        from agno.models.google import Gemini
    except ImportError:
        return "Error: agno package not installed. Run: pip install agno"

    # Configure Gemini via AGNO
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return "Error: GOOGLE_API_KEY environment variable not set"

    model = Gemini(id="gemini-2.5-pro", api_key=api_key)

    # Collect low-scoring examples
    low_score_examples = extract_low_score_examples(graph_data, threshold)

    # Extract SOP and system context
    worker_config = graph_data.get("worker_config", {})
    sop_text = worker_config.get("sop", "No SOP available")
    system_prompt = graph_data.get("system_prompt", "No system prompt available")

    # Extract agent configurations
    agents_info = []
    for agent in worker_config.get("agents", []):
        agents_info.append({
            "name": agent.get("name"),
            "agent_id": agent.get("agent_id"),
            "description": agent.get("description"),
            "prompt": agent.get("prompt", "")[:500],  # First 500 chars of agent prompt
            "tools": list(agent.get("tool_bindings", {}).keys())
        })

    # Extract knowledge bases summaries
    kb_info = []
    for kb in worker_config.get("knowledge_bases", [])[:5]:  # Limit to first 5 KBs
        content = kb.get("content", "")
        kb_info.append({
            "document": kb.get("document_name"),
            "content_preview": content[:300] + "..." if len(content) > 300 else content
        })

    # Build context for LLM
    context = {
        "overall_stats": stats["by_agent"],
        "criterion_stats": stats["by_criterion"],
        "scenario_stats": stats["by_scenario"],
        "low_score_examples": low_score_examples[:20],  # Top 20 worst
        "threshold": threshold,
        "sop": sop_text,
        "agents": agents_info,
        "knowledge_bases": kb_info
    }

    # Build prompt
    prompt = f"""You are an AI system optimization expert analyzing test results for a multi-agent workflow automation system.

# System Context

## Standard Operating Procedure (SOP)
The agents are following this SOP:
```
{context["sop"]}
```

## Agent Configuration
The system uses the following agents:
{json.dumps(context["agents"], indent=2)}

## Available Knowledge Bases
{json.dumps(context["knowledge_bases"], indent=2)}

# Performance Data

## Overall Agent Performance
{json.dumps(context["overall_stats"], indent=2)}

## Performance by Evaluation Criterion
{json.dumps(context["criterion_stats"], indent=2)}

## Low Score Examples (< {threshold})
The following are specific instances where agents scored poorly, including the evaluator's explanation:
{json.dumps(context["low_score_examples"], indent=2)}

# Task
Analyze the performance data above and provide actionable recommendations to improve the system. Focus on:
1. Identifying patterns in low-scoring areas
2. Understanding root causes from evaluation explanations (reference the SOP and agent configs)
3. Suggesting specific, prioritized improvements

# Your Analysis Should Include

1. **Performance Summary** (2-3 sentences)
   - Overall system performance level
   - Which agents/criteria need the most attention

2. **Key Issues Identified** (3-5 bullet points)
   - What are the most common failure patterns?
   - Which scenarios are most problematic?
   - What do the evaluation explanations reveal?

3. **Root Cause Analysis** (2-4 categories)
   - Group related issues together
   - Explain why these issues are occurring
   - Reference specific examples from the data

4. **Prioritized Recommendations** (Top 5-7, ordered by impact)
   For each recommendation:
   - **Category**: [SOP/Prompt/Tool/Architecture/Training Data]
   - **Priority**: [High/Medium/Low]
   - **Issue**: What's broken
   - **Action**: Specific change to make
   - **Expected Impact**: How much improvement to expect

5. **Quick Wins** (2-3 items)
   - Low-effort, high-impact changes that can be implemented immediately

# Output Format
Provide your analysis in clear markdown format with headers and bullet points. Be specific and actionable.
"""

    try:
        print("Generating LLM insights with Gemini 2.5 Pro...")

        # Create an Agent to handle the LLM call with markdown output
        agent = Agent(
            name="insights_analyzer",
            model=model,
            markdown=True,
            description="AI system optimization expert analyzing test results"
        )

        response = agent.run(prompt)

        # Extract content from RunOutput
        # response is a RunOutput object with a content attribute
        if hasattr(response, 'content') and response.content:
            return str(response.content)
        elif hasattr(response, 'get_content_as_string'):
            return response.get_content_as_string()
        elif isinstance(response, str):
            return response
        else:
            return str(response)
    except Exception as e:
        import traceback
        error_msg = f"Error generating insights: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
        print(error_msg, file=sys.stderr)
        return error_msg


def generate_summary_report(stats: Dict[str, Any]) -> str:
    """Generate a human-readable summary report."""
    lines = []
    lines.append("=" * 80)
    lines.append("AGENT PERFORMANCE SUMMARY")
    lines.append("=" * 80)
    lines.append("")

    # Overall agent performance
    agent_by_overall = sorted(
        [(name, data["overall"]) for name, data in stats["by_agent"].items()],
        key=lambda x: x[1],
        reverse=True
    )

    lines.append("Overall Agent Performance:")
    for agent_name, overall_score in agent_by_overall:
        lines.append(f"  {agent_name}: {overall_score:.3f}")
    lines.append("")

    # Per criterion breakdown
    lines.append("By Criterion:")
    for criterion, crit_data in stats["by_criterion"].items():
        lines.append(f"\n  {criterion} (Overall: {crit_data['overall']:.3f}):")
        agent_scores = [(agent, data["overall"]) for agent, data in crit_data["by_agent"].items()]
        agent_scores.sort(key=lambda x: x[1], reverse=True)
        for agent_name, score in agent_scores:
            lines.append(f"    {agent_name}: {score:.3f}")

    lines.append("")
    lines.append("=" * 80)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate agent statistics from evaluated graph output."
    )
    parser.add_argument(
        "graph_file",
        type=str,
        help="Path to the graph_output.json file with evaluation results"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for aggregated statistics (default: agent_stats.json)"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate a human-readable summary report"
    )
    parser.add_argument(
        "--insights",
        action="store_true",
        help="Generate LLM-based insights and recommendations using Gemini 2.0"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Score threshold for identifying low-performing areas (default: 0.7)"
    )

    args = parser.parse_args()

    # Load graph data
    try:
        with open(args.graph_file, 'r', encoding='utf-8') as f:
            graph_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found: {args.graph_file}")
        return 1
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {args.graph_file}: {e}")
        return 1

    # Extract statistics
    print("Extracting agent statistics...")
    by_agent_stats = extract_agent_stats_from_graph(graph_data)

    print("Extracting criterion statistics...")
    by_criterion_stats = extract_criterion_stats_from_graph(graph_data)

    print("Extracting scenario statistics...")
    by_scenario_stats = extract_scenario_stats_from_graph(graph_data)

    # Combine all statistics
    stats = {
        "by_agent": by_agent_stats,
        "by_criterion": by_criterion_stats,
        "by_scenario": by_scenario_stats
    }

    # Determine output file
    if args.output:
        output_file = args.output
    else:
        # Use same directory as input file
        import os
        base_dir = os.path.dirname(args.graph_file)
        output_file = os.path.join(base_dir, "agent_stats.json")

    # Save statistics
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2)
        print(f"\n✓ Statistics saved to: {output_file}")
    except Exception as e:
        print(f"Error saving statistics: {e}")
        return 1

    # Generate report if requested
    if args.report:
        report = generate_summary_report(stats)
        print("\n" + report)

        report_file = output_file.replace('.json', '_report.txt')
        try:
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(report)
            print(f"\n✓ Report saved to: {report_file}")
        except Exception as e:
            print(f"Error saving report: {e}")

    # Generate LLM insights if requested
    if args.insights:
        insights = generate_llm_insights(stats, graph_data, args.threshold)
        print("\n" + insights)

        insights_file = output_file.replace('.json', '_insights.md')
        try:
            with open(insights_file, 'w', encoding='utf-8') as f:
                f.write(insights)
            print(f"\n✓ Insights saved to: {insights_file}")
        except Exception as e:
            print(f"Error saving insights: {e}")

    return 0


if __name__ == "__main__":
    exit(main())