"""Aggregate recruiter router, mounted under /api/v1/recruiter."""
from fastapi import APIRouter

from app.recruiter.api.admin_routes import router as admin_router
from app.recruiter.api.agency_routes import router as agency_router
from app.recruiter.api.auth_routes import router as auth_router
from app.recruiter.api.billing_routes import router as billing_router
from app.recruiter.api.routes import (
    applications_router,
    candidates_router,
    clients_router,
    market_router,
    pipeline_router,
    roles_router,
    shortlist_router,
)

recruiter_router = APIRouter(prefix="/recruiter")
# Auth (public login) + operator management (admin-guarded) + agency-admin (owner).
recruiter_router.include_router(auth_router)
recruiter_router.include_router(admin_router)
recruiter_router.include_router(agency_router)
recruiter_router.include_router(billing_router)
# Agency-scoped resources (recruiter- or operator-authenticated).
recruiter_router.include_router(clients_router)
recruiter_router.include_router(roles_router)
recruiter_router.include_router(candidates_router)
recruiter_router.include_router(shortlist_router)
recruiter_router.include_router(pipeline_router)
recruiter_router.include_router(applications_router)
recruiter_router.include_router(market_router)
