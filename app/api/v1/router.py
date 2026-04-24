from fastapi import APIRouter
from app.api.v1.routes import admin, applications, auth, chat, documents, ephemeral, knowledge, profile

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(knowledge.router)
api_router.include_router(applications.router)
api_router.include_router(chat.router)
api_router.include_router(documents.router)
api_router.include_router(admin.router)
api_router.include_router(ephemeral.router)

