import random
import yaml
import os
import json
import hashlib
import copy
from typing import List, Dict, Any, Callable, Optional, Sequence, Union
from tenacity import retry, stop_after_attempt, wait_exponential
import logging

logger = logging.getLogger(__name__)

# --- Configuration ---
from dotenv import load_dotenv
labryon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
dotenv_path = os.path.join(labryon_root, ".env")
load_dotenv(dotenv_path=dotenv_path, override=True)

# --- Single Source of Truth for All Available Tools ---
# Standard definitions for mock tool schemas
ALL_TOOLS_SCHEMA = {
    "outlook": {
        "operations": {
            "send_email": {
                "description": "Send an email to recipients with subject and body content. You MUST provide category parameter - choose one: AI_In_Progress, AI_Done, or AI_Needs_Human to mark email status for team transparency. Supports sending on behalf of shared mailboxes and saving as drafts.",
                "usage": "send_email(to_emails: List[str], subject: str, body: str, category: str, cc_emails: Optional[List[str]] = None, session_state: Optional[Dict[str, Any]] = None, attachment_ids: Optional[List[str]] = None, files: Optional[Sequence[File]] = None, send_on_behalf_of_address: Optional[str] = None, save_as_draft: bool = False) -> Dict[str, Union[str, List[str], bool]]"
            },
            "reply_to_email": {
                "description": "Reply to a specific email message using its message ID. Category defaults to AI_Done but you can specify AI_In_Progress or AI_Needs_Human instead. Category is REQUIRED for team transparency. Supports replying on behalf of shared mailboxes, saving as drafts, and customizing recipients. IMPORTANT: If you provide to_emails or cc_emails, they completely override the default reply behavior (reply_all parameter is ignored).",
                "usage": "reply_to_email(message_id: str, body: str, reply_all: bool = True, to_emails: Optional[List[str]] = None, cc_emails: Optional[List[str]] = None, attachment_ids: Optional[List[str]] = None, files: Optional[Sequence[File]] = None, session_state: Optional[Dict[str, Any]] = None, send_on_behalf_of_address: Optional[str] = None, save_as_draft: bool = False, category: Optional[str] = 'AI_Done') -> Dict[str, Union[str, bool]]"
            },
            "read_emails": {
                "description": "Read emails from the user's mailbox or a shared mailbox. Returns a list of email messages with details.",
                "usage": "read_emails(folder: str = 'inbox', limit: int = 10, unread_only: bool = False, shared_mailbox_address: Optional[str] = None) -> Dict[str, Any]"
            },
            "categorize_email": {
                "description": "Categorize an existing email to show AI worker status for team transparency. You MUST specify: 1) which category (AI_In_Progress/AI_Done/AI_Needs_Human), and 2) send_on_behalf_of_address (the mailbox address where the email exists, e.g., 'support@example.com'). Both parameters are REQUIRED.",
                "usage": "categorize_email(message_id: str, category: str, send_on_behalf_of_address: str) -> Dict[str, Union[str, bool]]"
            }
        }
    },
    "impargo": {
        "operations": {
            "create_impargo_customer": {
                "description": "Create a new customer in the Impargo logistics platform. REQUIRED: company name. OPTIONAL: contact person (firstname, lastname), phone (with country code like +49), email, address (country as 2-letter code like 'de'). Returns customer with unique ID (24-char hex) for use in offers. Phone defaults to '00' if not provided.",
                "usage": "create_impargo_customer(name: str, firstname: Optional[str] = '', lastname: Optional[str] = '', telephone: Optional[str] = '00', email: Optional[str] = None, address: Optional[Dict[str, str]] = None) -> Dict[str, Union[str, bool, Dict]]"
            },
            "get_impargo_customer": {
                "description": "Retrieve customer from Impargo by ID or name. Use EITHER customer_id (24-char hex for exact match) OR customer_name (partial match OK). Name search is case-insensitive: 'logistics' finds 'ABC Logistics GmbH'. Returns full customer data including ID, contacts, and address. Use returned ID for creating offers.",
                "usage": "get_impargo_customer(customer_id: Optional[str] = None, customer_name: Optional[str] = None) -> Dict[str, Union[str, bool, Dict[str, Union[str, List[str]]], int]]"
            },
            "create_impargo_offer": {
                "description": "Create shipping offer in Impargo. EXACT FIELD STRUCTURE REQUIRED: stops: [{'location': {'address': 'Full Address', 'city': 'Berlin', 'country': 'de', 'zip_code': '10115', 'street_and_house_number': 'Marienplatz 1'}, 'date_from': '2025-07-06T00:00:00.000Z', 'date_to': '2025-07-06T00:00:00.000Z'}], load: {'weight': 18.5, 'length': 13.6}, customer_id: '24-char-hex-id', reference: 'your-ref'. Common mistakes: Missing required location fields (city, country, zip_code, street_and_house_number), using 'weight_tons' instead of 'weight', wrong date format.",
                "usage": "create_impargo_offer(customer_id: str, reference: str, stops: List[Dict[str, Union[str, Dict[str, str]]]], load: Dict[str, Union[str, float]], comment: Optional[str] = None) -> Dict[str, Union[str, bool, Dict[str, Union[str, int, float]]]]"
            },
            "list_impargo_offers": {
                "description": "List/search offers in Impargo. FILTERS: reference (partial match OK), customer_id (24-char hex), archived (true/false/none). Default limit: 20 offers. Returns offer ID, reference, price (EUR), customer info, pickup/delivery locations, dates, and sharing URL. Use for finding existing offers or checking prices.",
                "usage": "list_impargo_offers(reference: Optional[str] = None, customer_id: Optional[str] = None, archived: Optional[bool] = None, limit: int = 20) -> Dict[str, Union[str, bool, List[Dict[str, Union[str, int, float, Dict[str, str]]]], int]]"
            },
            "get_impargo_orders": {
                "description": "Get/search company orders in Impargo. Should NOT be mixed with list_impargo_offers tool, this tool is for ORDERS. FILTERS: reference (exact match), status (OPEN/PLANNING/PLANNED/AUCTION/DECLINED/ASSIGNED/CONFIRMED/IN_PROGRESS/FINISHED/BILL_CREATED/PAID), archived (true/false/none). If no filters provided, returns all orders. Default limit: 20 orders. Returns order ID, reference, status, load details, pickup/delivery locations, customer info, and cost data. Use for finding existing orders or checking order status.",
                "usage": "get_impargo_orders(reference: Optional[str] = None, status: Optional[str] = None, archived: Optional[bool] = False, limit: int = 20) -> Dict[str, Union[str, bool, List[Dict[str, Any]], int]]"
            }
        }
    },
    "knowledge_base": {
        "operations": {
            "knowledge_base_search": {
                "description": "Search the attached knowledge base(s) for relevant information using vector similarity. Use this tool to find answers, context, or specific information from uploaded documents. Returns the most relevant text chunks that match your query with relevance scores. Example usage: query='project timeline', top_k=5 Returns structured results with document names, page numbers, and relevance scores.",
                "usage": "knowledge_base_search(query: str, top_k: int = 5)"
            }
        }
    },
    "memory": {
        "operations": {
            "store_fact_memory": {
                "description": "Creates a new long-term fact, rule, or user preference in the shared memory. Use this tool when a human colleague provides a new, general instruction or preference that you must remember and apply in all future sessions, should persist beyond the current session. The `topics` list can have at most 5 items.",
                "usage": "store_fact_memory(topics: List[str], title: str, content: str, explanation: str)"
            },
            "query_fact_memory": {
                "description": "Searches for existing facts, rules, or preferences in the shared memory. Use this tool to retrieve long-term knowledge to guide your actions. This is your primary way to 'remember' what you've been taught. Call before performing an action where a specific rule might apply (e.g., composing an email), or when a user asks a question about established procedures (e.g., 'What are the rules for quoting?').",
                "usage": "query_fact_memory(query: str, topics: Optional[List[str]] = None)"
            },
            "update_fact_memory": {
                "description": "Modifies an existing fact in the shared memory. Use this tool to update a rule that has been explicitly changed by a user. This should be used with caution to ensure the agent's knowledge base remains accurate. Call when a user corrects a previous instruction, saying something like, 'Actually, let's change that rule...' or 'Ignore what I said before about email subjects; do this instead.' First, query the existing fact to get its ID.",
                "usage": "update_fact_memory(memory_id: str, title: Optional[str] = None, content: Optional[str] = None, explanation: Optional[str] = None)"
            },
            "store_operation_memory": {
                "description": "Saves a snapshot of a key event or data point for a specific operation. Use this tool to record important milestones or data within the lifecycle of a single operation. The `topics` list can have at most 5 items. This creates a detailed log that can be reviewed later or queried by other concurrent sessions. Call at critical points in an operation, such as after receiving a quote, confirming a booking, or encountering an error.",
                "usage": "store_operation_memory(title: str, content: str, explanation: str, topics: Optional[List[str]] = None)"
            },
            "query_operation_memory": {
                "description": "Searches for historical data from past or completed operations. Use this tool to look up information from the historical record of operations to inform current decisions. This is for analyzing past performance, not for checking on currently active tasks. Call when you need historical context, such as a user asking, 'What rate did we get from RapidLogistics last month?' or 'Find our last shipment to Hamburg.'",
                "usage": "query_operation_memory(query: str, topics: Optional[List[str]] = None)"
            },
            "get_active_operations": {
                "description": "Retrieves a summary of other operations that are currently in progress. This tool provides real-time situational awareness of what other instances of this worker are doing right now. It's for understanding the concurrent operational landscape, not for historical analysis. Query based on updated_at within last 24 hours + topics. Excludes the current session. Call when a decision could be impacted by parallel activities. For instance, before quoting a price, check if other large quotes are being prepared for the same trade lane to avoid capacity issues or internal price conflicts.",
                "usage": "get_active_operations(topics: Optional[List[str]] = None)"
            }
        }
    },
    "teams": {
        "operations": {
            "send_teams_channel_message": {
                "description": "Send an HTML formatted message to a Microsoft Teams channel. Requires both team_id and channel_id. Message content must be in HTML format for proper display. Supported HTML tags: <br>, <strong>, <em>, <ul><li>, <p>. Example usage: team_id='abc123', channel_id='xyz789', message='<strong>Meeting Update:</strong><br>Time changed to 3 PM'. Returns JSON with 'success' boolean, 'message_id' string, and 'timestamp' on success, or 'error' string on failure.",
                "usage": "send_teams_channel_message(team_id: str, channel_id: str, message: str, important: bool = False)"
            },
            "send_teams_chat_message": {
                "description": "Send an HTML formatted message to a Microsoft Teams chat (1:1 or group chat). Requires chat_id. Message content must be in HTML format. Supported HTML tags: <br>, <strong>, <em>, <ul><li>, <p>. Example usage: chat_id='chat123', message='<strong>Quick question:</strong><br>Are you available?' Returns JSON with 'success' boolean, 'message_id' string, and 'timestamp' on success, or 'error' string on failure.",
                "usage": "send_teams_chat_message(chat_id: str, message: str)"
            },
            "reply_teams_message": {
                "description": "Reply to a message in a Microsoft Teams channel thread. Requires team_id, channel_id, message_id, and reply_message. Reply content must be in HTML format. Supported HTML tags: <br>, <strong>, <em>, <ul><li>, <p>. Example usage: team_id='abc123', channel_id='xyz789', message_id='msg456', reply_message='<em>Thanks!</em><br>That helps.' Returns JSON with 'success' boolean, 'reply_id' string, and 'timestamp' on success, or 'error' string on failure.",
                "usage": "reply_teams_message(team_id: str, channel_id: str, message_id: str, reply_message: str)"
            },
            "list_teams": {
                "description": "List all Teams that the user is a member of. Returns JSON with 'success' boolean, 'items' list of teams, and 'count' number. Each team contains: id, display_name, description. Example response: {'success': True, 'items': [{'id': 'team123', 'display_name': 'Sales Team', 'description': 'Sales discussions'}], 'count': 1}",
                "usage": "list_teams()"
            },
            "list_teams_channels": {
                "description": "List all channels in a specific Team. Requires team_id. Use list_teams to discover available teams if not given. Returns JSON with 'success' boolean, 'items' list of channels, and 'count' number. Each channel contains: id, display_name, description, email, web_url. Example usage: team_id='abc123' Example response: {'success': True, 'items': [{'id': 'ch123', 'display_name': 'General', 'description': 'General discussions'}], 'count': 1}",
                "usage": "list_teams_channels(team_id: str)"
            },
            "list_teams_chats": {
                "description": "List all chats the user is part of. Returns JSON with 'success' boolean, 'items' list of chats, and 'count' number. Each chat contains: id, topic, chat_type, created_at, last_updated. Example response: {'success': True, 'items': [{'id': 'chat123', 'topic': 'Project Discussion', 'chat_type': 'group'}], 'count': 1}",
                "usage": "list_teams_chats()"
            }
        }
    },
    "system": {
        "operations": {
            "exit_session": {
                "description": "Permanently exit a session when needed. Marks the session as HUMAN_TAKEOVER. Future messages in this thread will NOT trigger the agent.",
                "usage": "exit_session(reason: str, context_summary: str, assignee_email: Optional[str] = None)"
            }
        }
    }
}

