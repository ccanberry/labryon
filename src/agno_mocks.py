"""
Standalone agno agent and team implementations for testing without backend dependencies.

This module provides mock implementations that use agno directly without importing
from the backend codebase.
"""

import os
import yaml
from typing import List, Dict, Any
from agno.agent import Agent
from agno.team import Team
from agno.models.anthropic import Claude
from agno.models.openai import OpenAIChat
from agno.models.google import Gemini

# --- Configuration ---
from dotenv import load_dotenv
labryon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
dotenv_path = os.path.join(labryon_root, ".env")
load_dotenv(dotenv_path=dotenv_path, override=True)


def _load_model_config():
    """Load model configuration from evaluation_criteria.yaml"""
    try:
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'evaluation_criteria.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
            provider = config.get('llm_provider', 'anthropic')
            anthropic_model = config.get('anthropic_model', 'claude-sonnet-4-20250514')
            openai_model = config.get('openai_model', 'gpt-4o')
            google_model = config.get('google_model', 'gemini-2.5-pro')
            return provider, anthropic_model, openai_model, google_model
    except Exception:
        return 'anthropic', 'claude-sonnet-4-20250514', 'gpt-4o', 'gemini-2.5-pro'


def _create_model(provider=None, anthropic_model=None, openai_model=None, google_model=None):
    """Create appropriate model based on provider"""
    if provider is None:
        provider, anthropic_model, openai_model, google_model = _load_model_config()

    if provider == 'openai':
        return OpenAIChat(id=openai_model)
    elif provider == 'google':
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable is not set.")
        # Add timeout to prevent hanging
        return Gemini(id=google_model, api_key=api_key)
    else:  # default to anthropic
        return Claude(id=anthropic_model)


class MockAtomicAgent(Agent):
    """Standalone agent for testing without backend imports."""

    def __init__(self, agent_name: str, agent_prompt: str, agent_description: str, mock_tools: List):
        self.agent_prompt = agent_prompt
        self.mock_tools = mock_tools
        # Agents in a team inherit the model from the team, not configured individually
        super().__init__(
            name=agent_name,
            role=agent_description or f"{agent_name} agent",
            instructions=agent_prompt or "You are a helpful agent.",
            tools=mock_tools
        )


class MockSupervisor:
    """Simple wrapper around agno Team with .team property for compatibility."""

    def __init__(self, team: Team):
        self._team = team

    @property
    def team(self):
        return self._team


def create_mock_team(
    agent_configs: List[Dict[str, Any]],
    supervisor_tools: List,
    system_prompt: str,
    worker_name: str,
    tool_recorder,
    mock_tool_cache: Dict[str, Any],
    tool_contexts: Dict[str, Any],
    create_mock_tools_fn
) -> MockSupervisor:
    """
    Create a standalone agno Team with mock agents and tools.

    Args:
        agent_configs: List of agent configurations from worker_config
        supervisor_tools: List of supervisor-level mock tools
        system_prompt: System-level instructions for the team
        worker_name: Name of the worker
        tool_recorder: ToolCallRecorder instance
        mock_tool_cache: Cache for mock tools
        tool_contexts: Context data for mock tools (e.g., knowledge base)
        create_mock_tools_fn: Function to create mock tools from config

    Returns:
        MockSupervisor wrapping the agno Team
    """
    # Create atomic agents
    atomic_agents = []
    for agent_dict in agent_configs:
        agent_name = agent_dict.get('name', 'unknown_agent')
        agent_description = agent_dict.get('description', '')
        agent_prompt = agent_dict.get('prompt', '')

        tools = create_mock_tools_fn(
            agent_dict.get('tool_bindings', {}),
            tool_recorder,
            mock_tool_cache,
            tool_contexts,
            agent_name=agent_name
        )

        agent = MockAtomicAgent(agent_name, agent_prompt, agent_description, tools)
        atomic_agents.append(agent)

    # Create team with configured model
    team = Team(
        members=atomic_agents,
        name=worker_name or "Test Worker",
        model=_create_model(),
        tools=supervisor_tools,
        instructions=system_prompt,
        show_members_responses=True,
        markdown=True,
    )

    return MockSupervisor(team)