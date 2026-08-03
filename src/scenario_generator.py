import os
import yaml
import json
import argparse
import sys
import multiprocessing
import random
from typing import List, Dict, Any, Type, TypeVar, Optional, Tuple
from pydantic import BaseModel, ValidationError
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Provider Imports ---
from agno.agent import Agent
from agno.models.anthropic import Claude
from agno.models.google import Gemini

# --- Configuration ---
from dotenv import load_dotenv
labryon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); dotenv_path = os.path.join(labryon_root, ".env"); load_dotenv(dotenv_path=dotenv_path, override=True)

# --- Local Imports ---
from data_models import SOPGraph, Scenario, Partition, UnhappyPath, Transition, Stakeholder
from mock_tools import ALL_TOOLS_SCHEMA
from pydantic import BaseModel, Field

class EnrichedScenarios(BaseModel):
    enriched_scenarios: List[str] = Field(description="The list of scenario strings with added metadata.")

# --- Constants ---
GENERATOR_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'llm_generator_config.yaml')
PARTITION_CONFIG_FILENAME = "partition_configs.yaml"
TASK_SCENARIO_GENERATION = "scenario_generation"
TASK_CREATIVE_GENERATION = "creative_generation"
TASK_FORMATTING = "formatting"

# --- Pydantic Models for LLM Responses ---
T = TypeVar("T", bound=BaseModel)

class PartitionHappyPath(BaseModel):
    model_config = {"extra": "forbid"}
    partition_name: str
    scenario: List[str]

class MasterHappyPath(BaseModel):
    model_config = {"extra": "forbid"}
    description: str
    scenario: List[str]

class StructuredMasterHappyPath(BaseModel):
    model_config = {"extra": "forbid"}
    partition_happy_paths: List[PartitionHappyPath]
    master_happy_path: MasterHappyPath

class UnhappyPathsBatch(BaseModel):
    model_config = {"extra": "forbid"}
    unhappy_paths: List[UnhappyPath]

class UnhappyPathsAndMaster(BaseModel):
    model_config = {"extra": "forbid"}
    unhappy_paths: List[UnhappyPath]
    partition_unhappy_path: UnhappyPath

# --- Core Functions ---

def _clear_existing_results(sop_graph: SOPGraph) -> SOPGraph:
    """Clears all simulation and evaluation results from the SOPGraph."""
    sop_graph.evaluation_summary = None

    for scenario in sop_graph.scenarios:
        scenario.evaluation_summary = None
        scenario.scenario_happy_path_response = []
        scenario.scenario_unhappy_path_response = []
        for partition in scenario.partitions:
            partition.evaluation_summary = None
            partition.happy_path_response = []
            partition.partition_unhappy_path_response = []
            partition.unhappy_path_responses = []
    return sop_graph

