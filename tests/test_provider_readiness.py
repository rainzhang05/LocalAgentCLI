"""Tests for provider readiness helpers (sync + async discovery)."""

from __future__ import annotations

from localagentcli.models.model_info import ModelInfo
from localagentcli.models.provider_readiness import (
    aresolve_remote_model_readiness,
    resolve_remote_model_readiness,
)
from tests.test_provider_base import StubRemoteProvider


def _caps() -> dict:
    return {"tool_use": {"supported": True, "reason": ""}, "reasoning": {}, "streaming": {}}


class _EmptyDiscovery(StubRemoteProvider):
    def list_models(self) -> list[ModelInfo]:
        return []

    async def alist_models(self) -> list[ModelInfo]:
        return []


class _OneModel(StubRemoteProvider):
    def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="m1",
                name="One",
                capabilities=_caps(),
                selection_state="api_discovered",
            )
        ]

    async def alist_models(self) -> list[ModelInfo]:
        return self.list_models()


class TestResolveRemoteModelReadiness:
    def test_empty_model_name(self):
        p = _EmptyDiscovery(name="p", base_url="http://x", api_key="k", default_model="m")
        r = resolve_remote_model_readiness(p, "  ")
        assert r.selection_state == "model_unselected"

    def test_model_found_by_id(self):
        p = _OneModel(name="p", base_url="http://x", api_key="k", default_model="m")
        r = resolve_remote_model_readiness(p, "m1")
        assert r.selection_state == "api_discovered"

    def test_model_not_in_discovery(self):
        p = _EmptyDiscovery(name="p", base_url="http://x", api_key="k", default_model="m")
        r = resolve_remote_model_readiness(p, "missing")
        assert r.selection_state == "unknown"


class TestAresolveRemoteModelReadiness:
    async def test_empty_model_name(self):
        p = _EmptyDiscovery(name="p", base_url="http://x", api_key="k", default_model="m")
        r = await aresolve_remote_model_readiness(p, "")
        assert r.selection_state == "model_unselected"

    async def test_model_found_async(self):
        p = _OneModel(name="p", base_url="http://x", api_key="k", default_model="m")
        r = await aresolve_remote_model_readiness(p, "m1")
        assert r.selection_state == "api_discovered"

    async def test_unknown_when_missing(self):
        p = _EmptyDiscovery(name="p", base_url="http://x", api_key="k", default_model="m")
        r = await aresolve_remote_model_readiness(p, "nope")
        assert r.selection_state == "unknown"
