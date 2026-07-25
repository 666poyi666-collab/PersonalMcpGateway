from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import uvicorn

from personal_mcp_gateway.admin.support_bundle import create_support_bundle
from personal_mcp_gateway.app import build_apps
from personal_mcp_gateway.core.logging import configure_logging
from personal_mcp_gateway.core.runtime import GatewayRuntime
from personal_mcp_gateway.core.secrets import SecretStore
from personal_mcp_gateway.modules.loader import load_modules
from personal_mcp_gateway.settings import Settings
from personal_mcp_gateway.storage.database import Database


def build_runtime(settings: Settings | None = None) -> GatewayRuntime:
    settings = settings or Settings()
    database = Database(settings.database_path)
    registry = load_modules(settings.modules_dir, database, SecretStore())
    return GatewayRuntime(settings, database, registry)


async def serve(runtime: GatewayRuntime) -> None:
    await runtime.start()
    mcp_app, admin_app, _ = build_apps(runtime)
    mcp_config = uvicorn.Config(
        mcp_app,
        host=runtime.settings.mcp_host,
        port=runtime.settings.mcp_port,
        log_config=None,
    )
    admin_config = uvicorn.Config(
        admin_app,
        host=runtime.settings.admin_host,
        port=runtime.settings.admin_port,
        lifespan="off",
        log_config=None,
    )
    mcp_server = uvicorn.Server(mcp_config)
    admin_server = uvicorn.Server(admin_config)
    try:
        await asyncio.gather(mcp_server.serve(), admin_server.serve())
    finally:
        await runtime.stop()


async def doctor(runtime: GatewayRuntime) -> int:
    await runtime.database.migrate()
    report = {
        "python": sys.version.split()[0],
        "database": str(runtime.settings.database_path),
        "modules": [module.module_id for module in runtime.registry.modules()],
        "mcp": f"http://{runtime.settings.mcp_host}:{runtime.settings.mcp_port}/mcp",
        "admin": f"http://{runtime.settings.admin_host}:{runtime.settings.admin_port}",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="personal-mcp-gateway")
    value.add_argument(
        "command",
        choices=["serve", "stdio", "doctor", "migrate-db", "collect-support-bundle"],
        nargs="?",
        default="serve",
    )
    return value


def main() -> None:
    args = parser().parse_args()
    settings = Settings()
    configure_logging(settings)
    runtime = build_runtime(settings)
    if args.command == "serve":
        try:
            asyncio.run(serve(runtime))
        except KeyboardInterrupt:
            pass
    elif args.command == "stdio":
        _, _, mcp = build_apps(runtime)
        mcp.run("stdio")
    elif args.command == "doctor":
        raise SystemExit(asyncio.run(doctor(runtime)))
    elif args.command == "migrate-db":
        asyncio.run(runtime.database.migrate())
    else:

        async def collect() -> Path:
            await runtime.database.migrate()
            return await create_support_bundle(runtime)

        print(asyncio.run(collect()))


if __name__ == "__main__":
    main()