class ToolCallRecorder:
    """A simple class to record tool calls made during a simulation episode."""
    def __init__(self):
        self._calls: List[Dict[str, Any]] = []

    def record(self, tool_name: str, parameters: Dict[str, Any], response: Any, agent_name: Optional[str] = None):
        """Records a single tool call and its response."""
        logger.debug(f"--- MOCK TOOL CALLED: {tool_name} by {agent_name or 'unknown'} with params: {parameters} ---")
        logger.debug(f"--- MOCK RESPONSE: {response} ---")
        self._calls.append({
            "tool_name": tool_name,
            "agent_name": agent_name,
            "parameters": parameters,
            "response": response
        })

    def get_calls(self) -> List[Dict[str, Any]]:
        """Returns the list of all recorded calls."""
        return self._calls

    def get_calls_by_agent(self) -> Dict[str, List[Dict[str, Any]]]:
        """Returns tool calls organized by agent name."""
        calls_by_agent = {}
        for call in self._calls:
            agent_name = call.get("agent_name", "unknown")
            if agent_name not in calls_by_agent:
                calls_by_agent[agent_name] = []
            calls_by_agent[agent_name].append(call)
        return calls_by_agent

    def clear(self):
        """Clears the list of recorded calls."""
        self._calls = []

# Global cache for LLM-generated responses
_LLM_RESPONSE_CACHE = {}

