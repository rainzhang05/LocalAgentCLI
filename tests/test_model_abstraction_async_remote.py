"""ModelAbstractionLayer async streaming against a remote (provider) backend."""

from __future__ import annotations

import pytest

from localagentcli.models.abstraction import ModelAbstractionLayer
from localagentcli.models.backends.base import ModelMessage
from tests.test_provider_base import StubRemoteProvider


@pytest.mark.asyncio
async def test_astream_generate_remote_delegates_to_provider():
    layer = ModelAbstractionLayer(
        StubRemoteProvider(name="t", base_url="http://x", api_key="k", default_model="m")
    )
    messages = [ModelMessage(role="user", content="hi")]
    chunks = [c async for c in layer.astream_generate(messages)]
    texts = [c.text for c in chunks if c.text]
    assert "chunk" in texts
