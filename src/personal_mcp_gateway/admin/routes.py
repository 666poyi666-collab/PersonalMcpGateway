from __future__ import annotations

import hmac
import html
import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from personal_mcp_gateway.admin.support_bundle import create_support_bundle
from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.core.runtime import GatewayRuntime


def build_admin_app(runtime: GatewayRuntime) -> Starlette:
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

    async def status(request: Request) -> JSONResponse | HTMLResponse:
        value = await runtime.system_status()
        if "text/html" in request.headers.get("accept", ""):
            body = html.escape(json.dumps(value, ensure_ascii=False, indent=2))
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>Personal MCP Gateway</title>"
                "<style>body{font:14px ui-monospace,monospace;max-width:960px;margin:32px auto;"
                "padding:0 16px;color:#202124}pre{white-space:pre-wrap;"
                "background:#f5f6f7;padding:16px;"
                "border:1px solid #d8dadd;border-radius:6px}</style>"
                f"<h1>Personal MCP Gateway</h1><pre>{body}</pre>"
            )
        return JSONResponse(value)

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
            Route("/healthz", health),
            Route("/readyz", ready),
            Route("/metrics", metrics),
            Route("/admin/status", status),
            Route("/admin/modules", modules),
            Route("/admin/errors", errors),
            Route("/admin/modules/{id:str}/restart", restart_module, methods=["POST"]),
            Route("/admin/support-bundle", support_bundle),
        ]
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