@retry(stop=stop_after_attempt(6), wait=wait_exponential(multiplier=2, min=10, max=60))
def _generate_fake_response_with_llm(
    tool_name: str,
    tool_description: str,
    tool_usage: str,
    parameters: Dict[str, Any],
    success_response_template: Optional[Any] = None,
    llm_hints: Optional[str] = None,
    use_llm: bool = True
) -> Any:
    """
    Uses Gemini to generate a realistic fake response for a tool call.
    Combines parameter validation and response generation.
    """
    if not use_llm:
        return None

    if 'knowledge_base' in tool_name:
        return None

    cache_key_payload = {
        "tool": tool_name,
        "params": parameters,
        "hints": llm_hints,
        "template": success_response_template,
        "usage": tool_usage
    }
    cache_key = hashlib.md5(json.dumps(cache_key_payload, sort_keys=True).encode()).hexdigest()

    if cache_key in _LLM_RESPONSE_CACHE:
        return _LLM_RESPONSE_CACHE[cache_key]

    try:
        import google.generativeai as genai
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable is not set.")
        
        genai.configure(api_key=api_key)

        model = genai.GenerativeModel(
            "gemini-2.5-pro",
            generation_config={"response_mime_type": "application/json"}
        )

        prompt = f"""
You are a mock tool execution engine. Your job is to simulate the execution of a software tool.
You must first VALIDATE the provided parameters against the tool's Usage definition.
Then, if valid, you must GENERATE a realistic JSON response.

Tool: {tool_name}
Description: {tool_description}
Usage Definition: {tool_usage}

Provided Parameters:
{json.dumps(parameters, indent=2)}

Instructions:
1.  **VALIDATION**:
    - Check the 'Provided Parameters' against the 'Usage Definition'.
    - Check if all REQUIRED parameters are present.
    - Check if parameter values match expected types.
    - **CRITICAL: BE STRICT.** If a required parameter is missing or invalid, you MUST return an error response. Do not infer or hallucinate missing values.

2.  **GENERATION**:
    - If validation FAILS: Generate a realistic error response (e.g., {{"success": false, "error": "Missing required parameter 'x'"}}).
    - If validation PASSES: Generate a realistic success response based on the tool description and parameters.
    - Use the 'Expected Response Format' below if provided.

"""
        if success_response_template:
            prompt += f"\nExpected Response Format (Success Case):\n{json.dumps(success_response_template, indent=2)}\n"

        if llm_hints:
            prompt += f"\nAdditional Hints: {llm_hints}\n"

        prompt += """
Output ONLY the final JSON response object.
"""

        response = model.generate_content(prompt)
        fake_response = json.loads(response.text)
        _LLM_RESPONSE_CACHE[cache_key] = fake_response
        return fake_response

    except Exception as e:
        logger.warning(f"[Warning] Failed to generate LLM response for {tool_name} on attempt. Error: {e}")
        raise

