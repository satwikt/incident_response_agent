"""Pydantic schemas for SRE Copilot API."""

from pydantic import BaseModel
from typing import List, Optional


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


class RenameChatRequest(BaseModel):
    title: str


class SuccessResponse(BaseModel):
    success: bool
