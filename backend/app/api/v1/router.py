from fastapi import APIRouter

from app.api.v1.endpoints import auth, documents, health, parsed, tables

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(parsed.router, prefix="/documents", tags=["parsed"])
api_router.include_router(tables.router, prefix="/documents", tags=["tables"])
