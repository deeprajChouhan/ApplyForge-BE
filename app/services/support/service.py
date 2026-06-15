"""SupportService — help desk / support ticket system.

Tickets are created by users with an initial message, replied to by either
the owning user or an admin, and managed (status/priority/assignment,
archive/restore) by admins.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.models.models import SupportTicket, SupportTicketMessage
from app.services.analytics.service import ProductEventService
from app.services.audit.service import AuditService
from app.services.common.soft_delete import not_deleted, restore, soft_delete

OPEN_STATUSES = ("open", "in_progress")


class SupportService:
    def __init__(self, db: Session, user_id: int | None = None):
        self.db = db
        self.user_id = user_id

    # ── User-facing ──────────────────────────────────────────────────────

    def create_ticket(
        self,
        subject: str,
        category: str,
        priority: str,
        message: str,
        related_entity_type: Optional[str] = None,
        related_entity_id: Optional[int] = None,
        request: Request | None = None,
    ) -> SupportTicket:
        ticket = SupportTicket(
            user_id=self.user_id,
            subject=subject,
            category=category,
            priority=priority,
            status="open",
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        )
        self.db.add(ticket)
        self.db.flush()

        self.db.add(SupportTicketMessage(
            ticket_id=ticket.id,
            sender_user_id=self.user_id,
            sender_role="user",
            body=message,
        ))
        self.db.commit()
        self.db.refresh(ticket)

        AuditService(self.db).log(
            action="support_ticket_created",
            entity_type="support_ticket",
            entity_id=ticket.id,
            actor_user_id=self.user_id,
            actor_role="user",
            after={"subject": subject, "category": category, "priority": priority},
            request=request,
        )
        ProductEventService(self.db).track(
            "support_ticket_created",
            user_id=self.user_id,
            entity_type="support_ticket",
            entity_id=ticket.id,
            properties={"category": category, "priority": priority},
            request=request,
        )
        return ticket

    def list_my_tickets(self) -> list[SupportTicket]:
        return (
            not_deleted(self.db.query(SupportTicket), SupportTicket)
            .filter_by(user_id=self.user_id)
            .order_by(SupportTicket.created_at.desc())
            .all()
        )

    def get_ticket(self, ticket_id: int, *, admin: bool = False) -> SupportTicket:
        q = self.db.query(SupportTicket).filter_by(id=ticket_id)
        if not admin:
            q = not_deleted(q, SupportTicket).filter_by(user_id=self.user_id)
        ticket = q.first()
        if not ticket:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
        return ticket

    def get_messages(self, ticket_id: int) -> list[SupportTicketMessage]:
        return (
            not_deleted(self.db.query(SupportTicketMessage), SupportTicketMessage)
            .filter_by(ticket_id=ticket_id)
            .order_by(SupportTicketMessage.created_at.asc())
            .all()
        )

    def add_message(
        self,
        ticket_id: int,
        body: str,
        *,
        admin: bool = False,
        sender_user_id: int | None = None,
        request: Request | None = None,
    ) -> SupportTicketMessage:
        ticket = self.get_ticket(ticket_id, admin=admin)
        sender_role = "admin" if admin else "user"
        actor_id = sender_user_id if sender_user_id is not None else self.user_id

        message = SupportTicketMessage(
            ticket_id=ticket.id,
            sender_user_id=actor_id,
            sender_role=sender_role,
            body=body,
        )
        self.db.add(message)

        if admin and ticket.status == "open":
            ticket.status = "in_progress"
        elif not admin and ticket.status not in OPEN_STATUSES:
            ticket.status = "open"
            ticket.resolved_at = None
            ticket.closed_at = None

        self.db.commit()
        self.db.refresh(message)

        AuditService(self.db).log(
            action="support_ticket_replied",
            entity_type="support_ticket",
            entity_id=ticket.id,
            actor_user_id=actor_id,
            actor_role=sender_role,
            after={"status": ticket.status},
            request=request,
        )
        ProductEventService(self.db).track(
            "support_ticket_replied",
            user_id=actor_id,
            entity_type="support_ticket",
            entity_id=ticket.id,
            properties={"sender_role": sender_role},
            request=request,
        )
        return message

    # ── Admin ────────────────────────────────────────────────────────────

    def list_all(
        self,
        status_filter: Optional[str] = None,
        priority: Optional[str] = None,
        category: Optional[str] = None,
        assigned_admin_id: Optional[int] = None,
    ) -> list[SupportTicket]:
        """All tickets, including archived ones, for admin management."""
        q = self.db.query(SupportTicket)
        if status_filter:
            q = q.filter_by(status=status_filter)
        if priority:
            q = q.filter_by(priority=priority)
        if category:
            q = q.filter_by(category=category)
        if assigned_admin_id is not None:
            q = q.filter_by(assigned_admin_id=assigned_admin_id)
        return q.order_by(SupportTicket.created_at.desc()).all()

    def update_ticket(
        self,
        ticket_id: int,
        *,
        new_status: Optional[str] = None,
        priority: Optional[str] = None,
        assigned_admin_id: Optional[int] = None,
        actor_user_id: Optional[int] = None,
        request: Request | None = None,
    ) -> SupportTicket:
        ticket = self.get_ticket(ticket_id, admin=True)
        before = {
            "status": ticket.status,
            "priority": ticket.priority,
            "assigned_admin_id": ticket.assigned_admin_id,
        }

        if new_status is not None:
            ticket.status = new_status
            ticket.resolved_at = datetime.utcnow() if new_status == "resolved" else None
            ticket.closed_at = datetime.utcnow() if new_status == "closed" else None
        if priority is not None:
            ticket.priority = priority
        if assigned_admin_id is not None:
            ticket.assigned_admin_id = assigned_admin_id

        self.db.commit()
        self.db.refresh(ticket)

        AuditService(self.db).log(
            action="admin_ticket_updated",
            entity_type="support_ticket",
            entity_id=ticket.id,
            actor_user_id=actor_user_id,
            actor_role="admin",
            before=before,
            after={
                "status": ticket.status,
                "priority": ticket.priority,
                "assigned_admin_id": ticket.assigned_admin_id,
            },
            request=request,
        )
        return ticket

    def archive(self, ticket_id: int, actor_user_id: Optional[int] = None, request: Request | None = None) -> SupportTicket:
        ticket = self.get_ticket(ticket_id, admin=True)
        soft_delete(self.db, ticket)
        AuditService(self.db).log(
            action="admin_ticket_archived",
            entity_type="support_ticket",
            entity_id=ticket.id,
            actor_user_id=actor_user_id,
            actor_role="admin",
            request=request,
        )
        return ticket

    def restore_ticket(self, ticket_id: int, actor_user_id: Optional[int] = None, request: Request | None = None) -> SupportTicket:
        ticket = self.db.query(SupportTicket).filter_by(id=ticket_id).first()
        if not ticket:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
        restore(self.db, ticket)
        AuditService(self.db).log(
            action="admin_ticket_restored",
            entity_type="support_ticket",
            entity_id=ticket.id,
            actor_user_id=actor_user_id,
            actor_role="admin",
            request=request,
        )
        return ticket
