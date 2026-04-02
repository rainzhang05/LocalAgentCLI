"""Tests for guardian approval reviewer behavior."""

from __future__ import annotations

import pytest

from localagentcli.guardian.reviewer import (
    GuardianReviewRequest,
    areview_with_guardian,
    review_with_guardian,
)


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeModel:
    def __init__(self, response_text: str = ""):
        self._response_text = response_text
        self.calls: list[tuple[list, dict]] = []

    def generate(self, messages: list, **kwargs):
        self.calls.append((messages, kwargs))
        return _FakeResponse(self._response_text)

    async def agenerate(self, messages: list, **kwargs):
        self.calls.append((messages, kwargs))
        return _FakeResponse(self._response_text)


class _FailingModel:
    def generate(self, messages: list, **kwargs):
        raise RuntimeError("boom")

    async def agenerate(self, messages: list, **kwargs):
        raise RuntimeError("boom")


def _request() -> GuardianReviewRequest:
    return GuardianReviewRequest(
        tool_name="file_write",
        arguments={"path": "out.txt", "content": "hello"},
        risk_level="high",
        risk_reason="mutating write",
        warnings=["writes file"],
        task="create output",
        step_index=1,
        step_description="write output file",
    )


def test_review_with_guardian_allows_low_score():
    model = _FakeModel(
        '{"risk_level":"low","risk_score":15,"rationale":"Scoped workspace write.",'
        '"evidence":[{"fact":"single path in workspace","source":"request"}]}'
    )

    result = review_with_guardian(model, _request())

    assert result.approved is True
    assert result.risk_score == 15
    assert result.failure == ""


def test_review_with_guardian_denies_high_score():
    model = _FakeModel(
        '{"risk_level":"high","risk_score":90,"rationale":"Potentially destructive.",'
        '"evidence":[{"fact":"high-risk command pattern","source":"request"}]}'
    )

    result = review_with_guardian(model, _request())

    assert result.approved is False
    assert result.risk_level == "high"
    assert result.risk_score == 90


def test_review_with_guardian_fail_closed_on_invalid_json():
    model = _FakeModel("not-json")

    result = review_with_guardian(model, _request())

    assert result.approved is False
    assert result.failure.startswith("guardian parse error:")
    assert result.risk_level == "high"
    assert result.risk_score == 100


def test_review_with_guardian_fail_closed_on_model_error():
    result = review_with_guardian(_FailingModel(), _request())

    assert result.approved is False
    assert result.failure.startswith("guardian model error:")


@pytest.mark.asyncio
async def test_areview_with_guardian_uses_async_path():
    model = _FakeModel(
        '{"risk_level":"medium","risk_score":45,"rationale":"Needs caution.",'
        '"evidence":[{"fact":"mutating tool","source":"request"}]}'
    )

    result = await areview_with_guardian(model, _request())

    assert result.approved is True
    assert result.risk_level == "medium"
    assert model.calls
