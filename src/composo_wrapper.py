"""
Composo tracing wrapper for Agno-based agents.

Minimal integration layer for Composo agent tracing without requiring changes
to core Agno agent implementation.

Handles:
- Initialization of Composo tracing infrastructure
- Marking agent boundaries with AgentTracer context managers
- Hierarchical agent tracing for multi-agent systems
- Optional agent evaluation after execution

Note: This is designed for test-framework simulations and evaluation.
"""

import os
import time
import logging
from typing import Optional, Any, Dict, List
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Global state for Composo
_composo_initialized = False
_composo_available = False
_composo_client: Optional[Any] = None
_current_tracer_stack: List[Any] = []


def is_composo_available() -> bool:
    """Check if Composo SDK is available."""
    global _composo_available
    if _composo_available:
        return True

    try:
        import composo  # noqa: F401
        _composo_available = True
        return True
    except ImportError:
        _composo_available = False
        return False


def _load_tracing_config():
    """Load tracing configuration from evaluation_criteria.yaml"""
    try:
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'evaluation_criteria.yaml')
        with open(config_path, 'r') as f:
            import yaml
            config = yaml.safe_load(f)
            provider = config.get('llm_provider', 'openai')
            enable_tracing = config.get('enable_tracing', True)
            return provider, enable_tracing
    except Exception as e:
        logger.warning(f"Could not load tracing config: {e}")
        return 'openai', True


def init_composo_tracing(instruments: Optional[List[str]] = None) -> bool:
    """
    Initialize Composo tracing infrastructure.

    Args:
        instruments: List of instrument types ('openai', 'anthropic', etc.)
                    If not specified, will read from evaluation_criteria.yaml.

    Returns:
        True if initialization was successful, False otherwise.
    """
    global _composo_initialized, _composo_client

    if _composo_initialized:
        logger.info("Composo tracing already initialized")
        return _composo_client is not None

    if not is_composo_available():
        logger.warning("Composo SDK not available. Tracing will be disabled.")
        return False

    try:
        from composo.tracing import ComposoTracer, Instruments
        from composo import Composo
        import importlib

        # Load config from YAML if instruments not specified
        if instruments is None:
            provider, enable_tracing = _load_tracing_config()
            if not enable_tracing:
                logger.info("Tracing disabled in evaluation_criteria.yaml")
                return False
            instruments = [provider]
            logger.info(f"Using LLM provider from config: {provider}")

        # Map string instrument names to Instruments enum
        # Note: ANTHROPIC is not available in composo 0.2.40, only OPENAI
        instrument_objs = []
        instrument_map = {
            "openai": Instruments.OPENAI,
        }

        # Add ANTHROPIC if available (for future versions)
        if hasattr(Instruments, 'ANTHROPIC'):
            instrument_map["anthropic"] = Instruments.ANTHROPIC

        # Add GOOGLE_GENAI if available
        if hasattr(Instruments, 'GOOGLE_GENAI'):
            instrument_map["google"] = Instruments.GOOGLE_GENAI

        # Validate and collect instruments
        validated_instruments = []
        for instrument in instruments:
            instrument_lower = instrument.lower()
            
            if instrument_lower in instrument_map:
                instrument_objs.append(instrument_map[instrument_lower])
                validated_instruments.append(instrument_lower)
            else:
                logger.warning(f"Unknown tracing instrument type skipped: {instrument}")

        if not instrument_objs:
            logger.warning("No valid tracing instruments found. Tracing will not be initialized.")
            return False

        # Logging detailed tracing configuration
        logger.debug(f"Configuring Composo Tracing:")
        logger.debug(f"  - Instruments: {', '.join(validated_instruments)}")

        # Get SDK version for logging
        try:
            spec = importlib.util.find_spec('composo')
            if spec:
                logger.debug(f"  - SDK Path: {spec.origin}")
        except Exception:
            pass

        # Initialize tracing
        ComposoTracer.init(instruments=instrument_objs)
        logger.debug(f"Composo tracing initialized successfully")

        # Initialize Composo client for evaluation
        api_key = os.getenv("COMPOSO_API_KEY")
        if not api_key:
            logger.warning("COMPOSO_API_KEY not set. Trace evaluation will be unavailable.")
            _composo_client = None
        else:
            # Mask the API key for security
            masked_key = f"{api_key[:4]}{'*' * (len(api_key) - 8)}{api_key[-4:]}"
            logger.debug(f"Composo evaluation client initialized (key: {masked_key})")
            _composo_client = Composo(api_key=api_key)

        _composo_initialized = True
        return True

    except Exception as e:
        logger.error(f"Failed to initialize Composo tracing", exc_info=True)
        _composo_initialized = False
        return False


