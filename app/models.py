from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

GroundingMode = Literal["strict", "grounded"]


class FileRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    display_name: str
    mime_type: str | None = None
    size_bytes: int
    sha256: str
    status: str = "pending"
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    num_chunks: int = 0
    paired_file_id: str | None = None
    subject: str | None = None
    source_path: str | None = None
    created_at: str
    updated_at: str


class SessionCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    file_ids: list[str] = Field(default_factory=list)
    grounding_mode: GroundingMode = "grounded"


class Session(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str | None = None
    goal: str
    phase: str = "setup"
    grounding_mode: GroundingMode = "grounded"
    current_node_id: str | None = None
    nodes_since_check: int = 0
    file_ids: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class Message(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    client_msg_id: str | None = None
    role: str
    kind: str = "text"
    content: str
    citations: list[str] = Field(default_factory=list)
    partial: int = 0
    created_at: str


class HealthReport(BaseModel):
    status: Literal["ok", "degraded", "error"]
    model_configured: bool
    endpoint_reachable: bool | None = None
    dependency_errors: list[str] = Field(default_factory=list)
    checked_at: str


class QuizQuestionInput(BaseModel):
    topic: str | None = None
    difficulty: int = 3
    question: str = Field(min_length=1)
    options: list[str] = Field(min_length=2, max_length=6)
    correct_index: int = Field(ge=0)
    explanation: str | None = None

    def validate_index(self) -> bool:
        return 0 <= self.correct_index < len(self.options)


class QuizQuestion(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    kind: str = "probe"
    topic: str | None = None
    difficulty: int = 3
    question: str
    options: list[str]
    correct_index: int
    explanation: str | None = None
    source_ref: str | None = None
    status: str = "pending"
    user_choice: int | None = None
    outcome: str | None = None
    created_at: str
    answered_at: str | None = None


class PlanNode(BaseModel):
    node_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    status: str = "pending"
    position: int = 0
    children: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    nodes: list[PlanNode]

    def validate_dependencies(self) -> bool:
        keys = {n.node_key for n in self.nodes}
        return all(dep in keys for n in self.nodes for dep in n.depends_on)


class MasteryTopic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    topic: str
    label: str
    confidence: float = 0.0
    observed_count: int = 0
    correct_count: int = 0
    idk_count: int = 0
    last_assessed_at: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None


class TurnBody(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    client_msg_id: str = Field(min_length=1, max_length=200)


class RetryBody(BaseModel):
    client_msg_id: str = Field(min_length=1, max_length=200)


class ProbeBody(BaseModel):
    pass


class CheckBody(BaseModel):
    pass


class NotesQuizBody(BaseModel):
    count: int = 3
    subject: str | None = None


class QuizAnswerBody(BaseModel):
    choice_index: int | None = None
    idk: bool = False
    confidence: Literal["guess", "confident", "know"] | None = None


class LearnerQuestionsBody(BaseModel):
    questions: list[str] = Field(min_length=2, max_length=2)


class WatchBody(BaseModel):
    path: str = Field(min_length=1)


class ReorderPlanBody(BaseModel):
    node_keys: list[str] = Field(min_length=1)


class PlanNodeActionBody(BaseModel):
    node_key: str = Field(min_length=1)


class ExpandPlanBody(BaseModel):
    node_key: str = Field(min_length=1)
    detail: str | None = None


class PreferencesBody(BaseModel):
    depth: Literal["brief", "standard", "deep"] | None = None
    pacing: Literal["slow", "normal", "fast"] | None = None
    style: Literal["analogy-first", "examples-first", "formal-first"] | None = None
    notes: str | None = None


class Preferences(BaseModel):
    depth: Literal["brief", "standard", "deep"]
    pacing: Literal["slow", "normal", "fast"]
    style: Literal["analogy-first", "examples-first", "formal-first"]
    notes: str | None = None
    updated_at: str


class StructuredOutputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_kind: Literal["chat", "mermaid", "todo", "quiz", "teach", "latex"]
    prompt: StrictStr = Field(min_length=1, max_length=8000)
    count: StrictInt = Field(default=3, ge=1, le=10)


class ChatOutputDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: StrictStr = Field(min_length=1, max_length=12000)


class MermaidOutputDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr = Field(min_length=1, max_length=200)
    source: StrictStr = Field(min_length=1, max_length=12000)


class TodoItemDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: StrictStr = Field(min_length=1, max_length=500)
    done: StrictBool = False


class TodoOutputDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr = Field(min_length=1, max_length=200)
    items: list[TodoItemDraft] = Field(min_length=1, max_length=100)


class QuizQuestionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: StrictStr = Field(min_length=1, max_length=1000)
    options: list[StrictStr] = Field(min_length=2, max_length=6)
    correct_index: StrictInt = Field(ge=0, le=5)
    explanation: StrictStr | None = Field(default=None, max_length=2000)
    topic: StrictStr | None = Field(default=None, max_length=200)
    difficulty: StrictInt = Field(default=3, ge=1, le=5)


class QuizOutputDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[QuizQuestionDraft] = Field(min_length=1, max_length=10)


class TeachActionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Literal[
        "continue", "ask_question", "practice", "example", "deeper", "next_topic"
    ]
    label: StrictStr = Field(min_length=1, max_length=60)
    prompt: StrictStr = Field(min_length=0, max_length=2000)


class TeachOutputDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: StrictStr = Field(min_length=1, max_length=12000)
    latex_blocks: list[Annotated[StrictStr, Field(max_length=6000)]] = Field(
        default_factory=list, max_length=6
    )
    actions: list[TeachActionDraft] = Field(min_length=1, max_length=6)


class LatexOutputDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr = Field(min_length=1, max_length=200)
    latex: StrictStr = Field(min_length=1, max_length=12000)


class ChatOutputContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: StrictStr = Field(min_length=1, max_length=12000)


class MermaidOutputContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr = Field(min_length=1, max_length=200)
    source: StrictStr = Field(min_length=1, max_length=12000)
    diagram_type: StrictStr = Field(min_length=1, max_length=100)


class TodoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StrictStr = Field(min_length=1, max_length=100)
    position: StrictInt = Field(ge=0, le=99)
    text: StrictStr = Field(min_length=1, max_length=500)
    done: StrictBool = False


class TodoOutputContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr = Field(min_length=1, max_length=200)
    items: list[TodoItem] = Field(min_length=1, max_length=100)


class StructuredQuizQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StrictStr = Field(min_length=1, max_length=100)
    position: StrictInt = Field(ge=0, le=9)
    question: StrictStr = Field(min_length=1, max_length=1000)
    options: list[StrictStr] = Field(min_length=2, max_length=6)
    correct_index: StrictInt = Field(ge=0, le=5)
    explanation: StrictStr | None = Field(default=None, max_length=2000)
    topic: StrictStr | None = Field(default=None, max_length=200)
    difficulty: StrictInt = Field(default=3, ge=1, le=5)


class QuizOutputContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[StructuredQuizQuestion] = Field(min_length=1, max_length=10)


class TeachOutputContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: StrictStr = Field(min_length=1, max_length=12000)
    latex_blocks: list[Annotated[StrictStr, Field(max_length=6000)]] = Field(
        default_factory=list, max_length=6
    )
    actions: list[TeachActionDraft] = Field(min_length=1, max_length=6)


class LatexOutputContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr = Field(min_length=1, max_length=200)
    latex: StrictStr = Field(min_length=1, max_length=12000)


class ValidationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["validated"] = "validated"
    attempts: StrictInt = Field(ge=1, le=2)


class StructuredOutputEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    output_id: StrictStr = Field(min_length=1, max_length=100)
    session_id: StrictStr = Field(min_length=1, max_length=100)
    citations: list[StrictStr] = Field(default_factory=list, max_length=100)
    validation: ValidationMetadata


class ChatOutputEnvelope(StructuredOutputEnvelope):
    kind: Literal["chat"]
    content: ChatOutputContent


class MermaidOutputEnvelope(StructuredOutputEnvelope):
    kind: Literal["mermaid"]
    content: MermaidOutputContent


class TodoOutputEnvelope(StructuredOutputEnvelope):
    kind: Literal["todo"]
    content: TodoOutputContent


class QuizOutputEnvelope(StructuredOutputEnvelope):
    kind: Literal["quiz"]
    content: QuizOutputContent


class TeachOutputEnvelope(StructuredOutputEnvelope):
    kind: Literal["teach"]
    content: TeachOutputContent


class LatexOutputEnvelope(StructuredOutputEnvelope):
    kind: Literal["latex"]
    content: LatexOutputContent


StructuredOutputResponse = Annotated[
    ChatOutputEnvelope
    | MermaidOutputEnvelope
    | TodoOutputEnvelope
    | QuizOutputEnvelope
    | TeachOutputEnvelope
    | LatexOutputEnvelope,
    Field(discriminator="kind"),
]
