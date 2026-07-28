from pathlib import Path
from unittest.mock import AsyncMock, patch

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
        assert response.json() == {
            "ready": True,
            "gateway": "ready",
            "dependencies": {
                "database": {
                    "state": "ready",
                    "schemaVersion": 2,
                    "expectedSchemaVersion": 2,
                }
            },
            "modules": {},
            "degraded": False,
            "unavailableModules": [],
        }
        forbidden = await client.post("/admin/modules/example/restart")
        assert forbidden.status_code == 403
    await runtime.stop()


@pytest.mark.asyncio
async def test_ready_fails_closed_when_the_live_database_probe_fails(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, modules_dir=tmp_path / "missing")
    runtime = GatewayRuntime(settings, Database(settings.database_path), ModuleRegistry())
    await runtime.start()
    async with AsyncClient(
        transport=ASGITransport(app=build_admin_app(runtime)), base_url="http://test"
    ) as client:
        with patch.object(
            runtime.database,
            "readiness",
            new=AsyncMock(side_effect=OSError("fixture database unavailable")),
        ):
            response = await client.get("/readyz")
            metrics = await client.get("/metrics")

    assert response.status_code == 503
    assert response.json() == {
        "ready": False,
        "gateway": "unavailable",
        "dependencies": {"database": {"state": "unavailable"}},
        "modules": {},
        "degraded": False,
        "unavailableModules": [],
    }
    assert "personal_mcp_ready 0" in metrics.text
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
        assert html.status_code == 200 and "所有系统" in html.text
        assert "Content-Security-Policy" in html.headers
        dashboard = await client.get("/admin/dashboard-data")
        assert dashboard.status_code == 200
        snapshot = dashboard.json()
        assert snapshot["refreshIntervalSeconds"] == 4
        assert snapshot["summary"]["total"] == 4
        assert snapshot["targets"][0]["id"] == "personal"
        assert snapshot["targets"][0]["sync"]["dataPlane"] == "local_only"
        assert snapshot["targets"][0]["sync"]["pcOff"]["continuedSync"] is False
        assert snapshot["targets"][1]["sync"]["dataPlane"] == "snapshot_mirror"
        assert snapshot["targets"][3]["sync"]["dataPlane"] == "cloud_primary"
        assert len(snapshot["activity"]["hourly"]) == 24
        assert snapshot["events"] == []
        cached = (await client.get("/admin/dashboard-data")).json()
        assert cached["generatedAt"] == snapshot["generatedAt"]
        forced = (await client.get("/admin/dashboard-data?force=1")).json()
        assert forced["generatedAt"] != snapshot["generatedAt"]
        stylesheet = await client.get("/admin/assets/dashboard.css")
        script = await client.get("/admin/assets/dashboard.js")
        profile_script = await client.get("/admin/assets/dashboard-profile.js")
        assert stylesheet.status_code == 200 and "project-grid" in stylesheet.text
        assert script.status_code == 200 and "dashboard-data" in script.text
        assert profile_script.status_code == 200 and "prepareExchange" in profile_script.text
        assert (await client.get("/admin/assets/private.txt")).status_code == 404
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
