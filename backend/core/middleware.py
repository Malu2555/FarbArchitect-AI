"""Minimal CORS middleware - zero external dependencies.

Honours an explicit allowlist (``settings.CORS_ALLOWED_ORIGINS``) and answers
preflight ``OPTIONS`` requests in-place so the Vercel frontend can talk to the
Modal endpoint cross-origin without any wildcard leakage.
"""

from __future__ import annotations

from typing import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

__all__ = ["CorsMiddleware"]


class CorsMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

        origins: list[str] = []
        for entry in getattr(settings, "CORS_ALLOWED_ORIGINS", []) or []:
            entry = (entry or "").strip()
            if entry:
                origins.append(entry)
        self.allow_origins = tuple(origins)
        self.allow_methods = tuple(
            getattr(settings, "CORS_ALLOW_METHODS", ["GET", "POST", "OPTIONS"])
        )
        self.allow_headers = tuple(
            getattr(
                settings,
                "CORS_ALLOW_HEADERS",
                ["content-type", "accept", "authorization"],
            )
        )
        self.expose_headers = tuple(getattr(settings, "CORS_EXPOSE_HEADERS", []))
        self.allow_credentials = bool(
            getattr(settings, "CORS_ALLOW_CREDENTIALS", False)
        )
        self.max_age = str(getattr(settings, "CORS_MAX_AGE", 86400))

    def __call__(self, request: HttpRequest) -> HttpResponse:
        origin = request.headers.get("Origin", "").strip()
        is_preflight = (
            request.method == "OPTIONS"
            and "access-control-request-method" in request.headers
        )

        if is_preflight:
            # Short-circuit: never hit the view layer for preflight.
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        if origin and origin in self.allow_origins:
            response["Access-Control-Allow-Origin"] = origin
            existing_vary = response.get("Vary", "")
            response["Vary"] = (
                f"{existing_vary}, Origin".strip(", ") if "Origin" not in existing_vary
                else existing_vary
            )
            response["Access-Control-Allow-Methods"] = ", ".join(self.allow_methods)
            response["Access-Control-Allow-Headers"] = ", ".join(self.allow_headers)
            if self.expose_headers:
                response["Access-Control-Expose-Headers"] = ", ".join(
                    self.expose_headers
                )
            if self.allow_credentials:
                response["Access-Control-Allow-Credentials"] = "true"
            response["Access-Control-Max-Age"] = self.max_age

        return response
