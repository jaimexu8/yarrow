"""HTTP-level protections for credentials in transit (NFR-6).

- Auth responses carry passwords, codes and tokens, so they are marked
  ``Cache-Control: no-store``: no browser or proxy may keep a copy
  (required for token responses by RFC 6749, section 5.1).
- ``X-Content-Type-Options: nosniff`` stops browsers from guessing a
  response's type and running it as something else.
- With ``ENFORCE_HTTPS`` on, plain-HTTP requests are redirected to HTTPS and
  every response tells browsers to only ever use HTTPS for this site (HSTS).
  Off by default, because local development runs on http://localhost.
  Behind a proxy that terminates TLS, uvicorn must trust that proxy's
  X-Forwarded-Proto header: set FORWARDED_ALLOW_IPS to the proxy's address
  (uvicorn reads it from the environment). Otherwise every request looks
  like http and would redirect forever.
"""

from fastapi import FastAPI, Request
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

AUTH_PATH_PREFIX = "/api/v1/auth"
HSTS_VALUE = "max-age=31536000; includeSubDomains"  # one year


def install_http_security(app: FastAPI, *, enforce_https: bool) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith(AUTH_PATH_PREFIX):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        if enforce_https:
            response.headers["Strict-Transport-Security"] = HSTS_VALUE
        return response

    if enforce_https:
        # Added last, so it runs first and redirects before any other work.
        app.add_middleware(HTTPSRedirectMiddleware)