@contextmanager
def trace_agent_execution(
    agent_name: str,
    parent_tracer: Optional[Any] = None,
    capture_output: bool = False,
) -> Any:
    """
    Context manager for tracing a single agent's execution.

    This creates a hierarchical AgentTracer context and optionally captures
    the result of the execution.

    Args:
        agent_name: Name of the agent being traced
        parent_tracer: Optional parent tracer for hierarchical tracing
        capture_output: Whether to capture and return the tracer object

    Yields:
        Tracer object that can be used for evaluation

    Example:
        with trace_agent_execution("supervisor") as tracer:
            result = supervisor.team.run(input_text)
        # tracer.trace contains the captured LLM calls
    """
    if not is_composo_available():
        # If Composo not available, yield None
        logger.debug(f"[TRACING] Skipping tracing for {agent_name}: Composo not available")
        yield None
        return

    try:
        from composo.tracing import AgentTracer

        logger.debug(f"[TRACING] Starting trace for agent: {agent_name}")
        start_time = time.time()

        # Create a new AgentTracer context
        with AgentTracer(agent_name) as tracer:
            _current_tracer_stack.append(tracer)
            try:
                logger.debug(f"[TRACING] Tracer created for {agent_name}, yielding...")
                yield tracer
            finally:
                if _current_tracer_stack and _current_tracer_stack[-1] is tracer:
                    _current_tracer_stack.pop()
                    logger.debug(f"[TRACING] Trace ended for agent: {agent_name} (duration: {time.time() - start_time:.2f}s)")
    except Exception as e:
        logger.debug(f"[TRACING] ERROR during agent tracing for '{agent_name}': {e}")
        logger.warning(f"Error during agent tracing for '{agent_name}': {e}")
        yield None


@contextmanager
def trace_multi_agent_execution(
    orchestrator_name: str = "orchestrator",
    agent_names: Optional[List[str]] = None,
):
    """
    Context manager for tracing a multi-agent orchestration.

    This sets up a top-level tracer for the orchestrator and optionally
    marks individual agents within the orchestration.

    Args:
        orchestrator_name: Name of the orchestrating agent
        agent_names: Optional list of sub-agent names (for documentation)

    Yields:
        Tracer object containing all captured LLM calls

    Example:
        with trace_multi_agent_execution("supervisor", ["agent1", "agent2"]) as tracer:
            # Run supervisor with multiple agents
            result = supervisor.team.run(input_text)

        # Evaluate the trace
        evaluation_results = evaluate_trace(tracer.trace)
    """
    if not is_composo_available():
        logger.debug(f"[TRACING] Composo not available, skipping trace for {orchestrator_name}")
        yield None
        return

    try:
        from composo.tracing import AgentTracer

        logger.debug(f"[TRACING] Starting multi-agent trace context for: {orchestrator_name}")
        start_time = time.time()
        with AgentTracer(orchestrator_name) as tracer:
            _current_tracer_stack.clear()
            _current_tracer_stack.append(tracer)
            logger.debug(f"[TRACING] Tracer created and pushed to stack: {tracer}")

            # Log sub-agents if provided
            if agent_names:
                logger.debug(f"[TRACING] Sub-agents: {', '.join(agent_names)}")

            try:
                yield tracer
            finally:
                duration = time.time() - start_time
                logger.debug(f"[TRACING] Multi-agent trace context ended for: {orchestrator_name} (duration: {duration:.2f}s)")
                _current_tracer_stack.clear()
    except Exception as e:
        logger.debug(f"[TRACING] ERROR during multi-agent tracing: {e}")
        import traceback
        traceback.print_exc()
        logger.warning(f"Error during multi-agent tracing: {e}")
        yield None


