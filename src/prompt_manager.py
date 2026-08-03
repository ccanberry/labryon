"""
Prompt management with Langfuse and local fallbacks.
Replicates the backend prompt_manager for use in labryon testing framework.
"""

import os
from typing import Dict, Any, Optional
from jinja2 import Template, exceptions

# Try to import Langfuse
LANGFUSE_AVAILABLE = False
try:
    from langfuse import Langfuse
    LANGFUSE_AVAILABLE = True
except ImportError:
    Langfuse = None


def get_fallback_prompt(prompt_name: str) -> Optional[str]:
    """Get fallback prompt template from local definitions."""
    from prompt_templates import get_fallback_templates

    fallback_prompts = get_fallback_templates()

    # Map prompt names to fallback template indices
    prompt_map = {
        "capability_classification_prompt": 0,
        "capability_description_generation_prompt": 1,
        "team_system_message": 2,
    }

    if prompt_name in prompt_map:
        idx = prompt_map[prompt_name]
        if idx < len(fallback_prompts):
            return fallback_prompts[idx]["template"]

    # Generic fallbacks for other prompts
    if prompt_name == "supervisor_agent":
        return "You are a supervisor agent. Coordinate and delegate tasks to your team of agents based on the workflow requirements. Use the available tools and context to make informed decisions about task delegation."

    if prompt_name == "atomic_agent":
        return "You are an atomic agent specialized in handling specific tasks. Execute your assigned task efficiently using the available tools and context provided by your supervisor."

    return None


def substitute_variables(template: str, variables: Dict[str, Any]) -> str:
    """Substitute variables using Jinja2."""
    try:
        jinja_template = Template(template)
        return jinja_template.render(variables)
    except exceptions.TemplateSyntaxError as e:
        print(f"Jinja2 template syntax error: {e}")
        return f"Template syntax error for: {template[:100]}"
    except Exception as e:
        print(f"Jinja2 rendering failed: {e}")
        return template


class PromptManager:
    """
    Manages prompts by fetching them from Langfuse with a local fallback.
    Replicates the backend's AdvancedPromptManager behavior.
    """

    def __init__(self):
        self.langfuse_client = None
        if LANGFUSE_AVAILABLE:
            # Initialize Langfuse client if credentials are available
            public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
            secret_key = os.getenv("LANGFUSE_SECRET_KEY")
            host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

            if public_key and secret_key:
                try:
                    self.langfuse_client = Langfuse(
                        public_key=public_key,
                        secret_key=secret_key,
                        host=host
                    )
                    print(f"✓ Langfuse client initialized (host: {host})")
                except Exception as e:
                    print(f"Warning: Failed to initialize Langfuse: {e}")

    def get_prompt(
        self,
        prompt_name: str,
        variables: Optional[Dict[str, Any]] = None,
        version: Optional[int] = None,
        tenant_id: Optional[str] = None
    ) -> str:
        """
        Get prompt with Langfuse -> Local fallback.

        Args:
            prompt_name: Name of the prompt
            variables: Variables to substitute
            version: Specific version (latest if None)
            tenant_id: Tenant isolation support (for analytics only)
        """
        variables_dict = variables or {}

        # Step 1: Try Langfuse first
        langfuse_template = self._get_from_langfuse(prompt_name, version)
        if langfuse_template:
            print(f"✓ Retrieved '{prompt_name}' from Langfuse")
            return substitute_variables(langfuse_template, variables_dict)

        # Step 2: Local fallback if Langfuse fails
        print(f"⚠ Using local fallback for '{prompt_name}'")
        fallback_template = get_fallback_prompt(prompt_name)

        if fallback_template:
            return substitute_variables(fallback_template, variables_dict)

        # Step 3: Emergency fallback
        print(f"✗ No fallback found for '{prompt_name}', using emergency fallback")
        return self._get_emergency_fallback(variables_dict)

    def _get_from_langfuse(self, name: str, version: Optional[int] = None) -> Optional[str]:
        """Get prompt from Langfuse with version support."""
        if not self.langfuse_client:
            return None

        try:
            # The get_prompt method fetches the latest version by default if version is None
            prompt = self.langfuse_client.get_prompt(name, version=version)
            if hasattr(prompt, 'prompt'):
                return prompt.prompt
            elif hasattr(prompt, 'template'):
                return prompt.template
            else:
                return str(prompt)
        except Exception as e:
            print(f"Langfuse lookup failed for '{name}': {e}")
            return None

    def _get_emergency_fallback(self, variables: Dict[str, Any]) -> str:
        """Emergency fallback when all else fails."""
        return f"System prompt unavailable. Custom instructions: {variables.get('custom_instructions', 'None provided')}"


# Global instance
prompt_manager = PromptManager()
