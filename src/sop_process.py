import os
import sys
import yaml
import json
import re
import argparse
from typing import Dict, Any, Type, TypeVar, List

# --- Local Imports ---
from data_models import SOPGraph, SOPGraphLLMOutput, BaseModel

def clean_generated_fields(sop_graph: SOPGraph) -> None:
    """
    Clean all generated fields from scenarios and partitions, preserving only:
    - stakeholders
    - scenario structure (name, description, stakeholders list)
    - partition structure (name, task, triggering/halting stakeholders, descriptions)

    This allows re-running scenario generation and simulation on a clean graph.
    """
    # Clean scenario-level generated fields
    for scenario in sop_graph.scenarios:
        scenario.scenario_happy_path = []
        scenario.scenario_unhappy_path = []
        scenario.scenario_happy_path_response = []
        scenario.scenario_unhappy_path_response = []
        scenario.evaluation_summary = None

        # Clean partition-level generated fields
        for partition in scenario.partitions:
            partition.happy_path = []
            partition.partition_unhappy_path = []
            partition.unhappy_paths = []
            partition.happy_path_response = []
            partition.partition_unhappy_path_response = []
            partition.unhappy_path_responses = []
            partition.transitions = []
            partition.context = []
            partition.evaluation_summary = None

    # Clean graph-level evaluation
    sop_graph.evaluation_summary = None

    print("[INFO] Cleaned all generated fields (scenarios, responses, evaluations)", file=sys.stderr)