def create_mock_tool(
    name: str,
    recorder: ToolCallRecorder,
    description: str,
    usage: str,
    success_rate: float,
    success_response: Any,
    failure_response: Any,
    latency_ms: int,
    context: Optional[Any] = None,
    agent_name: Optional[str] = None,
    use_llm_generation: bool = False,
    llm_hints: Optional[str] = None,
    validate_params: bool = True
) -> Callable:
    """
    Higher-order function that creates a mock tool.
    """
    # Parse parameter names for mapping args to kwargs
    param_names = []
    if usage and '(' in usage and ')' in usage:
        params_str = usage[usage.find('(') + 1:usage.rfind(')')]
        if params_str:
            # Simple parsing, might need more robust parser for complex types
            param_parts = [p.strip() for p in params_str.split(',')]
            for p in param_parts:
                if ':' in p:
                    param_names.append(p.split(':')[0].strip())
                elif '=' in p:
                     param_names.append(p.split('=')[0].strip())
                else:
                    param_names.append(p.strip())

    def mock_function(*args, **kwargs):
        actual_args = args
        actual_kwargs = kwargs

        # Handle AGNO's tool call structure if passed as a single dict
        if len(args) == 1 and isinstance(args[0], dict) and 'args' in args[0] and 'kwargs' in args[0]:
            actual_args = args[0].get('args', [])
            actual_kwargs = args[0].get('kwargs', {})
            actual_kwargs.update(kwargs)

        parameters = {}
        for i, arg_val in enumerate(actual_args):
            if arg_val == '': continue
            if i < len(param_names):
                parameters[param_names[i]] = arg_val
            else:
                parameters[f'__arg_{i}__'] = arg_val
        parameters.update(actual_kwargs)

        # Combined Validation and Generation via LLM
        if use_llm_generation:
            if random.random() < success_rate:
                try:
                    response = _generate_fake_response_with_llm(
                        tool_name=name,
                        tool_description=description,
                        tool_usage=usage,
                        parameters=parameters,
                        success_response_template=success_response,
                        llm_hints=llm_hints,
                        use_llm=True
                    )
                    if response is None:
                        response = context if context is not None else (success_response or {"status": "success"})
                except Exception as e:
                    logger.error(f"[Error] LLM generation for {name} failed after retries: {e}. Using failure response.")
                    response = failure_response or {"status": "error", "message": "LLM generation failed."}
            else:
                response = failure_response or {"status": "error", "message": "Mock tool failed by configuration."}
        else:
            # Fallback to simple static validation if LLM generation is disabled
            # (This part is kept for backward compatibility or non-LLM tools)
            if validate_params:
                # Basic check: are we missing any obviously required params?
                # This is a weak check compared to LLM, but better than nothing.
                pass 
            
            if random.random() < success_rate:
                response = context if context is not None else (success_response or {"status": "success"})
            else:
                response = failure_response or {"status": "error", "message": "Mock tool failed by configuration."}

        recorder.record(name, parameters, response, agent_name=agent_name)
        return response

    mock_function.__name__ = name
    mock_function.__doc__ = f"{description}\nUsage: {usage}"
    return mock_function

