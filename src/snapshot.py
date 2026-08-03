"""
Creates a snapshot of an AI-Worker's configuration from the database.

This script captures the initial worker configuration (SOP, agents, tools, knowledge bases)
and saves it as graph_output.json for further processing by the testing pipeline.
"""

import os
import sys
import re
import yaml
import argparse
import importlib
from datetime import datetime

from data_models import SOPGraph
from data_loader import get_worker_by_name, get_available_workers

def _get_hardcoded_impargo_prompt(project_root: str) -> str:
    """Loads default ImpargoExpert prompt from agent metadata registry."""
    try:
        from agent_metadata import get_agent_metadata
        metadata = get_agent_metadata("impargo-expert")
        if metadata and "prompt" in metadata:
            return metadata["prompt"]
    except Exception as e:
        print(f"[WARNING] Could not load Impargo prompt from metadata. Error: {e}", file=sys.stderr)
    return ""


def _sanitize_for_path(name: str) -> str:
    """Sanitizes a string to be a valid directory or file name."""
    name = name.replace(' ', '_')
    return re.sub(r'[^a-zA-Z0-9_.-]', '', name)


def main():
    """Main entry point for snapshot script."""
    from dotenv import load_dotenv

    # Load environment variables (checks labryon/.env first, then project root)
    labryon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); dotenv_path = os.path.join(labryon_root, ".env"); load_dotenv(dotenv_path=dotenv_path, override=True)
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    sop_validation_config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'sop_config.yaml')
    mock_tool_config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'mock_tool_config.yaml')

    parser = argparse.ArgumentParser(description="Create a snapshot of an AI-Worker's configuration.")
    parser.add_argument("--worker", type=str, help="Name of the AI-Worker. If omitted, a list will be shown.")
    parser.add_argument("--override", action="store_true", help="Override the latest snapshot, skipping the timestamp folder.")
    args = parser.parse_args()

    # Load configs
    with open(sop_validation_config_path, 'r') as f:
        config = yaml.safe_load(f)
    worker_name = args.worker or config.get("worker_name")

    try:
        with open(mock_tool_config_path, 'r') as f:
            mock_tool_config = yaml.safe_load(f)
        hardcoded_tools_mapping = mock_tool_config.get('HARDCODED_SUBAGENT_TOOLS', {})
    except (FileNotFoundError, yaml.YAMLError) as e:
        sys.exit(f"[Error] Could not load or parse mock_tool_config.yaml: {e}")

    if not worker_name:
        workers = get_available_workers()
        if not workers:
            sys.exit("[Error] No workers found in database.")
        print("\n--- Available Workers ---", file=sys.stderr)
        for idx, (name, _) in enumerate(workers):
            print(f"[{idx}] {name}", file=sys.stderr)
        print("------------------------", file=sys.stderr)
        try:
            worker_idx = int(input("\nEnter worker index: ").strip())
            worker_name = workers[worker_idx][0]
        except (ValueError, IndexError):
            sys.exit("[Error] Invalid selection.")

    # Fetch worker config
    worker_config = get_worker_by_name(worker_name)
    if not (worker_config and worker_config.get('sop')):
        sys.exit(f"Could not fetch a valid SOP for worker '{worker_name}'.")

    # Inject hardcoded tool bindings, agent_id, and default prompts for sub-agents
    if 'agents' in worker_config and worker_config['agents']:
        for agent in worker_config['agents']:
            agent_name = agent.get('name')

            if not agent.get('agent_id'):
                agent['agent_id'] = agent_name
                print(f"[INFO] Set agent_id='{agent_name}' for agent: {agent_name}", file=sys.stderr)

            if agent_name in hardcoded_tools_mapping:
                print(f"[INFO] Injecting hardcoded tools for sub-agent: {agent_name}", file=sys.stderr)
                provider_name = agent_name

                if 'tool_bindings' not in agent or not isinstance(agent['tool_bindings'], dict):
                    agent['tool_bindings'] = {}

                if provider_name not in agent['tool_bindings']:
                    agent['tool_bindings'][provider_name] = {'operations': []}

                for tool_name in hardcoded_tools_mapping[agent_name]:
                    if tool_name not in agent['tool_bindings'][provider_name]['operations']:
                        agent['tool_bindings'][provider_name]['operations'].append(tool_name)

            if agent_name == "impargo-expert" and not agent.get("prompt"):
                print(f"[INFO] Database prompt for '{agent_name}' is empty. Injecting hardcoded default prompt.", file=sys.stderr)
                agent["prompt"] = _get_hardcoded_impargo_prompt(project_root)

    # Create initial SOPGraph object
    system_prompt = worker_config.pop('system_prompt', None)
    sop_graph = SOPGraph(
        worker_name=worker_name,
        system_prompt=system_prompt,
        worker_config=worker_config,
        stakeholders=[],
        scenarios=[]
    )

    # Output final JSON
    safe_worker_name = _sanitize_for_path(worker_name)

    # Get labryon root directory (parent of config directory)
    labryon_root = os.path.dirname(os.path.dirname(sop_validation_config_path))

    if args.override:
        output_dir = os.path.join(labryon_root, 'sop', safe_worker_name)
    else:
        batch_timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        output_dir = os.path.join(labryon_root, 'sop', safe_worker_name, batch_timestamp)

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "graph_output.json")
    
    try:
        with open(output_path, 'w') as f:
            f.write(sop_graph.model_dump_json(indent=2))
        print(f"\n[SUCCESS] SOP snapshot saved to {output_path}", file=sys.stderr)
    except Exception as e:
        print(f"\n[ERROR] Failed to save output to {output_path}: {e}", file=sys.stderr)
    
    # Also print to stdout for immediate feedback or redirection
    print(sop_graph.model_dump_json(indent=2))

if __name__ == '__main__':
    main()
