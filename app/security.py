"""Response hardening: security headers on every response."""

from flask import Flask, Response, request

# Everything we serve is same-origin (no inline scripts/styles, no external
# assets), so the policy can be strict. frame-ancestors blocks clickjacking.
CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def init_app(app: Flask) -> None:
    @app.after_request
    def add_headers(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Ticket pages contain customer data: don't let browsers/proxies cache
        # them. Static assets are fine to cache.
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response
