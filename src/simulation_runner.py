import os
import sys
from dotenv import load_dotenv

# --- Force-load .env from labryon/.env at the very top ---
labryon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
dotenv_path = os.path.join(labryon_root, '.env')


import json
import uuid
import logging
import time
import argparse
from typing import List, Dict, Any, Optional, Tuple
from unittest.mock import patch
from types import SimpleNamespace

import yaml
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError
)
try:
    from func_timeout import func_timeout, FunctionTimedOut
except ImportError:
    # Fallback if library not present, though recommended
    func_timeout = None
    FunctionTimedOut = Exception

# --- Logging Setup with Red Error/Warning ---
class RedFormatter(logging.Formatter):
    RED = "\033[91m"
    BLUE_BOLD = "\033[1;34m"
    BOLD = "\033[1m"
    ITALIC = "\033[3m"
    RESET = "\033[0m"
    
    COLORS = [
        "\033[1;34m", # Blue
        "\033[1;32m", # Green
        "\033[1;35m", # Magenta
        "\033[1;36m", # Cyan
        "\033[1;33m", # Yellow
    ]
    
    def format(self, record):
        formatted_msg = super().format(record)
        if record.levelno >= logging.WARNING:
            return f"{self.RED}{formatted_msg}{self.RESET}"
        
        if getattr(record, 'highlight', False):
            # Custom format for highlighted messages: Colored INFO based on task_id, Bold Message
            
            # Determine color based on task_id
            task_id = getattr(record, 'task_id', None)
            color = self.BLUE_BOLD # Default
            if task_id:
                try:
                    # Extract the first number from "1/3" or similar
                    idx = int(str(task_id).split('/')[0]) if '/' in str(task_id) else 0
                    # Use modulo to cycle through colors, 0-indexed
                    color = self.COLORS[(idx - 1) % len(self.COLORS)]
                except (ValueError, IndexError):
                    pass
            
            # Use task_id if available (e.g. "1/3"), otherwise process ID
            identifier = getattr(record, 'task_id', record.process)
            
            levelname = f"{color}{record.levelname}{self.RESET}"
            
            # Split message into header (first line) and body (rest)
            full_msg = record.getMessage()
            if "\n" in full_msg:
                header, body = full_msg.split("\n", 1)
                # Header: Color + Bold
                formatted_header = f"{self.BOLD}{header}{self.RESET}"
                # Body: Italic (no color)
                formatted_body = f"{self.ITALIC}{body}{self.RESET}"
                return f"{levelname} - {identifier} - {color}{formatted_header}\n{formatted_body}"
            else:
                # Single line: All Color + Bold
                return f"{levelname} - {identifier} - {color}{self.BOLD}{full_msg}{self.RESET}"
            
        return formatted_msg

formatter = RedFormatter('%(asctime)s - %(levelname)s - %(process)d - %(message)s', datefmt='%H:%M:%S')
handler = logging.StreamHandler()
handler.setFormatter(formatter)
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)

# --- Path Setup ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# --- Local Imports ---
from mock_tools import ToolCallRecorder, create_mock_tools_from_config
from data_models import AgentResponse
from agno_mocks import create_mock_team

# --- API Exception Imports ---
try:
    from google.api_core.exceptions import ServiceUnavailable
except ImportError:
    ServiceUnavailable = None
try:
    import anthropic
except ImportError:
    pass

# --- Composo Imports ---
try:
    from composo.tracing import AgentTracer
    from composo import Composo
    from composo_wrapper import init_composo_tracing
    COMPOSO_AVAILABLE = True
except ImportError:
    COMPOSO_AVAILABLE = False

# --- Mock Implementations & JSON Encoder ---
class FakeMemoryService:
    def query_fact_memory(self, *args, **kwargs): return []
    def add_fact_memory(self, *args, **kwargs): pass
    def store_operation_memory(self, *args, **kwargs): pass

class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, uuid.UUID): return str(obj)
        if 'TeamRunOutput' in str(type(obj)): return str(obj)
        if hasattr(obj, '__class__') and 'EvaluationResult' in obj.__class__.__name__:
            return {"score": obj.score, "explanation": obj.explanation}
        if hasattr(obj, '__class__') and 'Trace' in obj.__class__.__name__:
            return {"_type": "CompozoTrace", "note": "Trace object not serializable"}
        return super().default(obj)

