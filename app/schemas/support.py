"""Schemas for the help desk / support ticket system."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TicketMessageOut(BaseModel):
    id: int
    ticket_id: int
    sender_user_id: int
    sender_role: str
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketOut(BaseModel):
    id: int
    user_id: int
    subject: str
    category: str
    priority: str
    status: str
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[int] = None
    assigned_admin_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    messages: list[TicketMessageOut] = []

    model_config = {"from_attributes": True}


class TicketListItem(BaseModel):
    id: int
    user_id: int
    subject: str
    category: str
    priority: str
    status: str
    assigned_admin_id: Optional[int] = None
    message_count: int
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TicketCreate(BaseModel):
    subject: str
    category: str = "general"
    priority: str = "normal"
    message: str
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[int] = None


class TicketMessageCreate(BaseModel):
    body: str


class AdminTicketUpdate(BaseModel):
    """Admin-only ticket update: change status, priority, and/or assignment."""
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_admin_id: Optional[int] = None
