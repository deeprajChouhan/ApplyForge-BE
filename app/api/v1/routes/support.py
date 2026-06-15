"""
Help desk / support ticket API routes (user-facing).

  POST /support/tickets              -> create a ticket (with first message)
  GET  /support/tickets              -> list the user's tickets
  GET  /support/tickets/{id}         -> ticket detail incl. message thread
  POST /support/tickets/{id}/messages -> reply to a ticket
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.models import User
from app.schemas.support import (
    TicketCreate,
    TicketListItem,
    TicketMessageCreate,
    TicketMessageOut,
    TicketOut,
)
from app.services.support.service import SupportService

router = APIRouter(prefix="/support", tags=["support"])


def _ticket_out(service: SupportService, ticket_id: int) -> TicketOut:
    ticket = service.get_ticket(ticket_id)
    messages = service.get_messages(ticket_id)
    return TicketOut(
        id=ticket.id,
        user_id=ticket.user_id,
        subject=ticket.subject,
        category=ticket.category,
        priority=ticket.priority,
        status=ticket.status,
        related_entity_type=ticket.related_entity_type,
        related_entity_id=ticket.related_entity_id,
        assigned_admin_id=ticket.assigned_admin_id,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        resolved_at=ticket.resolved_at,
        closed_at=ticket.closed_at,
        messages=[TicketMessageOut.model_validate(m) for m in messages],
    )


@router.post("/tickets", response_model=TicketOut, status_code=201)
def create_ticket(
    payload: TicketCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = SupportService(db, user.id)
    ticket = service.create_ticket(
        payload.subject,
        payload.category,
        payload.priority,
        payload.message,
        related_entity_type=payload.related_entity_type,
        related_entity_id=payload.related_entity_id,
        request=request,
    )
    return _ticket_out(service, ticket.id)


@router.get("/tickets", response_model=list[TicketListItem])
def list_tickets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = SupportService(db, user.id)
    tickets = service.list_my_tickets()
    return [
        TicketListItem(
            id=t.id,
            user_id=t.user_id,
            subject=t.subject,
            category=t.category,
            priority=t.priority,
            status=t.status,
            assigned_admin_id=t.assigned_admin_id,
            message_count=len(service.get_messages(t.id)),
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in tickets
    ]


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = SupportService(db, user.id)
    return _ticket_out(service, ticket_id)


@router.post("/tickets/{ticket_id}/messages", response_model=TicketOut)
def reply_to_ticket(
    ticket_id: int,
    payload: TicketMessageCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = SupportService(db, user.id)
    service.add_message(ticket_id, payload.body, admin=False, request=request)
    return _ticket_out(service, ticket_id)