# --- SimulationOrchestrator ---
class SimulationOrchestrator:
    def __init__(self, worker_config: Dict[str, Any], partition_context: Optional[List[str]] = None, system_prompt: Optional[str] = None, tool_contexts: Optional[Dict[str, Any]] = None, task_id: str = ""):
        self.worker_config = worker_config
        self.partition_context = partition_context
        self.system_prompt = system_prompt
        self.tool_contexts = tool_contexts or {}
        self.task_id = task_id
        self.tool_recorder = ToolCallRecorder()
        self.patches = []
        self.enable_tracing = self._read_tracing_config()
        self.tool_calls_source = self._load_tool_calls_source()
        self.custom_criteria = self._load_custom_criteria()
        self.communication_tools = self._load_communication_tools_config()
        self.composo_client = None
        if self.enable_tracing and COMPOSO_AVAILABLE:
            self._init_composo()
        self.supervisor = self._initialize_supervisor()

    def _read_tracing_config(self) -> bool:
        try:
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'evaluation_criteria.yaml')
            with open(config_path, 'r') as f: return yaml.safe_load(f).get('enable_tracing', False)
        except Exception: return False

    def _load_custom_criteria(self) -> List[str]:
        try:
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'evaluation_criteria.yaml')
            with open(config_path, 'r') as f: return yaml.safe_load(f).get('trace_criteria', [])
        except Exception: return []

    def _load_tool_calls_source(self) -> str:
        try:
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'evaluation_criteria.yaml')
            with open(config_path, 'r') as f: return yaml.safe_load(f).get('tool_calls_source', 'recorder')
        except Exception: return 'recorder'

    def _load_communication_tools_config(self) -> List[str]:
        try:
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'llm_generator_config.yaml')
            with open(config_path, 'r') as f:
                return yaml.safe_load(f).get('external_communication_tools', [])
        except Exception:
            return []

    def _init_composo(self):
        # Determine instrument based on provider
        instruments = ["anthropic"] # Default
        try:
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'evaluation_criteria.yaml')
            with open(config_path, 'r') as f:
                provider = yaml.safe_load(f).get('llm_provider', 'anthropic')
                if provider == 'google': instruments = ["google"]
                elif provider == 'openai': instruments = ["openai"]
                elif provider == 'anthropic': instruments = ["anthropic"]
        except Exception: pass

        if init_composo_tracing(instruments=instruments):
            api_key = os.getenv("COMPOSO_API_KEY")
            if api_key:
                self.composo_client = Composo(api_key=api_key)
                logger.debug("Composo tracing initialized successfully")
            else:
                logger.warning("Tracing is enabled, but COMPOSO_API_KEY is not set. Tracing will be disabled.")
                self.enable_tracing = False
        else:
            logger.warning("Failed to initialize Composo tracing. Tracing will be disabled.")
            self.enable_tracing = False

    def _initialize_supervisor(self):
        """Create standalone mock supervisor for testing without backend imports."""
        mock_tool_cache = {}
        supervisor_tools_list = create_mock_tools_from_config(
            self.worker_config.get('supervisor_tools', {}),
            self.tool_recorder,
            mock_tool_cache,
            self.tool_contexts,
            agent_name="supervisor"
        )

        # Ensure exit_session is always available
        system_tools_config = {
            "system": {
                "operations": ["exit_session"]
            }
        }
        system_tools = create_mock_tools_from_config(
            system_tools_config,
            self.tool_recorder,
            mock_tool_cache,
            self.tool_contexts,
            agent_name="supervisor"
        )
        supervisor_tools_list.extend(system_tools)

        # Build system prompt with pre-context if available
        full_system_prompt = self.system_prompt or ""
        
        # Inject SOP if available in worker config
        sop = self.worker_config.get('sop')
        if sop:
            full_system_prompt += f"\n\nStandard Operating Procedure (SOP):\n{sop}"

        if self.partition_context:
            context_text = "\n\n".join([
                f"**Previous Work Completed ({i+1}/{len(self.partition_context)}):** {ctx}"
                for i, ctx in enumerate(self.partition_context)
            ])
            full_system_prompt = f"{full_system_prompt}\n\n<previous_work_context>\n{context_text}\n</previous_work_context>"

        return create_mock_team(
            agent_configs=self.worker_config.get('agents', []),
            supervisor_tools=supervisor_tools_list,
            system_prompt=full_system_prompt,
            worker_name=self.worker_config.get('name') or "Test Worker",
            tool_recorder=self.tool_recorder,
            mock_tool_cache=mock_tool_cache,
            tool_contexts=self.tool_contexts,
            create_mock_tools_fn=create_mock_tools_from_config
        )

    def cleanup(self):
        for p in self.patches: p.stop()

    def _is_retryable_api_error(self, exception):
        """Check if exception is a retryable API error."""
        if ServiceUnavailable and isinstance(exception, ServiceUnavailable):
            return True
        if hasattr(exception, '__class__') and 'APIStatusError' in exception.__class__.__name__:
            return hasattr(exception, 'status_code') and exception.status_code in [429, 500, 503, 529]
        return False

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ServiceUnavailable, anthropic.APIStatusError)),
        reraise=True
    )
    def _run_team_with_retry(self, team, user_message: str) -> Any:
        """Wraps team.run with exponential backoff retry for transient API errors."""
        try:
            logger.debug(f"Calling team.run for task {self.task_id}...")
            
            # Define the actual run function
            def run_fn():
                return team.run(user_message)
            
            # Use func_timeout if available to enforce hard limit
            if func_timeout:
                try:
                    # 60s timeout for the entire run call
                    response = func_timeout(60, run_fn)
                    logger.debug(f"team.run returned for task {self.task_id}")
                    return response
                except FunctionTimedOut:
                    logger.error(f"team.run TIMED OUT for task {self.task_id} after 60s")
                    raise ServiceUnavailable("Local timeout enforced on team.run")
            else:
                response = team.run(user_message)
                logger.debug(f"team.run returned for task {self.task_id}")
                return response
                
        except (ServiceUnavailable, anthropic.APIStatusError) as e:
            # Log specific API errors that trigger retry
            logger.warning(f"API Error in task {self.task_id}: {e}. Retrying...")
            raise e
        except Exception as e:
            # Log other errors that might not trigger retry
            logger.error(f"Unexpected error in task {self.task_id}: {e}")
            raise e

    def run_episode(self, user_messages: List[str]) -> Dict[str, Any]:
        turns = []
        team = self.supervisor.team

        for turn_idx, user_message in enumerate(user_messages):
            # Format: Scenario [1/3], Turn 1/1 : ...message...
            task_id_str = f"[{self.task_id}]" if self.task_id else ""
            turn_str = f"Turn {turn_idx + 1}/{len(user_messages)}"
            
            # Last 30 words
            words = user_message.split()
            user_message_preview = " ".join(words[-30:])
            if len(words) > 30:
                user_message_preview = "..." + user_message_preview
            
            log_msg = f"Scenario {task_id_str}, {turn_str} :\n{user_message_preview}"
            logger.info(log_msg, extra={'highlight': True, 'task_id': self.task_id})
            
            logger.debug(f"--- {task_id_str} {turn_str} Full Message --- User >>> {user_message}")
            calls_before = len(self.tool_recorder.get_calls())
            start_time = time.time()

            trace_analysis = None
            if self.enable_tracing and self.composo_client:
                try:
                    agent_response, trace_analysis = self._run_episode_with_tracing(
                        team, user_message, turn_idx + 1, len(user_messages)
                    )
                except (RetryError, Exception) as e:
                    logger.warning(f"Tracing failed, falling back to non-traced execution: {e}")
                    agent_response = self._run_team_with_retry(team, user_message)
            else:
                agent_response = self._run_team_with_retry(team, user_message)

            new_calls = self.tool_recorder.get_calls()[calls_before:]

            # Log tool calls if debug is enabled
            for call in new_calls:
                logger.debug(f"[{self.task_id}] Tool Call: {call.get('tool_name')} | Params: {str(call.get('parameters', {}))[:500]}")

            # Extract all external communications from tool calls
            final_content = []
            for c in new_calls:
                if c.get('tool_name') in self.communication_tools:
                    # Try to get body or message, otherwise include a string representation
                    content = c.get('parameters', {}).get('body') or c.get('parameters', {}).get('message')
                    if content:
                        final_content.append(content)
                    else:
                        # Convert dict to string if no body/message found
                        final_content.append(f"Tool call: {c.get('tool_name')} with params: {c.get('parameters', {})}")

            # Organize tool calls by agent
            tool_calls_by_agent = {}
            for call in new_calls:
                agent_name = call.get('agent_name', 'unknown')
                if agent_name not in tool_calls_by_agent:
                    tool_calls_by_agent[agent_name] = []
                tool_calls_by_agent[agent_name].append(call)

            # Add tool calls by agent to trace analysis if it exists
            if trace_analysis:
                trace_analysis["tool_calls_by_agent"] = tool_calls_by_agent
                trace_analysis["turn_index"] = turn_idx

            turns.append({
                "turn_index": turn_idx,
                "user_message": user_message,
                "assistant_response": final_content,
                "assistant_internal": agent_response.content if hasattr(agent_response, 'content') else str(agent_response),
                "tool_calls": new_calls,
                "tool_calls_by_agent": tool_calls_by_agent,
                "latency_seconds": time.time() - start_time,
                "trace_analysis": trace_analysis
            })
            # Log turn completion
            # logger.info(f"{prefix}Completed Turn {turn_idx + 1}/{len(user_messages)}", extra={'highlight': True, 'task_id': self.task_id})
        return {"simulation_results": turns}

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=5),
        reraise=False
    )
    def _parse_trace_evaluation(self, eval_results, custom_criteria):
        """
        Parse trace evaluation results, collecting performances for all sub-agents.

        Args:
            eval_results: Results from trace evaluation
            custom_criteria: List of criteria used for evaluation

        Returns:
            List of parsed evaluation dictionaries for all agents
        """
        criterion_scores = []
        agents_evaluated = set()

        for result, criterion in zip(eval_results, custom_criteria):
            try:
                # Extract results for all agents
                agents_results = result.results_by_agent_name

                # Iterate through all agents in the trace
                for agent_name, agent_data in agents_results.items():
                    agents_evaluated.add(agent_name)
                    try:
                        # Get all instance results for this agent
                        instances_results = agent_data.results_by_agent_instance_id

                        # Process each instance for the agent
                        for instance_id, instance_data in instances_results.items():
                            # Extract score and explanation
                            score = instance_data.score
                            explanation = instance_data.explanation

                            # Handle potential None values
                            if score is None:
                                logger.warning(f"Evaluation for criterion '{criterion}', agent '{agent_name}' (instance {instance_id}) returned a null score.")

                            criterion_scores.append({
                                "criterion": str(criterion),
                                "agent_name": agent_name,
                                "instance_id": instance_id,
                                "score": score,
                                "explanation": explanation
                            })

                    except (StopIteration, AttributeError, KeyError, TypeError) as instance_error:
                        logger.warning(f"Could not parse instance results for agent '{agent_name}' in criterion '{criterion}'. Error: {instance_error}")

            except (StopIteration, AttributeError, KeyError, TypeError) as e:
                # This will catch errors if the overall structure is not as expected
                logger.warning(f"Could not parse nested evaluation result for criterion '{criterion}'. Error: {e}")

        # Log summary of agents evaluated
        if agents_evaluated:
            logger.info(f"Evaluated {len(agents_evaluated)} agents: {', '.join(sorted(agents_evaluated))}")

        return criterion_scores

    def _extract_agent_interactions_from_trace(self, trace) -> Dict[str, List[Dict[str, Any]]]:
        """
        Extract tool calls from the Composo trace, organized by agent.

        Args:
            trace: MultiAgentTrace object from Composo

        Returns:
            Dict mapping agent_name to list of tool calls made by that agent
        """
        interactions_by_agent = {}

        try:
            if not hasattr(trace, 'agent_instance_by_id'):
                logger.warning("Trace does not have agent_instance_by_id attribute")
                return interactions_by_agent

            # Iterate through all agent instances
            for agent_id, agent_instance in trace.agent_instance_by_id.items():
                agent_name = getattr(agent_instance, 'name', 'unknown')

                if not hasattr(agent_instance, 'interactions'):
                    continue

                agent_tool_calls = []
                for interaction in agent_instance.interactions:
                    # Skip if interaction is just an ID reference
                    if isinstance(interaction, str):
                        continue

                    # Extract tool calls if available
                    if hasattr(interaction, 'tool_calls') and interaction.tool_calls:
                        for tc in interaction.tool_calls:
                            agent_tool_calls.append({
                                "agent_id": agent_id,
                                "agent_name": agent_name,
                                "tool_name": getattr(tc, 'name', None),
                                "arguments": getattr(tc, 'arguments', None)
                            })

                if agent_tool_calls:
                    interactions_by_agent[agent_name] = agent_tool_calls

            logger.info(f"Extracted tool calls for {len(interactions_by_agent)} agents from trace")

        except Exception as e:
            logger.warning(f"Failed to extract tool calls from trace: {e}", exc_info=True)

        return interactions_by_agent

    def _run_episode_with_tracing(self, team, user_message: str, turn_idx: int = 0, total_turns: int = 0) -> Tuple[Any, Optional[Dict]]:
        try:
            if not COMPOSO_AVAILABLE or not AgentTracer:
                logger.warning("Composo tracing not available")
                return self._run_team_with_retry(team, user_message), None

            prefix = f"Scenario [{self.task_id}]" if self.task_id else ""
            turn_info = f"Turn {turn_idx}/{total_turns}" if total_turns > 0 else ""
            
            # logger.info(f"{prefix}{turn_info}Starting Agentic Run", extra={'highlight': True, 'task_id': self.task_id})
            start_time = time.time()

            with AgentTracer("orchestrator") as tracer:
                response = self._run_team_with_retry(team, user_message)

            trace_duration = time.time() - start_time
            # logger.info(f"{prefix}{turn_info}Agentic Run Ended (Duration: {trace_duration:.2f}s)", extra={'highlight': True, 'task_id': self.task_id})

            if tracer and tracer.trace and self.custom_criteria and self.composo_client:
                logger.debug(f"Evaluating trace with {len(self.custom_criteria)} custom criteria")

                eval_start_time = time.time()
                try:
                    eval_results = self.composo_client.evaluate_trace(tracer.trace, criteria=self.custom_criteria)
                    eval_duration = time.time() - eval_start_time

                    # Parse evaluation results
                    analysis = self._parse_trace_evaluation(eval_results, self.custom_criteria)

                    # Reorganize by agent
                    by_agent = {}
                    all_scores = []

                    # Group evaluations by agent
                    for eval_item in analysis:
                        agent_name = eval_item.get("agent_name")
                        if agent_name not in by_agent:
                            by_agent[agent_name] = {"evaluations": []}

                        # Remove agent_name from evaluation item since it's now the key
                        eval_copy = {k: v for k, v in eval_item.items() if k != "agent_name"}
                        by_agent[agent_name]["evaluations"].append(eval_copy)
                        
                        if "score" in eval_copy and eval_copy["score"] is not None:
                            all_scores.append(eval_copy["score"])

                    # Conditionally add tool calls from Composo trace based on configuration
                    # Conditionally add tool calls from Composo trace based on configuration
                    if self.tool_calls_source == "composo":
                        logger.debug("Using Composo trace as tool calls source")
                        interactions_by_agent = self._extract_agent_interactions_from_trace(tracer.trace)

                        for agent_name, tool_calls in interactions_by_agent.items():
                            if agent_name not in by_agent:
                                by_agent[agent_name] = {"evaluations": []}
                            by_agent[agent_name]["tool_calls_from_trace"] = tool_calls
                    else:
                        logger.debug("Using ToolCallRecorder as tool calls source (Composo tool calls will not be extracted)")

                    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
                    
                    # Consolidate info into one log line
                    agents_count = len(by_agent)
                    tool_agents_count = len(interactions_by_agent) if self.tool_calls_source == 'composo' else "N/A"
                    
                    logger.info(f"{prefix}, {turn_info} : Evaluation Score: {avg_score:.2f} (Duration: {eval_duration:.2f}s, Agents: {agents_count}, Tool Agents: {tool_agents_count})", extra={'highlight': True, 'task_id': self.task_id})

                    if analysis:
                        return response, {
                            "by_agent": by_agent,
                            "trace_duration": trace_duration,
                            "eval_duration": eval_duration,
                            "criteria_count": len(self.custom_criteria)
                        }

                except Exception as eval_error:
                    logger.error(f"Trace evaluation failed: {eval_error}", exc_info=True)

            return response, None

        except Exception as e:
            logger.error(f"Error during traced execution: {e}", exc_info=True)
            raise