def evaluate_trace(
    trace: Any,
    evaluation_type: str = "agent",
    criteria: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """
    Evaluate a captured trace against specified criteria.

    Args:
        trace: Trace object from AgentTracer
        evaluation_type: Type of evaluation ("agent", "rag", etc.)
        criteria: Optional pre-defined criteria object.
                 If None, will use default criteria for the evaluation type.

    Returns:
        Dictionary containing evaluation results, or None if evaluation failed

    Example:
        with trace_agent_execution("my_agent") as tracer:
            result = agent.run(input_text)

        eval_results = evaluate_trace(tracer.trace, evaluation_type="agent")
        print(eval_results)
    """
    if trace is None or not _composo_client:
        logger.debug("Cannot evaluate trace: trace is None or Composo client not initialized")
        return None

    try:
        from composo.models import criteria as default_criteria

        # Use provided criteria or default based on evaluation type
        if criteria is None:
            if evaluation_type == "agent":
                criteria = default_criteria.agent
            elif evaluation_type == "rag":
                criteria = default_criteria.rag
            else:
                logger.warning(f"Unknown evaluation type: {evaluation_type}")
                return None

        # Evaluate the trace
        results = _composo_client.evaluate_trace(trace, criteria=criteria)

        # Format results for easier consumption
        evaluation_output = {
            "evaluation_type": evaluation_type,
            "status": "completed",
            "results": []
        }

        if hasattr(results, '__iter__'):
            for result in results:
                evaluation_output["results"].append({
                    "criteria": str(result.get("criteria", "unknown")) if isinstance(result, dict) else str(result),
                    "score": result.get("score") if isinstance(result, dict) else None,
                    "details": str(result)
                })

        logger.info(f"Trace evaluation completed: {len(evaluation_output['results'])} criteria evaluated")
        return evaluation_output

    except Exception as e:
        logger.error(f"Failed to evaluate trace: {e}")
        return None


def evaluate_rag(
    messages: List[Dict[str, str]],
    tools: Optional[List[Dict[str, Any]]] = None,
    criteria: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """
    Evaluate a conversation with RAG using Composo's RAG framework.

    This is specifically for RAG evaluation which requires:
    - Properly formatted messages (list of dicts with role, content)
    - Tool schemas (optional)
    - RAG-specific criteria (faithfulness, completeness, precision, relevance)

    Args:
        messages: List of message dicts with 'role' and 'content' keys
        tools: Optional list of tool schemas in OpenAI function format
        criteria: Optional pre-defined RAG criteria object.
                 If None, will use default criteria.rag

    Returns:
        Dictionary containing evaluation results, or None if evaluation failed

    Example:
        messages = [
            {"role": "user", "content": "What is X?\n\nContext:\n{context}"},
            {"role": "assistant", "content": "Based on the context..."}
        ]
        tools = [{"type": "function", "function": {"name": "tool1", ...}}]

        results = evaluate_rag(messages, tools=tools)
    """
    if not messages or not _composo_client:
        logger.debug("Cannot evaluate RAG: messages empty or Composo client not initialized")
        return None

    try:
        from composo.models import criteria as default_criteria

        # Use provided criteria or default RAG criteria
        if criteria is None:
            criteria = default_criteria.rag

        # Build evaluation call parameters
        eval_params = {
            "messages": messages,
            "criteria": criteria
        }

        # Add tools if provided
        if tools:
            eval_params["tools"] = tools

        # Evaluate using Composo API
        results = _composo_client.evaluate(**eval_params)

        # Format results for easier consumption
        evaluation_output = {
            "evaluation_type": "rag",
            "status": "completed",
            "results": []
        }

        if hasattr(results, '__iter__'):
            for result in results:
                # Extract score and explanation from result object or dict
                score = None
                explanation = None

                if hasattr(result, 'score'):
                    score = result.score
                elif isinstance(result, dict):
                    score = result.get("score")

                if hasattr(result, 'explanation'):
                    explanation = result.explanation
                elif isinstance(result, dict):
                    explanation = result.get("explanation")

                evaluation_output["results"].append({
                    "criteria": str(result.get("criteria", "unknown")) if isinstance(result, dict) else str(result),
                    "score": score,
                    "explanation": explanation,
                    "details": str(result)
                })

        logger.info(f"RAG evaluation completed: {len(evaluation_output['results'])} criteria evaluated")
        return evaluation_output

    except Exception as e:
        logger.error(f"Failed to evaluate RAG: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_current_tracer() -> Optional[Any]:
    """Get the current active tracer from the stack."""
    return _current_tracer_stack[-1] if _current_tracer_stack else None


def has_active_tracer() -> bool:
    """Check if there's an active tracer in the stack."""
    return len(_current_tracer_stack) > 0


def get_tracer_stack_depth() -> int:
    """Get the depth of the tracer stack (for hierarchical tracing)."""
    return len(_current_tracer_stack)
