from __future__ import annotations

import json
import time
import uuid
from typing import Any, cast

from personal_mcp_gateway.core.errors import GatewayError
from personal_mcp_gateway.core.models import (
    HealthState,
    ModuleHealth,
    ModuleManifest,
    PermissionLevel,
    ResourceDefinition,
    ToolDefinition,
)
from personal_mcp_gateway.core.result import now_iso
from personal_mcp_gateway.core.secrets import SecretStore
from personal_mcp_gateway.modules.watch.client import (
    EndpointConfig,
    WatchHttpClient,
    encoded_id,
)
from personal_mcp_gateway.storage.database import Database
from personal_mcp_gateway.storage.idempotency_store import IdempotencyStore


class WatchModule:
    module_id = "watch"
    display_name = "WatchIntervals"
    version = "1"

    def __init__(
        self,
        manifest: ModuleManifest,
        raw: dict[str, Any],
        database: Database,
        secrets: SecretStore,
    ) -> None:
        self.manifest = manifest
        self.database = database
        self.idempotency = IdempotencyStore(database)
        self.startup_error: GatewayError | None = None
        try:
            pairing_code = secrets.get(manifest.authentication.secret_ref)
        except GatewayError as exc:
            pairing_code = ""
            self.startup_error = exc
        raw_watch = raw.get("watch", {})
        raw_phone = raw.get("phone", {})
        watch = cast(dict[str, Any], raw_watch) if isinstance(raw_watch, dict) else {}
        phone = cast(dict[str, Any], raw_phone) if isinstance(raw_phone, dict) else {}
        self.watch_config = self._endpoint("watch", watch, pairing_code)
        self.phone_config = self._endpoint("phone", phone, pairing_code)
        self.watch = self._new_client(self.watch_config)
        self.phone = self._new_client(self.phone_config)
        self.started = False
        self.last_success_at: str | None = None

    def _endpoint(self, role: str, value: dict[str, Any], pairing_code: str) -> EndpointConfig:
        default_service = (
            "_watchintervals-phone._tcp.local."
            if role == "phone"
            else "_watchintervals._tcp.local."
        )
        return EndpointConfig(
            role=role,
            service_type=str(value.get("service_type", default_service)),
            expected_device_id=str(value.get("expected_device_id", "")),
            id_field="phoneDeviceId" if role == "phone" else "deviceId",
            fallback_base_url=str(value.get("fallback_base_url", "")),
            pairing_code=pairing_code,
        )

    async def start(self) -> None:
        if self.startup_error is not None:
            raise self.startup_error
        if self.phone.closed:
            self.phone = self._new_client(self.phone_config)
        if self.watch.closed:
            self.watch = self._new_client(self.watch_config)
        self.started = True

    async def stop(self) -> None:
        await self.phone.close()
        await self.watch.close()
        self.started = False

    def _new_client(self, config: EndpointConfig) -> WatchHttpClient:
        return WatchHttpClient(
            config,
            self.database,
            self.manifest.timeouts,
            self.manifest.retry,
        )

    async def health(self) -> ModuleHealth:
        if not self.started:
            return ModuleHealth(state=HealthState.STARTING)
        phone_state: dict[str, Any]
        watch_state: dict[str, Any]
        try:
            phone_state = await self.phone.verify()
        except GatewayError as exc:
            return ModuleHealth(
                state=HealthState.DEGRADED,
                message=exc.code,
                lastSuccessAt=self.last_success_at,
                phone="offline",
                watch="unknown",
            )
        try:
            watch_state = await self.watch.verify()
        except GatewayError as exc:
            return ModuleHealth(
                state=HealthState.DEGRADED,
                message=exc.code,
                lastSuccessAt=self.last_success_at,
                phone="online",
                watch="offline",
                protocolVersion=phone_state.get("protocolVersion"),
            )
        self.last_success_at = now_iso()
        return ModuleHealth(
            state=HealthState.HEALTHY,
            lastSuccessAt=self.last_success_at,
            phone="online",
            watch="online",
            protocolVersion=phone_state.get("protocolVersion"),
            watchVersion=watch_state.get("appVersion"),
        )

    def tools(self) -> list[ToolDefinition]:
        read = PermissionLevel.READ
        write = PermissionLevel.WRITE_REVERSIBLE
        definitions = [
            ("watch_get_status", "Get phone, watch, and workout state", read, self.get_status),
            (
                "watch_get_current_plan",
                "Get the selected training plan",
                read,
                self.get_current_plan,
            ),
            ("watch_list_plans", "List plans from the phone plan library", read, self.list_plans),
            (
                "watch_set_plan",
                "Create or update a plan with revision protection",
                write,
                self.set_plan,
            ),
            ("watch_select_plan", "Select and synchronize a plan", write, self.select_plan),
            ("watch_list_workouts", "List workout summaries", read, self.list_workouts),
            (
                "watch_get_workout",
                "Get a workout summary and route resource URI",
                read,
                self.get_workout,
            ),
            (
                "watch_get_latest_sleep",
                "Get the latest system sleep record",
                read,
                self.get_latest_sleep,
            ),
            (
                "watch_summarize_sleep",
                "Summarize recent system sleep metrics",
                read,
                self.summarize_sleep,
            ),
            ("watch_start_workout", "Start the selected workout plan", write, self.start_workout),
            ("watch_pause_workout", "Pause a running workout", write, self.pause_workout),
            ("watch_resume_workout", "Resume a paused workout", write, self.resume_workout),
            ("watch_stop_workout", "Stop and save the active workout", write, self.stop_workout),
            (
                "watch_sync_status",
                "Get phone-to-watch synchronization status",
                read,
                self.sync_status,
            ),
        ]
        return [
            ToolDefinition(
                name=name,
                description=description,
                permission=permission,
                idempotent=True,
                timeout_seconds=self.manifest.timeouts.write_seconds
                if permission is not read
                else self.manifest.timeouts.read_seconds,
                handler=handler,
            )
            for name, description, permission, handler in definitions
        ]

    def resources(self) -> list[ResourceDefinition]:
        values = [
            ("watch://status", "Watch status", self.resource_status),
            ("watch://plans", "Watch plans", self.resource_plans),
            ("watch://workouts/recent", "Recent workouts", self.resource_recent_workouts),
            ("watch://workouts/{id}", "Workout summary", self.resource_workout),
            ("watch://workouts/{id}/route", "Paginated workout route", self.resource_route),
            ("watch://sleep/latest", "Latest sleep", self.resource_latest_sleep),
            ("watch://diagnostics", "Watch diagnostics", self.resource_diagnostics),
        ]
        return [
            ResourceDefinition(uri=uri, name=name, description=name, handler=handler)
            for uri, name, handler in values
        ]

    async def get_status(self, _: dict[str, Any]) -> dict[str, Any]:
        phone = await self.phone.verify()
        try:
            watch = await self.watch.verify()
        except GatewayError as exc:
            watch = {"state": "offline", "error": exc.code}
        return {"phone": phone, "watch": watch}

    async def get_current_plan(self, _: dict[str, Any]) -> dict[str, Any]:
        result = await self.watch.request("GET", "/v1/plan/profile")
        return self._object(result)

    async def list_plans(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._object(await self.phone.request("GET", "/v1/plans"))

    async def set_plan(self, args: dict[str, Any]) -> dict[str, Any]:
        request_id = self._required(args, "requestId")
        expected_revision = self._required_int(args, "expectedRevision")
        raw_plan = args.get("plan")
        if not isinstance(raw_plan, dict):
            raise GatewayError("INVALID_ARGUMENT", "plan must be an object")
        plan = cast(dict[str, Any], raw_plan)
        payload: dict[str, Any] = {
            "requestId": request_id,
            "expectedRevision": expected_revision,
            "plan": plan,
        }
        return await self._write_once(
            "watch_set_plan",
            request_id,
            payload,
            lambda: self.phone.request("POST", "/v1/plans", payload),
        )

    async def select_plan(self, args: dict[str, Any]) -> dict[str, Any]:
        request_id = self._required(args, "requestId")
        payload = {
            "requestId": request_id,
            "expectedRevision": self._required_int(args, "expectedRevision"),
            "planId": self._required(args, "planId"),
        }
        return await self._write_once(
            "watch_select_plan",
            request_id,
            payload,
            lambda: self.phone.request("PUT", "/v1/plan-selection", payload),
        )

    async def list_workouts(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(100, int(args.get("limit", 20))))
        result = await self.watch.request("GET", "/v1/history")
        rows = cast(list[Any], result) if isinstance(result, list) else []
        return {"items": rows[:limit], "count": min(limit, len(rows)), "total": len(rows)}

    async def get_workout(self, args: dict[str, Any]) -> dict[str, Any]:
        workout_id = self._required(args, "id")
        result = self._object(
            await self.watch.request("GET", f"/v1/history/{encoded_id(workout_id)}")
        )
        result.pop("route", None)
        result.pop("heartRates", None)
        result["routeResource"] = f"watch://workouts/{workout_id}/route"
        return result

    async def get_latest_sleep(self, _: dict[str, Any]) -> dict[str, Any]:
        result = self._object(await self.watch.request("GET", "/v1/sleep?days=7"))
        records = result.get("records", [])
        latest = max(records, key=lambda item: item.get("timestamp", 0)) if records else None
        return {"state": result.get("state"), "source": result.get("source"), "record": latest}

    async def summarize_sleep(self, args: dict[str, Any]) -> dict[str, Any]:
        days = max(1, min(31, int(args.get("days", 7))))
        result = self._object(await self.watch.request("GET", f"/v1/sleep?days={days}"))
        records = result.get("records", [])
        durations = [
            x.get("totalDurationMinutes") for x in records if x.get("totalDurationMinutes")
        ]
        scores = [x.get("sleepScore") for x in records if x.get("sleepScore")]
        spo2 = [x.get("spo2AveragePercent") for x in records if x.get("spo2AveragePercent")]
        return {
            "state": result.get("state"),
            "source": result.get("source"),
            "recordCount": len(records),
            "averageDurationMinutes": round(sum(durations) / len(durations)) if durations else None,
            "averageSleepScore": round(sum(scores) / len(scores)) if scores else None,
            "averageSpo2Percent": round(sum(spo2) / len(spo2), 1) if spo2 else None,
            "sampleCounts": {
                "duration": len(durations),
                "sleepScore": len(scores),
                "spo2": len(spo2),
            },
        }

    async def start_workout(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._control("start", args)

    async def pause_workout(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._control("pause", args)

    async def resume_workout(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._control("resume", args)

    async def stop_workout(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._control("stop", args)

    async def _control(self, action: str, args: dict[str, Any]) -> dict[str, Any]:
        request_id = self._required(args, "requestId")
        payload = {
            "requestId": request_id,
            "expectedRevision": self._required_int(args, "expectedRevision"),
            "commandId": self._required(args, "commandId"),
            "expectedState": self._required(args, "expectedState"),
            "expiresAt": self._required_int(args, "expiresAt"),
        }
        if int(payload["expiresAt"]) < int(time.time() * 1000):
            raise GatewayError("COMMAND_EXPIRED", "Control command has expired")
        return await self._write_once(
            f"watch_{action}_workout",
            request_id,
            payload,
            lambda: self.watch.request("POST", f"/v1/control/{action}", payload),
            ttl_seconds=86400,
        )

    async def sync_status(self, _: dict[str, Any]) -> dict[str, Any]:
        status = await self.phone.verify()
        return {
            "state": status.get("syncState", status.get("state", "unknown")),
            "pending": status.get("pendingOperationCount"),
            "verified": status.get("syncVerified", False),
        }

    async def _write_once(
        self,
        scope: str,
        request_id: str,
        payload: dict[str, Any],
        operation: Any,
        ttl_seconds: int = 604800,
    ) -> dict[str, Any]:
        previous = await self.idempotency.claim(scope, request_id, payload, ttl_seconds)
        if previous is not None:
            return previous
        try:
            result = self._object(await operation())
            await self.idempotency.complete(scope, request_id, result)
            return result
        except GatewayError as exc:
            await self.idempotency.fail(scope, request_id, exc)
            raise

    async def resource_status(self, _: dict[str, str]) -> str:
        return self._json(await self.get_status({}))

    async def resource_plans(self, _: dict[str, str]) -> str:
        return self._json(await self.list_plans({}))

    async def resource_recent_workouts(self, _: dict[str, str]) -> str:
        return self._json(await self.list_workouts({"limit": 20}))

    async def resource_workout(self, variables: dict[str, str]) -> str:
        return self._json(await self.get_workout({"id": variables["id"]}))

    async def resource_route(self, variables: dict[str, str]) -> str:
        workout_id = encoded_id(variables["id"])
        cursor = max(0, int(variables.get("cursor", "0")))
        result = await self.watch.request(
            "GET", f"/v1/history/{workout_id}/route?cursor={cursor}&limit=500"
        )
        return self._json(result)

    async def resource_latest_sleep(self, _: dict[str, str]) -> str:
        return self._json(await self.get_latest_sleep({}))

    async def resource_diagnostics(self, _: dict[str, str]) -> str:
        health = await self.health()
        return health.model_dump_json(by_alias=True)

    @staticmethod
    def _required(args: dict[str, Any], name: str) -> str:
        value = args.get(name)
        if not isinstance(value, str) or not value.strip():
            raise GatewayError("INVALID_ARGUMENT", f"{name} is required")
        if name in {"requestId", "commandId"}:
            try:
                uuid.UUID(value)
            except ValueError as exc:
                raise GatewayError("INVALID_ARGUMENT", f"{name} must be a UUID") from exc
        return value

    @staticmethod
    def _required_int(args: dict[str, Any], name: str) -> int:
        value = args.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise GatewayError("INVALID_ARGUMENT", f"{name} must be an integer")
        return value

    @staticmethod
    def _object(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise GatewayError("MODULE_PROTOCOL_ERROR", "Expected a JSON object", retryable=True)
        return cast(dict[str, Any], value)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