# --- Main Execution Logic ---
def format_task_id(identifiers: Dict[str, Any]) -> str:
    t = identifiers.get('type')
    s_idx = identifiers.get('s_idx', '?')
    p_idx = identifiers.get('p_idx', '?')
    up_idx = identifiers.get('up_idx', '?')
    
    if t == 'scenario_happy':
        return f"Scenario {s_idx} (Happy Path)"
    elif t == 'scenario_unhappy':
        return f"Scenario {s_idx} (Unhappy Path)"
    elif t == 'partition_happy':
        return f"Scenario {s_idx}, Partition {p_idx} (Happy Path)"
    elif t == 'partition_unhappy':
        return f"Scenario {s_idx}, Partition {p_idx} (Unhappy Path)"
    elif t == 'unhappy':
        return f"Scenario {s_idx}, Partition {p_idx}, Unhappy Path {up_idx}"
    return str(identifiers)

def execute_simulation_episode(
    worker_config_dict: Dict[str, Any],
    user_messages: List[str],
    partition_context: List[str],
    result_identifiers: Dict[str, Any],
    system_prompt: str,
    tool_contexts: Dict[str, Any],
    task_idx: int = 1,
    total_tasks: int = 1
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if not user_messages: return result_identifiers, []
    
    task_name = format_task_id(result_identifiers)
    task_label = f"{task_idx}/{total_tasks}"
    logger.info(f"Scenario [{task_label}] STARTED: {task_name}", extra={'highlight': True, 'task_id': task_label})
    
    orchestrator = SimulationOrchestrator(
        worker_config_dict, 
        partition_context=partition_context, 
        system_prompt=system_prompt,
        tool_contexts=tool_contexts,
        task_id=task_label
    )
    try:
        episode_result = orchestrator.run_episode(user_messages)
        logger.info(f"Scenario [{task_label}] COMPLETED: {task_name}", extra={'highlight': True, 'task_id': task_label})
        turns = episode_result.get("simulation_results", [])
        
        agent_responses = []
        for turn_idx, turn in enumerate(turns):
            response_content = turn.get("assistant_response")
            internal_content = turn.get("assistant_internal")

            # response_content is now a list of all external communications
            response_list = response_content if isinstance(response_content, list) else (
                [response_content] if response_content is not None else []
            )
            internal_list = [internal_content] if internal_content is not None else []

            # If tracing is disabled, create same structure as tracing-enabled for e2e evaluation
            trace_data = turn.get("trace_analysis")
            if trace_data is None:
                trace_data = {
                    "by_agent": None,  # Only filled when tracing is enabled
                    "tool_calls_by_agent": turn.get("tool_calls_by_agent", {}),
                    "turn_index": turn_idx
                }

            agent_responses.append(
                AgentResponse(
                    response=response_list,
                    internal=internal_list,
                    trace=trace_data
                )
            )
        
        result_data = [resp.model_dump() for resp in agent_responses]
        return result_identifiers, result_data
    finally:
        orchestrator.cleanup()

def _clear_evaluation_fields(sop_graph_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Clears all evaluation summary fields from the SOP graph dictionary."""
    sop_graph_dict['evaluation_summary'] = None
    for scenario in sop_graph_dict.get("scenarios", []):
        scenario['evaluation_summary'] = None
        for partition in scenario.get("partitions", []):
            partition['evaluation_summary'] = None

            # Clear evaluation data from responses
            for response_key in ['happy_path_response', 'partition_unhappy_path_response', 'scenario_happy_path_response', 'scenario_unhappy_path_response']:
                if response_key in partition:
                    for response in partition[response_key]:
                        if 'evaluation' in response:
                            response['evaluation'] = None

            if 'unhappy_path_responses' in partition:
                for response_group in partition['unhappy_path_responses']:
                    for response in response_group:
                        if 'evaluation' in response:
                            response['evaluation'] = None
    return sop_graph_dict

def _initialize_response_fields(sop_graph_dict: Dict[str, Any], args: Optional[argparse.Namespace]) -> None:
    """Initialize response fields based on requested simulation flags."""
    if not args:
        return

    for s in sop_graph_dict.get("scenarios", []):
        if args.scenario_happy:
            s['scenario_happy_path_response'] = []
        if args.scenario_unhappy:
            s['scenario_unhappy_path_response'] = []
        for p in s.get("partitions", []):
            if args.partition_happy:
                p['happy_path_response'] = []
            if args.partition_unhappy:
                p['partition_unhappy_path_response'] = []
            if args.unhappy:
                p['unhappy_path_responses'] = [[] for _ in range(len(p.get("unhappy_paths", [])))]

def _build_scenario_tasks(
    scenarios: List[Dict[str, Any]],
    worker_config: Dict[str, Any],
    system_prompt: str,
    tool_contexts: Dict[str, Any],
    args: Optional[argparse.Namespace]
) -> List[Tuple]:
    """Build simulation tasks for scenario-level paths."""
    tasks = []
    if not args:
        return tasks

    for s_idx, s in enumerate(scenarios):
        if args.scenario_happy and s.get("scenario_happy_path"):
            tasks.append((
                worker_config,
                s["scenario_happy_path"],
                [],
                {'type': 'scenario_happy', 's_idx': s_idx, 'scenario_idx': s_idx},
                system_prompt,
                tool_contexts
            ))

        if args.scenario_unhappy and s.get("scenario_unhappy_path"):
            tasks.append((
                worker_config,
                s["scenario_unhappy_path"],
                [],
                {'type': 'scenario_unhappy', 's_idx': s_idx, 'scenario_idx': s_idx},
                system_prompt,
                tool_contexts
            ))

    return tasks

def _build_partition_tasks(
    scenarios: List[Dict[str, Any]],
    worker_config: Dict[str, Any],
    system_prompt: str,
    tool_contexts: Dict[str, Any],
    args: Optional[argparse.Namespace]
) -> List[Tuple]:
    """Build simulation tasks for partition-level paths."""
    tasks = []
    if not args:
        return tasks

    for s_idx, s in enumerate(scenarios):
        for p_idx, p in enumerate(s.get("partitions", [])):
            if args.partition_happy and p.get("happy_path"):
                tasks.append((
                    worker_config,
                    p["happy_path"],
                    p.get("context", []),
                    {'type': 'partition_happy', 's_idx': s_idx, 'p_idx': p_idx, 'scenario_idx': s_idx},
                    system_prompt,
                    tool_contexts
                ))

            if args.partition_unhappy and p.get("partition_unhappy_path"):
                tasks.append((
                    worker_config,
                    p["partition_unhappy_path"],
                    p.get("context", []),
                    {'type': 'partition_unhappy', 's_idx': s_idx, 'p_idx': p_idx, 'scenario_idx': s_idx},
                    system_prompt,
                    tool_contexts
                ))

            if args.unhappy:
                for up_idx, unhappy_path in enumerate(p.get("unhappy_paths", [])):
                    # The 'scenario' key is expected to contain the messages for the unhappy path
                    # If it's missing, this task will be skipped.
                    if unhappy_path.get("scenario"):
                        tasks.append((
                            worker_config,
                            unhappy_path["scenario"],
                            p.get("context", []),
                            {'type': 'unhappy', 's_idx': s_idx, 'p_idx': p_idx, 'up_idx': up_idx, 'scenario_idx': s_idx},
                            system_prompt,
                            tool_contexts
                        ))

    return tasks

def run_simulations_from_sop_graph(
    graph_path: str,
    parallel_processes: int = 1,
    args: Optional[argparse.Namespace] = None
):
    # Configure logging level based on debug flag
    if args and args.debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger("composo").setLevel(logging.INFO)
        logging.getLogger("agno").handlers = []
        logging.getLogger("agno").setLevel(logging.DEBUG)
        logging.getLogger("phi").handlers = []
        logging.getLogger("phi").setLevel(logging.DEBUG)
        logger.debug("Debug logging ENABLED")
    else:
        logger.setLevel(logging.INFO)
        logging.getLogger("composo").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("google").setLevel(logging.WARNING)
        
        # Aggressively silence agno/phi loggers
        for logger_name in ["agno", "phi"]:
            logging.getLogger(logger_name).handlers = []
            logging.getLogger(logger_name).setLevel(logging.WARNING)
            
        # Also catch any sub-loggers that might have been created
        for name in logging.root.manager.loggerDict:
            if name.startswith("agno") or name.startswith("phi"):
                logging.getLogger(name).setLevel(logging.WARNING)
                logging.getLogger(name).handlers = []

    logger.info(f"Loading SOP Graph from {graph_path}", extra={'highlight': True})

    # Check tracing configuration at the start
    try:
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'evaluation_criteria.yaml')
        with open(config_path, 'r') as f:
            eval_config = yaml.safe_load(f)
            enable_tracing = eval_config.get('enable_tracing', False)
            if enable_tracing:
                logger.debug("✓ Tracing is ENABLED in evaluation_criteria.yaml")
                if COMPOSO_AVAILABLE:
                    api_key = os.getenv("COMPOSO_API_KEY")
                    if api_key:
                        logger.debug("✓ Composo SDK available and COMPOSO_API_KEY is set")
                    else:
                        logger.warning("✗ Composo SDK available but COMPOSO_API_KEY is NOT set - tracing will be disabled")
                else:
                    logger.warning("✗ Composo SDK not available - tracing will be disabled")
            else:
                logger.debug("✗ Tracing is DISABLED in evaluation_criteria.yaml")
    except Exception as e:
        logger.warning(f"Could not read tracing configuration: {e}")

    if not os.path.exists(graph_path):
        logger.error(f"SOP Graph file not found: {graph_path}")
        sys.exit(1)
    with open(graph_path, 'r') as f:
        sop_graph_dict = json.load(f)
    
    sop_graph_dict = _clear_evaluation_fields(sop_graph_dict)

    worker_config_dict = sop_graph_dict.get("worker_config")
    if not worker_config_dict:
        logger.error("No 'worker_config' found in the SOP graph file. Please regenerate the graph file.")
        sys.exit(1)

    system_prompt = sop_graph_dict.get("system_prompt")
    if not system_prompt:
        logger.error("No system_prompt found in the SOP graph file. Please regenerate the graph file.")
        sys.exit(1)

    # Prepare tool contexts for mock tools
    knowledge_bases = worker_config_dict.get('knowledge_bases', [])
    flattened_kb = "\n\n---\n\n".join([
        f"Document: {kb.get('document_name', 'N/A')}\n\n{kb.get('content', '')}"
        for kb in knowledge_bases
    ])
    tool_contexts = {"knowledge_base_search": flattened_kb}

    # Initialize response fields and build task queue
    _initialize_response_fields(sop_graph_dict, args)

    scenarios = sop_graph_dict.get("scenarios", [])
    tasks = []
    tasks.extend(_build_scenario_tasks(scenarios, worker_config_dict, system_prompt, tool_contexts, args))
    tasks.extend(_build_partition_tasks(scenarios, worker_config_dict, system_prompt, tool_contexts, args))

    if not tasks:
        logger.warning("No simulation tasks generated based on the provided flags.")
        return

    # Filter by scenario ID if specified
    if args.scenario and args.scenario.lower() != 'all':
        try:
            target_idx = int(args.scenario)
            # Debug: Print available scenario indices
            if tasks:
                logger.debug(f"Available task scenario indices: {[t[3].get('scenario_idx') for t in tasks]}")
                logger.debug(f"Target scenario index: {target_idx}")
            
            tasks = [t for t in tasks if t[3].get('scenario_idx') == target_idx]
        except ValueError:
            logger.warning(f"Invalid scenario ID '{args.scenario}'. Running all scenarios.")

    logger.info(f"Generated {len(tasks)} simulation tasks")

    # Add index and total to tasks
    tasks_with_indices = [
        t + (i + 1, len(tasks)) for i, t in enumerate(tasks)
    ]

    # Execute tasks
    results = []
    # --- Configuration Summary ---
    try:
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'evaluation_criteria.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
            
        provider = config.get('llm_provider', 'anthropic')
        model_key = f"{provider}_model"
        model_name = config.get(model_key, 'unknown')
        tracing_enabled = config.get('enable_tracing', False)
        
        summary_msg = (
            f"\n==================== Simulation Configuration ====================\n"
            f"Total Tasks       : {len(tasks_with_indices)}\n"
            f"Parallel Processes: {parallel_processes}\n"
            f"LLM Provider      : {provider.upper()}\n"
            f"Model             : {model_name}\n"
            f"Tracing           : {'ENABLED' if tracing_enabled else 'DISABLED'}\n"
            f"=================================================================="
        )
        logger.info(summary_msg, extra={'highlight': True, 'task_id': 'CONFIG'})
    except Exception as e:
        logger.warning(f"Could not load configuration summary: {e}")

    if parallel_processes > 1 and len(tasks) > 1:
        import multiprocessing
        logger.info(f"Running simulations in parallel with {parallel_processes} processes")
        with multiprocessing.Pool(processes=parallel_processes) as pool:
            results = pool.starmap(execute_simulation_episode, tasks_with_indices)
    else:
        logger.info("Running simulations sequentially")
        results = [execute_simulation_episode(*task) for task in tasks_with_indices]

    for identifiers, result_data in results:
        if not result_data: continue
        res_type = identifiers['type']

        if res_type == 'scenario_happy':
            s_idx = identifiers['scenario_idx']
            sop_graph_dict['scenarios'][s_idx]['scenario_happy_path_response'] = result_data
        elif res_type == 'scenario_unhappy':
            s_idx = identifiers['scenario_idx']
            sop_graph_dict['scenarios'][s_idx]['scenario_unhappy_path_response'] = result_data
        elif res_type == 'partition_happy':
            s_idx, p_idx = identifiers['scenario_idx'], identifiers['p_idx']
            sop_graph_dict['scenarios'][s_idx]['partitions'][p_idx]['happy_path_response'] = result_data
        elif res_type == 'partition_unhappy':
            s_idx, p_idx = identifiers['scenario_idx'], identifiers['p_idx']
            sop_graph_dict['scenarios'][s_idx]['partitions'][p_idx]['partition_unhappy_path_response'] = result_data
        elif res_type == 'unhappy':
            s_idx, p_idx, up_idx = identifiers['scenario_idx'], identifiers['p_idx'], identifiers['up_idx']
            sop_graph_dict['scenarios'][s_idx]['partitions'][p_idx]['unhappy_path_responses'][up_idx] = result_data

    with open(graph_path, 'w') as f:
        json.dump(sop_graph_dict, f, indent=2, cls=CustomJSONEncoder)
    logger.info(f"Updated SOP Graph file with simulation results: {graph_path}")

if __name__ == '__main__':
    import multiprocessing
    parser = argparse.ArgumentParser(description="Run simulation scenarios from an SOP Graph and update it in-place.")
    parser.add_argument('--sop-graph', type=str, required=True, help='Path to the SOP Graph JSON file.')
    parser.add_argument('--parallel', type=int, nargs='?', const=10, default=1, help='Number of parallel processes.')

    parser.add_argument('--all', action='store_true', help='Run all available simulation paths.')
    parser.add_argument('--scenario-happy', action='store_true', help='Run end-to-end happy path.')
    parser.add_argument('--scenario-unhappy', action='store_true', help='Run end-to-end unhappy path.')
    parser.add_argument('--partition-happy', action='store_true', help='Run per-partition happy paths.')
    parser.add_argument('--partition-unhappy', action='store_true', help='Run per-partition unhappy paths.')
    parser.add_argument('--happy', action='store_true', help='Alias for --partition-happy.')
    parser.add_argument('--unhappy', action='store_true', help='Run individual unhappy path variations.')
    parser.add_argument('--scenario', type=str, default='all', help='Scenario ID to run (0-indexed) or "all"')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging for tool calls and messages.')

    args = parser.parse_args()

    if args.all:
        args.scenario_happy = True
        args.scenario_unhappy = True
        args.partition_happy = True
        args.partition_unhappy = True
        args.unhappy = True

    if not any([args.scenario_happy, args.scenario_unhappy, args.partition_happy, args.partition_unhappy, args.happy, args.unhappy]):
        print("No simulation flags provided. Use --all or specific flags. Exiting.")
        sys.exit(0)

    if args.happy:
        args.partition_happy = True

    run_simulations_from_sop_graph(args.sop_graph, args.parallel, args)
