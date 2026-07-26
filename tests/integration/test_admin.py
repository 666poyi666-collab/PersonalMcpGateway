from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from personal_mcp_gateway.admin.routes import build_admin_app
from personal_mcp_gateway.core.registry import ModuleRegistry
from personal_mcp_gateway.core.runtime import GatewayRuntime
from personal_mcp_gateway.settings import Settings
from personal_mcp_gateway.storage.database import Database


@pytest.mark.asyncio
async def test_ready_does_not_require_modules(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, modules_dir=tmp_path / "missing")
    runtime = GatewayRuntime(settings, Database(settings.database_path), ModuleRegistry())
    await runtime.start()
    async with AsyncClient(
        transport=ASGITransport(app=build_admin_app(runtime)), base_url="http://test"
    ) as client:
        response = await client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"gateway": "ready", "modules": {}}
        forbidden = await client.post("/admin/modules/example/restart")
        assert forbidden.status_code == 403
    await runtime.stop()


@pytest.mark.asyncio
async def test_admin_endpoints_authorization_and_bundle(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, modules_dir=tmp_path / "missing")
    runtime = GatewayRuntime(settings, Database(settings.database_path), ModuleRegistry())
    await runtime.start()
    async with AsyncClient(
        transport=ASGITransport(app=build_admin_app(runtime)), base_url="http://test"
    ) as client:
        assert (await client.get("/healthz")).json()["gateway"] == "alive"
        assert "personal_mcp_ready 1" in (await client.get("/metrics")).text
        html = await client.get("/admin/status", headers={"accept": "text/html"})
        assert html.status_code == 200 and "<h1>" in html.text
        assert (await client.get("/admin/modules")).json() == {"modules": []}
        assert (await client.get("/admin/errors?limit=1")).json() == {"errors": []}

        headers = {
            "X-Admin-Token": runtime.admin_token,
            "X-CSRF-Token": runtime.admin_csrf_token,
        }
        restart = await client.post("/admin/modules/missing/restart", headers=headers)
        assert restart.status_code == 409
        blocked = await client.post(
            "/admin/modules/missing/restart",
            headers={**headers, "origin": "https://example.invalid"},
        )
        assert blocked.status_code == 403
        bundle = await client.get("/admin/support-bundle", headers=headers)
        assert bundle.status_code == 200
        assert bundle.headers["content-type"] == "application/zip"
    await runtime.stop()
