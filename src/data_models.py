from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class UnhappyPath(BaseModel):
    model_config = {"extra": "forbid"}
    description: str
    path: List[str] = Field(..., alias='scenario')

class EvaluationScore(BaseModel):
    model_config = {"extra": "forbid"}
    criterion: str
    score: float
    analysis: str

class TurnEvaluation(BaseModel):
    model_config = {"extra": "forbid"}
    composo_scores: List[EvaluationScore] = []
    latency_seconds: Optional[float] = None

class AgentResponse(BaseModel):
    model_config = {"extra": "forbid"}
    response: List[str]
    internal: List[str]
    trace: Optional[Any] = None
    evaluation: Optional[TurnEvaluation] = None

class PathEvaluationSummary(BaseModel):
    model_config = {"extra": "forbid"}
    by_criterion: Dict[str, Any] = {}
    overall: float = 0.0

class UnhappyPathEvaluationSummary(PathEvaluationSummary):
    model_config = {"extra": "forbid"}
    description: str = ""

class PartitionEvaluationSummary(BaseModel):
    model_config = {"extra": "forbid"}
    happy_path_avg: Optional[Any] = None
    partition_unhappy_path_avg: Optional[Any] = None
    unhappy_paths_avg: List[Any] = []
    avg_latency_seconds: Optional[float] = None

class Transition(BaseModel):
    model_config = {"extra": "forbid"}
    source_partition_index: int
    source_partition_name: str
    conversation: List[str]
    response: Optional[AgentResponse] = None

class Partition(BaseModel):
    model_config = {"extra": "forbid"}
    partition_name: str
    task: str
    triggering_stakeholder: str
    halting_stakeholder: str
    triggering_event_description: str
    expected_agent_output: str
    happy_path: List[str] = []
    partition_unhappy_path: List[str] = []
    unhappy_paths: List[UnhappyPath] = []
    happy_path_response: List[AgentResponse] = []
    partition_unhappy_path_response: List[AgentResponse] = []
    unhappy_path_responses: List[List[AgentResponse]] = []
    transitions: List[Transition] = []
    context: List[str] = []
    evaluation_summary: Optional[Any] = None

class Partitions(BaseModel):
    model_config = {"extra": "forbid"}
    partitions: List[Partition]

class Stakeholder(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    description: str

class ScenarioEvaluationSummary(BaseModel):
    model_config = {"extra": "forbid"}
    by_partition: Dict[str, Any] = {}
    overall: float = 0.0
    avg_latency_seconds: Optional[float] = None

class SOPEvaluationSummary(BaseModel):
    model_config = {"extra": "forbid"}
    overall_score: float = 0.0
    by_criterion: Dict[str, Any] = {}
    by_scenario: Dict[str, Any] = {}
    performance_metrics: Dict[str, Any] = {}

class Scenario(BaseModel):
    model_config = {"extra": "forbid"}
    scenario_name: str
    description: str
    stakeholders: List[str]
    partitions: List[Partition]
    scenario_happy_path: List[str] = []
    scenario_unhappy_path: List[str] = []
    scenario_happy_path_response: List[AgentResponse] = []
    scenario_unhappy_path_response: List[AgentResponse] = []
    evaluation_summary: Optional[Any] = None

class SOPGraphLLMOutput(BaseModel):
    model_config = {"extra": "forbid"}
    stakeholders: List[Stakeholder]
    scenarios: List[Scenario]
    ambiguities: List[str] = []

class TransitionScenario(BaseModel):
    model_config = {"extra": "forbid"}
    description: str
    path: List[str] = Field(..., alias='scenario')

class WorkerConfig(BaseModel):
    model_config = {"extra": "allow"}
    sop: Optional[str] = None

class SOPGraph(BaseModel):
    model_config = {"extra": "forbid"}
    worker_name: str
    stakeholders: List[Stakeholder]
    scenarios: List[Scenario]
    ambiguities: List[str] = []
    transition_paths: List[TransitionScenario] = []
    system_prompt: Optional[str] = None
    worker_config: Optional[WorkerConfig] = None
    evaluation_summary: Optional[Any] = None