def _load_mock_tool_config():
    """Loads and returns the mock tool configuration."""
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'mock_tool_config.yaml')
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning(f"[Warning] mock_tool_config.yaml not found at {config_path}. Using defaults.")
        return {}
    except Exception as e:
        logger.warning(f"[Warning] Error loading mock_tool_config.yaml: {e}. Using defaults.")
        return {}

def create_mock_tools_from_config(
    tool_bindings: Dict[str, Any],
    recorder: ToolCallRecorder,
    cache: Dict[str, Any],
    tool_contexts: Optional[Dict[str, Any]] = None,
    agent_name: Optional[str] = None
) -> List[Any]:
    """
    Creates a list of mock Agno Tools based on a configuration dictionary.
    """
    mock_tools = []
    mock_config = _load_mock_tool_config()
    mock_responses = mock_config.get('mock_tool_responses', {})
    default_success_rate = mock_config.get('default_success_rate', 1.0)
    default_use_llm = mock_config.get('default_use_llm_generation', False)
    default_validate_params = mock_config.get('default_validate_params', True)
    tool_contexts = tool_contexts or {}

    bindings = []
    if 'provider' in tool_bindings and 'operations' in tool_bindings:
        for op in tool_bindings['operations']:
            bindings.append((f"{tool_bindings['provider']}_{op}", tool_bindings['provider'], op))
    else:
        for tool_name, schema in tool_bindings.items():
            if isinstance(schema, dict):
                for op in schema.get('operations', []):
                    bindings.append((f"{tool_name}_{op}", tool_name, op))

    for full_tool_name, tool_name, op_name in bindings:
        cache_key = f"{agent_name}:{full_tool_name}" if agent_name else full_tool_name
        if cache_key not in cache:
            tool_response_config = mock_responses.get(op_name, {})
            tool_info = ALL_TOOLS_SCHEMA.get(tool_name, {}).get('operations', {}).get(op_name, {})
            context = tool_contexts.get(full_tool_name)

            mock_tool = create_mock_tool(
                name=full_tool_name,
                description=tool_info.get('description', ''),
                usage=tool_info.get('usage', ''),
                recorder=recorder,
                success_rate=tool_response_config.get('success_rate', default_success_rate),
                success_response=tool_response_config.get('success_response'),
                failure_response=tool_response_config.get('failure_response'),
                latency_ms=tool_response_config.get('latency_ms', 10),
                context=context,
                agent_name=agent_name,
                use_llm_generation=tool_response_config.get('use_llm_generation', default_use_llm),
                llm_hints=tool_response_config.get('llm_hints'),
                validate_params=tool_response_config.get('validate_params', default_validate_params)
            )
            cache[cache_key] = mock_tool
        mock_tools.append(cache[cache_key])

    return mock_tools
