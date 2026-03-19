"""Tests for Hugging Face API-backed model discovery."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx

from localagentcli.models.hf_catalog import HuggingFaceCatalog


def _mock_response(data: object, status_code: int = 200) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = data
    response.text = json.dumps(data)
    response.raise_for_status.return_value = None
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=response,
        )
    return response


class TestHuggingFaceCatalogFamilies:
    def test_lists_many_families_for_backend(self):
        catalog = HuggingFaceCatalog(client=MagicMock())

        families = catalog.list_families("gguf")

        keys = {family.key for family in families}
        assert len(families) >= 10
        assert {"qwen", "llama", "gemma", "mistral", "phi"} <= keys


class TestHuggingFaceCatalogModels:
    def test_discovers_models_from_hf_api(self):
        client = MagicMock()
        client.get.side_effect = [
            _mock_response(
                [
                    {
                        "id": "unsloth/Qwen3-14B-GGUF",
                        "downloads": 5000,
                        "likes": 200,
                        "tags": ["gguf", "qwen"],
                        "pipeline_tag": "text-generation",
                    },
                    {
                        "id": "mlx-community/Qwen3-8B-4bit",
                        "downloads": 9000,
                        "likes": 500,
                        "tags": ["mlx", "qwen"],
                        "pipeline_tag": "text-generation",
                    },
                ]
            ),
            _mock_response(
                [
                    {
                        "id": "bartowski/Qwen3-8B-GGUF",
                        "downloads": 7000,
                        "likes": 250,
                        "tags": ["gguf", "qwen"],
                        "pipeline_tag": "text-generation",
                    }
                ]
            ),
        ]
        catalog = HuggingFaceCatalog(client=client)

        models = catalog.list_models("gguf", "qwen")

        assert [model.repo for model in models] == [
            "bartowski/Qwen3-8B-GGUF",
            "unsloth/Qwen3-14B-GGUF",
        ]
        assert models[0].install_name == "bartowski-qwen3-8b-gguf"
        assert "downloads" in models[0].summary

    def test_caches_discovered_models(self):
        client = MagicMock()
        client.get.side_effect = [
            _mock_response(
                [
                    {
                        "id": "openai/gpt-oss-20b",
                        "downloads": 100,
                        "likes": 10,
                        "tags": ["text-generation"],
                        "pipeline_tag": "text-generation",
                    }
                ]
            ),
            _mock_response([]),
        ]
        catalog = HuggingFaceCatalog(client=client)

        first = catalog.list_models("safetensors", "gpt-oss")
        second = catalog.list_models("safetensors", "gpt-oss")

        assert first == second
        assert client.get.call_count == 2
