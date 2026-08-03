import os
import sys
import uuid
import json
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone

# --- Environment Setup ---
from dotenv import load_dotenv

# Load environment from labryon/.env
labryon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")); dotenv_path = os.path.join(labryon_root, ".env"); load_dotenv(dotenv_path=dotenv_path, override=True)

# Add labryon root to sys.path
labryon_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if labryon_root not in sys.path:
    sys.path.insert(0, labryon_root)


from sqlalchemy import create_engine, Column, String, Text, Boolean, Float, ForeignKey, Integer
from sqlalchemy.orm import sessionmaker, relationship, declarative_base, joinedload
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID, JSONB, ARRAY
from sqlalchemy import TIMESTAMP, UniqueConstraint

# NOTE: Standalone testing harness schema. All data is fetched directly from the database.

# --- Database Connection Setup ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set. Check your .env file in the project root.")

SYNC_DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
engine = create_engine(SYNC_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# --- Model Replication ---
class WorkerV2(Base):
    __tablename__ = "workers_v2"
    id = Column(PostgresUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    status = Column(String(50), default="INACTIVE")
    capabilities = Column(JSONB, default=lambda: [])
    agents = Column(JSONB, default=lambda: [])
    sop = Column(Text)
    supervisor_model = Column(String(100), default="gpt-4")
    supervisor_temperature = Column(Float, default=0)
    supervisor_tool_bindings = Column(JSONB, default=lambda: {})
    supervisor_knowledge_base_ids = Column(ARRAY(Text), default=lambda: [])
    configuration = Column(JSONB, default=lambda: {})

class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    id = Column(PostgresUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

class KnowledgeBaseChunk(Base):
    __tablename__ = "knowledge_base_chunks"
    id = Column(PostgresUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(PostgresUUID(as_uuid=True), ForeignKey("knowledge_base_documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text)
    document = relationship("KnowledgeBaseDocument", back_populates="chunks")

class KnowledgeBaseDocument(Base):
    __tablename__ = "knowledge_base_documents"
    id = Column(PostgresUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_base_id = Column(PostgresUUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)
    document_name = Column(String(255), nullable=False)
    chunks = relationship("KnowledgeBaseChunk", back_populates="document", cascade="all, delete-orphan", lazy="joined")


# --- Data Fetching Functions ---
def get_available_workers() -> List[Tuple[str, uuid.UUID]]:
    """
    Get list of available workers from database.

    Returns:
        List of tuples (worker_name, worker_id) ordered by name
    """
    db_session = SessionLocal()
    try:
        workers = db_session.query(WorkerV2.name, WorkerV2.id).order_by(WorkerV2.name).all()
        return workers if workers else []
    except Exception as e:
        print(f"An error occurred while listing workers: {e}")
        return []
    finally:
        db_session.close()


def list_available_workers():
    """List available workers and print them."""
    workers = get_available_workers()
    if not workers:
        print("No AI-Workers found in the database.")
        return
    print("--- Available AI-Workers ---")
    for idx, (name, worker_id) in enumerate(workers):
        print(f"[{idx}] {name}")
    print("----------------------------")

def get_kb_content(kb_ids: List[str]) -> List[Dict[str, Any]]:
    if not kb_ids:
        return []
    db_session = SessionLocal()
    try:
        kb_uuid_ids = []
        for kb_id in kb_ids:
            try:
                kb_uuid_ids.append(uuid.UUID(kb_id))
            except ValueError:
                print(f"Warning: Invalid kb_id format: '{kb_id}'. Skipping.")
        
        if not kb_uuid_ids:
            return []

        documents = db_session.query(KnowledgeBaseDocument).filter(
            KnowledgeBaseDocument.knowledge_base_id.in_(kb_uuid_ids)
        ).all()

        result = []
        for doc in documents:
            if doc.chunks:
                sorted_chunks = sorted(doc.chunks, key=lambda c: c.chunk_index)
                full_content = "".join(c.chunk_text for c in sorted_chunks if c.chunk_text)
            else:
                full_content = ""
            
            result.append({
                "kb_id": str(doc.knowledge_base_id),
                "document_name": doc.document_name,
                "content": full_content
            })
        return result
    except Exception as e:
        print(f"An error occurred while fetching knowledge base content: {e}")
        return []
    finally:
        db_session.close()

def get_worker_configuration(worker_id: uuid.UUID) -> Optional[Dict[str, Any]]:
    db_session = SessionLocal()
    try:
        worker = db_session.query(WorkerV2).filter(WorkerV2.id == worker_id).one_or_none()

        if not worker:
            print(f"Error: WorkerV2 with ID {worker_id} not found.")
            return None

        # In the test environment, the supervisor's tools are mocked separately.
        # We will pass the database configuration as-is.
        final_supervisor_tools = worker.supervisor_tool_bindings or {}

        subagents = []
        agent_kb_ids = []

        # Import agent metadata registry
        from agent_metadata import get_agent_metadata

        # All agent data comes from the database (worker.agents)
        # Agent descriptions and prompts come from static metadata (synced with backend)
        if worker.agents and isinstance(worker.agents, list):
            for agent_config in worker.agents:
                agent_id = agent_config.get('agent_id')
                if not agent_id:
                    print(f"Warning: Agent config missing 'agent_id': {agent_config}")
                    continue

                # Get static metadata for this agent (description, prompt)
                agent_metadata = get_agent_metadata(agent_id)
                if not agent_metadata:
                    print(f"Warning: No metadata found for agent '{agent_id}' in agent_metadata.py")
                    agent_metadata = {
                        "description": "",
                        "prompt": "",
                        "name": agent_id
                    }

                preferences_str = agent_config.get("preferences")
                preferences = {}
                if preferences_str and isinstance(preferences_str, str):
                    try:
                        preferences = json.loads(preferences_str)
                    except json.JSONDecodeError:
                        print(f"Warning: could not parse preferences for agent {agent_id}")
                elif isinstance(preferences_str, dict):
                    preferences = preferences_str

                tool_bindings = preferences.get("tool_bindings") or agent_config.get("tool_bindings", {})

                current_agent_kb_ids = preferences.get("knowledge_base_ids", [])
                agent_kb_ids.extend(current_agent_kb_ids)

                subagents.append({
                    "name": agent_id,
                    "agent_id": agent_id,
                    "description": agent_metadata.get("description", ""),
                    "prompt": agent_metadata.get("prompt", ""),
                    "model": agent_config.get("model"),
                    "tool_requirements": agent_config.get("tool_requirements", {}),
                    "tool_bindings": tool_bindings,
                    "knowledge_base_ids": current_agent_kb_ids,
                })
        else:
            print(f"Warning: No agents configured for worker '{worker.name}'")
            # Return empty agents list - snapshot can still be created
        
        all_kb_ids = list(set((worker.supervisor_knowledge_base_ids or []) + agent_kb_ids))
        all_kb_content = get_kb_content(all_kb_ids)

        # Prepare variables for the system prompt
        prompt_variables = {
            "company_name": "Simulation Company",
            "team_members": "\n".join([f"- {a['name']}: {a['description']}" for a in subagents]),
            "memory": "",  # Fact memories are mocked in the simulation
            "summary_of_previous_interactions": "", # No summary for a fresh run
            "sop": worker.sop,
            "current_time": datetime.now(timezone.utc).isoformat(),
        }
        
        system_prompt = get_system_prompt("team_system_message", prompt_variables)

        config = {
            "worker_id": worker.id, "name": worker.name, "sop": worker.sop,
            "supervisor_tools": final_supervisor_tools,
            "supervisor_knowledge_base_ids": worker.supervisor_knowledge_base_ids or [],
            "knowledge_bases": all_kb_content,
            "system_prompt": system_prompt,
            "agents": subagents,
        }
        return config
    except Exception as e:
        print(f"An error occurred while fetching worker configuration: {e}")
        return None
    finally:
        db_session.close()

def get_worker_by_name(worker_name: str) -> Optional[Dict[str, Any]]:
    db_session = SessionLocal()
    try:
        workers = db_session.query(WorkerV2).filter(WorkerV2.name == worker_name).all()
        if not workers:
            print(f"Error: No worker found with the name '{worker_name}'.")
            return None
        if len(workers) > 1:
            print(f"Found multiple workers with the name '{worker_name}'. Please specify by ID:")
            for worker in workers:
                print(f"  - ID: {worker.id}")
            return None
        return get_worker_configuration(workers[0].id)
    except Exception as e:
        print(f"An error occurred while fetching worker by name: {e}")
        return None
    finally:
        db_session.close()

def get_system_prompt(prompt_name: str, variables: Optional[Dict[str, Any]] = None) -> str:
    """
    Fetches a system prompt using Langfuse with local fallback.
    Replicates the backend prompt_manager behavior.
    """
    from prompt_manager import prompt_manager

    try:
        return prompt_manager.get_prompt(prompt_name, variables)
    except Exception as e:
        print(f"CRITICAL: Could not fetch prompt '{prompt_name}'. Error: {e}")
        # Emergency fallback
        if variables is None:
            variables = {}
        return f"System prompt unavailable. SOP: {variables.get('sop', 'N/A')}"

# --- Main Execution ---
if __name__ == '__main__':
    import sys
    try:
        workers = get_available_workers()
        if not workers:
            print("No AI-Workers found in the database.")
            sys.exit(1)

        print("--- Available AI-Workers ---")
        for idx, (name, _) in enumerate(workers):
            print(f"[{idx}] {name}")
        print("----------------------------")

        try:
            worker_idx_str = input("Enter the index of the AI-Worker to fetch: ")
            worker_idx = int(worker_idx_str)
            if not 0 <= worker_idx < len(workers):
                raise IndexError
            worker_name_to_find = workers[worker_idx][0]
        except (ValueError, IndexError):
            print("Invalid index. Exiting.")
            sys.exit(1)
        
        if not worker_name_to_find:
            print("No name entered. Exiting.")
        else:
            print(f"\nFetching configuration for AI-Worker: '{worker_name_to_find}'")
            worker_config = get_worker_by_name(worker_name_to_find)

            if worker_config:
                print("\n" + "="*50)
                print("--- Worker Configuration Fetched ---")
                print(f"ID:   {worker_config['worker_id']}")
                print(f"Name: {worker_config['name']}")
                
                sop = worker_config.get('sop', '') or ''
                words = sop.split()
                sop_display = " ".join(words[:30]) + " ... " + " ".join(words[-30:]) if len(words) > 60 else sop
                print(f"\nSOP:\n{sop_display}")

                print("\n" + "-"*20 + " SYSTEM PROMPT " + "-"*20)
                print(worker_config.get('system_prompt', 'System prompt not found.'))
                
                print("\n" + "-"*20 + " KNOWLEDGE BASES " + "-"*19)
                kb_content = worker_config.get('knowledge_bases', [])
                if not kb_content:
                    print("No knowledge base content found for this worker.")
                for doc in kb_content:
                    if doc:
                        print(f"  - Document: {doc.get('document_name')} (from KB: {doc.get('kb_id')})")
                        content = doc.get('content')
                        if content:
                            print(f"    Content: {content[:200]}...")
                        else:
                            print("    Content: [Not available or empty]")

                print("\n" + "-"*23 + " SUBAGENTS " + "-"*24)
                if not worker_config['agents']:
                    print("No subagents found for this worker.")
                for agent in worker_config['agents']:
                    print(f"\n--- Subagent: {agent['name']} ---")
                    print(f"  - Description: {agent['description']}")
                    print(f"  - Tool Bindings: {json.dumps(agent['tool_bindings'], indent=2)}")
                    
                    agent_kb_ids = agent['knowledge_base_ids']
                    print(f"  - Knowledge Bases: {agent_kb_ids}")

                print("\n" + "="*50)

    except ValueError as e:
        print(f"\nError: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