def _load_generator_config() -> Dict[str, Any]:
    try:
        with open(GENERATOR_CONFIG_PATH, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        raise RuntimeError(f"Generator config file not found at {GENERATOR_CONFIG_PATH}.")
    except Exception as e:
        raise RuntimeError(f"Error loading generator config: {e}")

def _load_partition_configs(sop_graph_dir: str) -> Tuple[Dict[str, Any], bool, List[List[Dict[str, Any]]]]:
    """Loads index-based partition configurations from the SOP graph's directory."""
    config_path = os.path.join(sop_graph_dir, PARTITION_CONFIG_FILENAME)
    defaults = {'limit_variations': 3}
    redirectors_enabled = True
    scenario_configs = []
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
            defaults = config.get('defaults', defaults)
            redirectors_enabled = config.get('redirectors_enabled', redirectors_enabled)
            scenario_configs = config.get('scenario_configs', scenario_configs)
            print(f"[INFO] Loaded partition configs from {config_path}.")
    except FileNotFoundError:
        print(f"[INFO] Partition config file not found at {config_path}. Using defaults for all scenarios.", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] Error loading partition config from {config_path}: {e}. Using defaults.", file=sys.stderr)
        
    return defaults, redirectors_enabled, scenario_configs

def _get_model(config: Dict[str, Any], task_name: str, max_tokens: int = 8192):
    provider = config.get("llm_provider", "anthropic")
    temperature = config.get(f"{provider}_temperatures", {}).get(task_name, 0.1)
    api_key_name = {"anthropic": "ANTHROPIC_API_KEY", "google": "GOOGLE_API_KEY"}.get(provider)
    api_key = os.getenv(api_key_name)

    if not api_key: raise ValueError(f"{api_key_name} not found in environment.")

    if provider == "anthropic":
        model_name = config.get("anthropic_model_name")
        print(f"[INFO] Using {provider} model: {model_name} (temperature={temperature}, task={task_name})", file=sys.stderr)
        return Claude(id=model_name, temperature=temperature, max_tokens=max_tokens, api_key=api_key)
    elif provider == "google":
        model_name = config.get("google_model_name")
        print(f"[INFO] Using {provider} model: {model_name} (temperature={temperature}, task={task_name})", file=sys.stderr)
        return Gemini(id=model_name, temperature=temperature, max_output_tokens=max_tokens, api_key=api_key)
    raise ValueError(f"Unsupported LLM provider: {provider}")

def _call_llm_for_text(prompt: str, config: Dict[str, Any], max_tokens: int = 8192, task_name: str = TASK_CREATIVE_GENERATION) -> str:
    model = _get_model(config, task_name, max_tokens)
    agent = Agent(name=f"sop_creative_{task_name}", model=model)
    try:
        response = agent.run(prompt)
        return str(response.content) if hasattr(response, 'content') else str(response)
    except Exception as e:
        print(f"    [FAIL] Text generation LLM call failed. Error: {e}", file=sys.stderr)
        return ""

def _call_llm(prompt: str, config: Dict[str, Any], response_model: Type[T], max_tokens: int = 8192, task_name: str = TASK_SCENARIO_GENERATION) -> T:
    model = _get_model(config, task_name, max_tokens)
    agent = Agent(
        name=f"sop_json_{task_name}", model=model,
        system_message="Your response must be a single, valid JSON object.",
        output_schema=response_model, retries=3, delay_between_retries=1, exponential_backoff=True,
    )
    try:
        response = agent.run(prompt)
        if hasattr(response, 'content') and response.content is not None:
            return response.content
        raise ValueError(f"Unexpected response structure: {type(response)}")
    except Exception as e:
        print(f"    [FAIL] JSON generation LLM call failed after retries. Error: {e}", file=sys.stderr)
        raise

def _get_all_communication_tool_definitions(config: Dict[str, Any]) -> str:
    """
    Extracts schemas for ALL external communication tools to allow the LLM to infer the correct one.
    """
    external_tools = set(config.get('external_communication_tools', []))
    schemas = []
    
    for provider, provider_data in ALL_TOOLS_SCHEMA.items():
        if 'operations' in provider_data:
            for tool_name, tool_def in provider_data['operations'].items():
                # If filtered list is provided, use it; otherwise provide all (or just comms tools)
                if not external_tools or tool_name in external_tools:
                    schemas.append(f"Tool: {tool_name}\nUsage: {tool_def.get('usage', 'N/A')}\nDescription: {tool_def.get('description', 'N/A')}")
    
    if not schemas:
        return "No external communication tool definitions found."
        
    return "\n\n".join(schemas)

def _enrich_scenario_with_metadata(scenario: List[str], config: Dict[str, Any]) -> List[str]:
    """
    Applies a dedicated LLM step to enrich the scenario messages with required metadata (e.g., message_id).
    """
    enrichment_prompt = config.get('metadata_enrichment_prompt')
    if not enrichment_prompt:
        print("    [WARN] No metadata_enrichment_prompt found. Skipping enrichment.")
        return scenario

    print("    [INFO] Enriching scenario with metadata...")
    tool_definitions = _get_all_communication_tool_definitions(config)
    prompt = enrichment_prompt.format(
        scenarios_json=json.dumps(scenario, indent=2),
        tool_definitions=tool_definitions
    )
    
    try:
        result = _call_llm(prompt, config, EnrichedScenarios, task_name=TASK_SCENARIO_GENERATION)
        return result.enriched_scenarios
    except Exception as e:
        print(f"    [WARN] Metadata enrichment failed: {e}. Returning original scenario.")
        return scenario

def _generate_master_happy_path(sop: str, config: Dict[str, Any], partitions: List[Partition], stakeholders: str) -> StructuredMasterHappyPath:
    print("\n" + "="*50 + "\n--- Generating Master Happy Path ---\n" + "="*50)
    prompt = config.get('master_happy_path_generation_prompt', '').format(
        sop=sop,
        partitions_json=json.dumps([p.model_dump(by_alias=True) for p in partitions], indent=2),
        stakeholders=stakeholders
    )
    happy_path = _call_llm(prompt, config, StructuredMasterHappyPath, task_name=TASK_SCENARIO_GENERATION)
    
    # Enrich the master path
    happy_path.master_happy_path.scenario = _enrich_scenario_with_metadata(happy_path.master_happy_path.scenario, config)
    
    # Enrich each partition path
    for pp in happy_path.partition_happy_paths:
        pp.scenario = _enrich_scenario_with_metadata(pp.scenario, config)
        
    print(f"    [OK] Generated structured master happy path.")
    return happy_path

def _generate_scenario_variations(
    sop: str, config: Dict[str, Any], partition: Partition, stakeholders: str,
    limit_variations: Optional[int], redirector_examples: List[str],
    master_happy_path: List[str], all_partitions: List[Partition], partition_happy_path: List[str]
) -> Partition:
    print(f"  - Generating variations for '{partition.partition_name}'...")

    # Clear existing unhappy paths before regenerating
    partition.unhappy_paths = []
    partition.partition_unhappy_path = []

    partition_dict = partition.model_dump(by_alias=True)

    # Step 1: Brainstorm new creative ideas, if requested
    brainstormed_ideas = []
    if limit_variations is not None and limit_variations > 0:
        num_creative = limit_variations
        creative_prompt = config.get('creative_variation_prompt', '').format(
            generation_mode_instruction=f"a plain text, bulleted list of exactly {num_creative} distinct ideas for unhappy paths.",
            partition_happy_path=json.dumps(partition_happy_path, indent=2),
            sub_sop=partition.task, **partition_dict
        )
        creative_text = _call_llm_for_text(creative_prompt, config, task_name=TASK_CREATIVE_GENERATION)
        brainstormed_ideas = [line.strip('*-• ').strip() for line in creative_text.split('\n') if line.strip()]
        
        if len(brainstormed_ideas) > num_creative:
            brainstormed_ideas = brainstormed_ideas[:num_creative]
            print(f"    [INFO] Enforced limit: trimmed brainstormed ideas to {num_creative}.")
        print(f"    [INFO] Brainstormed {len(brainstormed_ideas)} new ideas.")

    # Step 2: Combine redirectors and brainstormed ideas into a single list
    all_ideas_to_format = redirector_examples + brainstormed_ideas
    if not all_ideas_to_format:
        print("    [INFO] No redirectors or brainstormed ideas to format. Skipping.")
        return partition

    print(f"    [INFO] Formatting a total of {len(all_ideas_to_format)} ideas.")

    # Step 3: Format all ideas in a single call
    formatting_prompt = config.get('formatting_prompt', '')
    batch_prompt = formatting_prompt.format(
        all_ideas_to_format="\n".join([f"- {ex}" for ex in all_ideas_to_format]),
        sop=sop, stakeholders=stakeholders, master_happy_path_json=json.dumps(master_happy_path, indent=2),
        partitions_json=json.dumps([p.model_dump(by_alias=True) for p in all_partitions], indent=2),
        happy_path_context_json=json.dumps(master_happy_path, indent=2),
        **partition_dict
    )
    try:
        result = _call_llm(batch_prompt, config, UnhappyPathsAndMaster, task_name=TASK_FORMATTING)
        
        # Enrich all generated unhappy paths
        for up in result.unhappy_paths:
            up.path = _enrich_scenario_with_metadata(up.path, config)
            
        if result.partition_unhappy_path:
            result.partition_unhappy_path.path = _enrich_scenario_with_metadata(result.partition_unhappy_path.path, config)
            
        partition.unhappy_paths = result.unhappy_paths
        if result.partition_unhappy_path and result.partition_unhappy_path.path:
            partition.partition_unhappy_path = result.partition_unhappy_path.path
        print(f"    [OK] Generated {len(partition.unhappy_paths)} unhappy paths and one master scenario.")
    except Exception as e:
        print(f"    [FAIL] Could not format unhappy paths: {e}", file=sys.stderr)
    
    return partition

def _generate_coherent_end_to_end_unhappy_path(
    config: Dict[str, Any],
    master_happy_path: List[str],
    partition_unhappy_paths: List[List[str]]
) -> List[str]:
    """
    Takes disjointed partition unhappy paths and uses an LLM to stitch them into a single, coherent story.
    """
    print("\n" + "="*50 + "\n--- Generating Coherent End-to-End Unhappy Path ---\n" + "="*50)
    prompt = config.get('end_to_end_unhappy_path_prompt', '').format(
        master_happy_path_json=json.dumps(master_happy_path, indent=2),
        partition_unhappy_paths_json=json.dumps(partition_unhappy_paths, indent=2)
    )
    try:
        result = _call_llm(prompt, config, UnhappyPath, task_name=TASK_SCENARIO_GENERATION)
        result.path = _enrich_scenario_with_metadata(result.path, config)
        print("    [OK] Generated coherent end-to-end unhappy path.")
        return result.path
    except Exception as e:
        print(f"    [FAIL] Could not generate coherent end-to-end unhappy path: {e}", file=sys.stderr)
        return []

def process_scenario(scenario: Scenario, sop: str, generator_config: Dict[str, Any], stakeholder_list_str: str, partition_configs: List[Dict[str, Any]], defaults: Dict[str, Any], redirectors_enabled: bool, partition_workers: int = 1) -> Scenario:
    print(f"\n--- Generating tests for Scenario: {scenario.scenario_name} ---")
    
    # Always generate the master happy path
    structured_master_happy_path = _generate_master_happy_path(sop, generator_config, scenario.partitions, stakeholder_list_str)
    happy_path_lookup = {hp.partition_name: hp.scenario for hp in structured_master_happy_path.partition_happy_paths}
    master_happy_path_conversation = structured_master_happy_path.master_happy_path.scenario
    
    # Set happy paths for all partitions
    for partition in scenario.partitions:
        partition.happy_path = happy_path_lookup.get(partition.partition_name, [])

    # Always generate unhappy paths
    updated_partitions = []
    with ThreadPoolExecutor(max_workers=partition_workers) as executor:
        futures = {}
        # Use enumerate to get the index of each partition being processed
        for i, partition in enumerate(p for p in scenario.partitions if p.happy_path):
            # Start with the global default limit_variations
            limit_variations = defaults.get('limit_variations', 1)
            redirector_examples = []

            # Get partition-specific config or use defaults if index is out of bounds
            if i < len(partition_configs) and partition_configs[i]:
                config = partition_configs[i]
                # Override default if specified in this partition's config
                limit_variations = config.get('limit_variations', limit_variations)
                # Only use redirector examples if they are globally enabled
                if redirectors_enabled:
                    redirector_examples = config.get('redirector_examples', [])
            
            future = executor.submit(
                _generate_scenario_variations,
                sop, generator_config, partition, stakeholder_list_str,
                limit_variations, redirector_examples, master_happy_path_conversation,
                scenario.partitions, partition.happy_path
            )
            futures[future] = partition

        for future in as_completed(futures):
            try:
                updated_partition = future.result()
                updated_partitions.append(updated_partition)
            except Exception as exc:
                original_partition = futures[future]
                print(f"   [ERROR] Partition '{original_partition.partition_name}' failed: {exc}", file=sys.stderr)

    skipped_partitions = [p for p in scenario.partitions if not p.happy_path]
    scenario.partitions = sorted(updated_partitions + skipped_partitions, key=lambda p: [part.partition_name for part in scenario.partitions].index(p.partition_name))

    # Always generate transitions and context
    print(f"\n  - Generating transitions and context for '{scenario.scenario_name}'...")
    for i, target_partition in enumerate(scenario.partitions):
        target_partition.transitions = []
        target_partition.context = []
        for j, p in enumerate(scenario.partitions):
            if j < i and p.expected_agent_output:
                target_partition.context.append(f"Previous step completed ({p.partition_name}): {p.expected_agent_output}")

        for j, source_partition in enumerate(scenario.partitions):
            if j < i and source_partition.happy_path:
                target_partition.transitions.append(Transition(
                    source_partition_index=j,
                    source_partition_name=source_partition.partition_name,
                    conversation=source_partition.happy_path,
                    response=None
                ))

    # Populate scenario-level happy path and unhappy path
    scenario.scenario_happy_path = master_happy_path_conversation

    all_partition_unhappy_paths = [p.partition_unhappy_path for p in scenario.partitions if p.partition_unhappy_path]
    if all_partition_unhappy_paths and master_happy_path_conversation:
        scenario.scenario_unhappy_path = _generate_coherent_end_to_end_unhappy_path(
            generator_config,
            master_happy_path_conversation,
            all_partition_unhappy_paths
        )
    else:
        scenario.scenario_unhappy_path = [msg for p in scenario.partitions for msg in p.partition_unhappy_path]

    return scenario

def run_scenario_generation(sop_graph_path: str, parallel_workers: int = 1, generator_config: Optional[Dict[str, Any]] = None):
    """
    Main function to run the scenario generation process.
    Can be called from other modules.
    """
    try:
        with open(sop_graph_path, 'r', encoding='utf-8') as f:
            sop_graph = SOPGraph.model_validate(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, ValidationError) as e:
        raise RuntimeError(f"Failed to load SOP graph '{sop_graph_path}': {e}")

    # Clear existing results before generation
    sop_graph = _clear_existing_results(sop_graph)

    # Use provided config or load from file
    if generator_config is None:
        generator_config = _load_generator_config()
    
    sop_graph_dir = os.path.dirname(sop_graph_path)
    defaults, redirectors_enabled, scenario_configs = _load_partition_configs(sop_graph_dir)

    if not sop_graph.worker_config or not sop_graph.worker_config.sop:
        raise RuntimeError(f"SOP text not found in the worker_config of the SOP graph file: {sop_graph_path}")

    if not sop_graph.scenarios:
        print("[INFO] No scenarios found in the SOP graph. Exiting.")
        return

    stakeholder_list_str = ", ".join([s.name for s in sop_graph.stakeholders])

    # Intelligent worker allocation
    num_scenarios = len(sop_graph.scenarios)
    max_partitions_per_scenario = max(len(s.partitions) for s in sop_graph.scenarios) if sop_graph.scenarios else 1

    if parallel_workers == 1:
        scenario_workers, partition_workers_per_scenario = 1, 1
    elif num_scenarios == 1:
        scenario_workers, partition_workers_per_scenario = 1, parallel_workers
    elif max_partitions_per_scenario == 1:
        scenario_workers, partition_workers_per_scenario = parallel_workers, 1
    else:
        scenario_workers = min(num_scenarios, max(1, int(parallel_workers ** 0.5)))
        partition_workers_per_scenario = max(1, parallel_workers // scenario_workers)

    print(f"\n[INFO] Parallelism: {scenario_workers} scenario workers × {partition_workers_per_scenario} partition workers = {scenario_workers * partition_workers_per_scenario} effective workers\n")

    updated_scenarios = []
    failed_scenarios = []

    with ThreadPoolExecutor(max_workers=scenario_workers) as executor:
        future_to_scenario = {}
        for i, s in enumerate(sop_graph.scenarios):
            # Get partition configs by scenario index.
            partition_configs = scenario_configs[i] if i < len(scenario_configs) else []
            
            future_to_scenario[executor.submit(process_scenario, s, sop_graph.worker_config.sop, generator_config, stakeholder_list_str, partition_configs, defaults, redirectors_enabled, partition_workers_per_scenario)] = s

        for future in as_completed(future_to_scenario):
            scenario = future_to_scenario[future]
            try:
                updated_scenario = future.result()
                updated_scenarios.append(updated_scenario)
            except Exception as exc:
                print(f"   [ERROR] Scenario '{scenario.scenario_name}' failed: {exc}", file=sys.stderr)
                failed_scenarios.append(scenario.scenario_name)

    if failed_scenarios:
        print(f"\n[WARN] {len(failed_scenarios)} scenario(s) failed to process: {', '.join(failed_scenarios)}", file=sys.stderr)

    if not updated_scenarios:
        raise RuntimeError("All scenarios failed to process.")

    sop_graph.scenarios = updated_scenarios

    # Always override the input file
    output_path = sop_graph_path
    
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(sop_graph.model_dump_json(indent=2, by_alias=True))
        print(f"\n[SUCCESS] Enriched SOP graph saved to {output_path}")
    except Exception as e:
        raise IOError(f"Failed to save output: {e}")

# --- Main Execution ---
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Generate a test suite for an AI-Worker from an SOP graph file.")
    parser.add_argument("--sop-graph", required=True, help="Path to the SOP graph output JSON file.")
    parser.add_argument('--parallel', type=int, nargs='?', const=multiprocessing.cpu_count(), default=1, help='Run scenario generation in parallel.')
    
    args = parser.parse_args()

    try:
        # When run from terminal, generator_config is None and will be loaded from the file
        run_scenario_generation(sop_graph_path=args.sop_graph, parallel_workers=args.parallel)
    except (RuntimeError, IOError) as e:
        sys.exit(f"\n[ERROR] {e}")

