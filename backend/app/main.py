import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.http_security import install_http_security

logger = logging.getLogger(__name__)

app = FastAPI(title="Yarrow API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
install_http_security(app, enforce_https=settings.ENFORCE_HTTPS)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Same shape as FastAPI's default 422, minus the submitted values.

    The default echoes each rejected value back in an "input" field, which
    for a too-short password means sending the password straight back, where
    browser tools, proxies and error trackers can record it (NFR-6). The
    field name ("loc") and reason ("msg") are kept for the frontend.
    """
    errors = [
        {key: value for key, value in error.items() if key not in ("input", "url")}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


@app.exception_handler(Exception)
async def standard_error_handler(request: Request, exc: Exception):
    """Log the full error for developers; tell the client only that it failed.

    Exception text can contain connection strings, credentials, or user
    input, so it never goes into the response (NFR-6).
    """
    logger.exception(f"Unhandled error on {request.method} {request.url.path}")
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Something went wrong on our side. Please try again.",
            "code": "INTERNAL_SERVER_ERROR",
        },
    )


app.include_router(api_router, prefix="/api/v1")
