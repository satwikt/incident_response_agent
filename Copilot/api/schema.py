"""Pydantic schemas for SRE Copilot API."""

from pydantic import BaseModel
from typing import Any, Dict, List, Optional


class ChatInfoResponse(BaseModel):
    id: int
    title: str
    created_at: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    message: str
    llm_calls: int = 0


class ChatResponse(BaseModel):
    id: int
    title: str
    messages: List[ChatMessage]


class NewChatResponse(BaseModel):
    id: int


class AskChatRequest(BaseModel):
    message: str


class AskChatResponse(BaseModel):
    response: str
    llm_calls: int = 0
    intent: Optional[str] = None
    agent: Optional[str] = None
    incident_id: Optional[str] = None


class RenameChatRequest(BaseModel):
    title: str


class SuccessResponse(BaseModel):
    success: bool


# ─── Dashboard Schemas ──────────────────────────────────────────────────────────

class IncidentEvent(BaseModel):
    id: int
    incident_id: str
    ts: int
    kind: str
    actor: str
    detail: Optional[str] = ""


class IncidentSummary(BaseModel):
    id: str
    service: str
    status: str
    severity: str
    opened_at: int
    last_seen_at: int
    resolved_at: Optional[int] = None
    resolved_by: Optional[str] = None
    fingerprint: str
    evidence: List[str] = []
    rca_text: Optional[str] = None
    rca_status: str = "pending"
    proposed_action: Optional[str] = None
    actions: List[Dict[str, Any]] = []
    recalled: List[Dict[str, Any]] = []
    version: int = 1


class IncidentDetail(IncidentSummary):
    fingerprints: List[Dict[str, Any]] = []
    context_sample: Optional[str] = None
    memory_status: Optional[str] = None
    timeline: List[IncidentEvent] = []


class MemoryItem(BaseModel):
    incident_id: str
    narrative: str
    score: Optional[float] = None
    fix: Optional[str] = None
    failed: List[str] = []
    source: str = "live"
    tags: List[str] = []


class PastIncidentsMemoryResponse(BaseModel):
    enabled: bool
    bank_id: str
    total: int
    memories: List[MemoryItem]


class TelemetryOverviewResponse(BaseModel):
    service: str
    health: Dict[str, Any]
    error_rate: Dict[str, Any]
    latency: Dict[str, Any]
    active_incidents: int
    total_incidents: int


class IncidentFeedbackRequest(BaseModel):
    action: str
    worked: bool
    notes: Optional[str] = None


class IncidentFeedbackResponse(BaseModel):
    incident_id: str
    status: str
    action: str
    worked: bool
    memory_retained: bool
    new_recommendation: Optional[str] = None
    message: str

