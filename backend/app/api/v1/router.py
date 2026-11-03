from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    auth,
    documents,
    folders,
    health,
    integrations,
    parsed,
    regions,
    search,
    sharing,
    tables,
    tags,
    warnings,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(folders.router, prefix="/folders", tags=["folders"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(tags.document_router, prefix="/documents", tags=["tags"])
api_router.include_router(tags.router, prefix="/tags", tags=["tags"])
api_router.include_router(sharing.router, prefix="/documents", tags=["sharing"])
api_router.include_router(parsed.router, prefix="/documents", tags=["parsed"])
api_router.include_router(tables.router, prefix="/documents", tags=["tables"])
api_router.include_router(regions.router, prefix="/documents", tags=["regions"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(warnings.router, prefix="/warnings", tags=["warnings"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(
    integrations.router, prefix="/integrations", tags=["integrations"]
)