def main():
    # --- Provider Imports ---
    from agno.agent import Agent
    from agno.models.anthropic import Claude
    from agno.models.google import Gemini

    # --- Configuration ---
    from dotenv import load_dotenv
    labryon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); dotenv_path = os.path.join(labryon_root, ".env"); load_dotenv(dotenv_path=dotenv_path, override=True)
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    # --- Constants ---
    SOP_VALIDATION_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'sop_config.yaml')
    SOP_EXAMPLES_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'sop_partition_examples.yaml')

    # --- Pydantic Models for Type Enforcement ---
    T = TypeVar("T", bound=BaseModel)

    # --- Core Functions ---
    def _load_sop_validation_config() -> Dict[str, Any]:
        """Loads the full generator configuration from the YAML file."""
        try:
            with open(SOP_VALIDATION_CONFIG_PATH, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            raise RuntimeError(f"SOP validation config file not found at {SOP_VALIDATION_CONFIG_PATH}.")
        except Exception as e:
            raise RuntimeError(f"Error loading SOP validation config: {e}")

    def _call_llm(prompt: str, config: Dict[str, Any], response_model: Type[T], max_tokens: int = 32657, task_name: str = "scenario_generation") -> T:
        """
        Calls the configured LLM provider using agno, expecting a JSON response that is parsed
        and validated against the provided Pydantic model. Retries on failure.
        """
        provider = config.get("llm_provider", "anthropic")
        provider_temps = config.get(f"{provider}_temperatures", {})
        temperature = provider_temps.get(task_name, 0.1)

        model = None
        if provider == "anthropic":
            model_name = config.get("anthropic_model_name")
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not found in environment for Anthropic provider.")
            # Set timeout to 20 minutes (1200 seconds) for long requests
            model = Claude(id=model_name, temperature=temperature, max_tokens=max_tokens, api_key=api_key, timeout=1200.0)
            print(f"[INFO] Using {provider} model: {model_name} (temperature={temperature}, timeout=1200s)", file=sys.stderr)
        elif provider == "google":
            model_name = config.get("google_model_name")
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError("GOOGLE_API_KEY not found in environment for Google provider.")
            model = Gemini(id=model_name, temperature=temperature, max_output_tokens=max_tokens, api_key=api_key)
            print(f"[INFO] Using {provider} model: {model_name} (temperature={temperature})", file=sys.stderr)
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

        # For Anthropic, enable streaming to handle long requests
        if provider == "anthropic":
            agent = Agent(
                name=f"sop_process_{task_name}",
                model=model,
                system_message="Your response must be a single, valid JSON object. Do not include any other text, explanations, or markdown formatting.",
                output_schema=response_model,
                retries=3,
                delay_between_retries=1,
                exponential_backoff=True,
                stream=True,  # Enable streaming for long Anthropic requests
            )
        else:
            agent = Agent(
                name=f"sop_process_{task_name}",
                model=model,
                system_message="Your response must be a single, valid JSON object. Do not include any other text, explanations, or markdown formatting.",
                output_schema=response_model,
                retries=3,
                delay_between_retries=1,
                exponential_backoff=True,
            )

        try:
            response = agent.run(prompt)

            # When stream=True, consume the iterator
            if provider == "anthropic":
                final_response = None
                for event in response:
                    final_response = event
                if final_response and hasattr(final_response, 'content') and final_response.content is not None:
                    return final_response.content
                else:
                    raise ValueError("No valid content in streamed response")
            else:
                if hasattr(response, 'content') and response.content is not None:
                    return response.content
                else:
                    raise ValueError(f"Unexpected response structure: {type(response)}")
        except Exception as e:
            print(f"    [FAIL] Failed to get a valid response from the LLM after multiple retries. Error: {e}", file=sys.stderr)
            raise

    def _analyze_and_partition_sop(sop: str, config: Dict[str, Any], hints: List[Dict[str, Any]], attempt_num: int) -> SOPGraphLLMOutput:
        """Uses the configured LLM to analyze the SOP and extract the workflow graph."""
        print(f"--- Starting SOP graph analysis attempt {attempt_num}... ---", file=sys.stderr)

        try:
            with open(SOP_EXAMPLES_PATH, 'r') as f:
                examples = yaml.safe_load(f)
            
            examples_str = ""
            for ex in examples:
                examples_str += f"Example Name: {ex['name']}\n"
                examples_str += f"<sop>\n{ex['sop']}\n</sop>\n"
                examples_str += f"Correct Partitions:\n{json.dumps(ex['partitions'], indent=2)}\n\n"
            
        except FileNotFoundError:
            examples_str = "No few-shot examples found."
        except Exception as e:
            examples_str = f"Error loading few-shot examples: {e}"

        analysis_prompt = config.get('graph_analysis_prompt', '').format(
            sop=sop,
            few_shot_examples=examples_str
        )
        try:
            graph_obj = _call_llm(analysis_prompt, config, response_model=SOPGraphLLMOutput, task_name="sop_analysis")
            if not graph_obj.scenarios:
                raise ValueError("LLM analysis did not return any scenarios.")
            print(f"[OK] Attempt {attempt_num} identified {len(graph_obj.stakeholders)} stakeholders and {len(graph_obj.scenarios)} scenarios.", file=sys.stderr)
            return graph_obj
        except Exception as e:
            print(f"Error during SOP graph analysis for attempt {attempt_num}: {e}", file=sys.stderr)
            return SOPGraphLLMOutput(stakeholders=[], scenarios=[])

    parser = argparse.ArgumentParser(description="Analyze an AI-Worker's SOP to extract its workflow graph.")
    parser.add_argument("--sop-graph", required=True, help="Path to the SOP graph snapshot JSON file.")
    args = parser.parse_args()

    # 1. Load Config and SOP Graph
    config = _load_sop_validation_config()
    
    try:
        with open(args.sop_graph, 'r') as f:
            sop_graph_data = json.load(f)
        sop_graph = SOPGraph.model_validate(sop_graph_data)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        sys.exit(f"[Error] Could not load or parse the SOP graph file: {e}")

    if not sop_graph.worker_config or not sop_graph.worker_config.sop:
        sys.exit(f"Could not find a valid SOP in the worker_config of '{args.sop_graph}'.")

    # 2. Clean generated fields before re-analysis
    clean_generated_fields(sop_graph)

    # 3. Perform Graph Analysis
    print(f"\n--- Analyzing SOP for '{sop_graph.worker_name}' to build workflow graph... ---", file=sys.stderr)
    llm_output = _analyze_and_partition_sop(sop_graph.worker_config.sop, config, [], 1)

    if not llm_output.scenarios:
        sys.exit("[Error] Could not extract any scenarios from the SOP. Aborting.")

    # 4. Enrich the SOPGraph object
    sop_graph.stakeholders = llm_output.stakeholders
    sop_graph.scenarios = llm_output.scenarios
    sop_graph.ambiguities = llm_output.ambiguities

    # 5. Output final JSON by overwriting the input file
    try:
        with open(args.sop_graph, 'w') as f:
            f.write(sop_graph.model_dump_json(indent=2))
        print(f"\n[SUCCESS] Enriched SOP graph and saved to {args.sop_graph}", file=sys.stderr)
    except Exception as e:
        print(f"\n[ERROR] Failed to save output to {args.sop_graph}: {e}", file=sys.stderr)
    
    # Also print to stdout for immediate feedback or redirection
    print(sop_graph.model_dump_json(indent=2))

if __name__ == '__main__':
    main()
