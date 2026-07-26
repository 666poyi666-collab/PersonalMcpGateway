from __future__ import annotations

import hmac
import json
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route

from personal_mcp_gateway.admin.dashboard import DashboardMonitor
from personal_mcp_gateway.admin.support_bundle import create_support_bundle
from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.core.runtime import GatewayRuntime

STATIC_ROOT = Path(__file__).with_name("static")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'",
        )
        return response


def build_admin_app(runtime: GatewayRuntime) -> Starlette:
    dashboard = DashboardMonitor(runtime)

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"gateway": "alive", "version": runtime.settings.version})

    async def ready(_: Request) -> JSONResponse:
        status = 200 if runtime.ready else 503
        modules = await runtime.registry.all_health()
        return JSONResponse(
            {
                "gateway": "ready" if runtime.ready else "starting",
                "modules": {key: value.state for key, value in modules.items()},
            },
            status_code=status,
        )

    async def metrics(_: Request) -> PlainTextResponse:
        return PlainTextResponse(
            "# TYPE personal_mcp_tool_calls_total counter\n"
            f"personal_mcp_tool_calls_total {runtime.calls_total}\n"
            "# TYPE personal_mcp_tool_failures_total counter\n"
            f"personal_mcp_tool_failures_total {runtime.calls_failed}\n"
            "# TYPE personal_mcp_ready gauge\n"
            f"personal_mcp_ready {1 if runtime.ready else 0}\n",
            media_type="text/plain; version=0.0.4",
        )

    async def status(request: Request) -> Response:
        value = await runtime.system_status()
        if "text/html" in request.headers.get("accept", ""):
            return FileResponse(STATIC_ROOT / "dashboard.html", media_type="text/html")
        return JSONResponse(value)

    async def dashboard_data(request: Request) -> JSONResponse:
        return JSONResponse(
            await dashboard.snapshot(force=request.query_params.get("force") == "1"),
            headers={"Cache-Control": "no-store"},
        )

    async def dashboard_asset(request: Request) -> FileResponse | JSONResponse:
        name = request.path_params["name"]
        if name not in {"dashboard.css", "dashboard.js"}:
            return JSONResponse({"error": "not_found"}, status_code=404)
        media_type = "text/css" if name.endswith(".css") else "text/javascript"
        return FileResponse(STATIC_ROOT / name, media_type=media_type)

    async def root(_: Request) -> RedirectResponse:
        return RedirectResponse("/admin/status", status_code=307)

    async def modules(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "modules": [
                    {
                        "id": module.module_id,
                        "enabled": module.manifest.enabled,
                        "version": module.version,
                    }
                    for module in runtime.registry.modules()
                ]
            }
        )

    async def errors(request: Request) -> JSONResponse:
        limit = int(request.query_params.get("limit", "20"))
        return JSONResponse({"errors": await runtime.recent_errors(limit)})

    async def restart_module(request: Request) -> JSONResponse:
        if not _authorized(
            request,
            runtime.admin_token,
            csrf_token=runtime.admin_csrf_token,
        ):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            result = await runtime.registry.restart(request.path_params["id"])
            await runtime.database.execute(
                "INSERT INTO audit_events(event,summary_json) VALUES (?,?)",
                (
                    "module_restart",
                    json.dumps({"module": request.path_params["id"]}, separators=(",", ":")),
                ),
            )
            return JSONResponse(result.model_dump(by_alias=True, exclude_none=True))
        except GatewayError as exc:
            return JSONResponse({"error": exc.code, "message": exc.message}, status_code=409)

    async def support_bundle(request: Request) -> FileResponse | JSONResponse:
        if not _authorized(request, runtime.admin_token):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        path = await create_support_bundle(runtime)
        return FileResponse(path, filename=path.name, media_type="application/zip")

    return Starlette(
        routes=[
            Route("/", root),
            Route("/healthz", health),
            Route("/readyz", ready),
            Route("/metrics", metrics),
            Route("/admin/status", status),
            Route("/admin/dashboard-data", dashboard_data),
            Route("/admin/assets/{name:str}", dashboard_asset),
            Route("/admin/modules", modules),
            Route("/admin/errors", errors),
            Route("/admin/modules/{id:str}/restart", restart_module, methods=["POST"]),
            Route("/admin/support-bundle", support_bundle),
        ],
        middleware=[Middleware(SecurityHeadersMiddleware)],
    )


def _authorized(request: Request, token: str, *, csrf_token: str | None = None) -> bool:
    provided = request.headers.get("X-Admin-Token", "")
    origin = request.headers.get("origin")
    if origin and origin not in {
        "http://127.0.0.1:8761",
        "http://localhost:8761",
    }:
        return False
    if not provided or not hmac.compare_digest(provided, token):
        return False
    if csrf_token is not None:
        provided_csrf = request.headers.get("X-CSRF-Token", "")
        return bool(provided_csrf) and hmac.compare_digest(provided_csrf, csrf_token)
    return True
