"""Tests for runtime protocol serialization helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from localagentcli.models.backends.base import StreamChunk
from localagentcli.runtime.protocol import (
    RuntimeEvent,
    Submission,
    UserTurnOp,
    serialize_runtime_value,
)


def test_serialize_primitives():
    assert serialize_runtime_value(None) is None
    assert serialize_runtime_value(3) == 3
    assert serialize_runtime_value(True) is True


def test_serialize_datetime():
    dt = datetime(2024, 1, 2, 3, 4, 5)
    assert serialize_runtime_value(dt) == dt.isoformat()


def test_serialize_stream_chunk():
    ch = StreamChunk(text="x", is_done=True)
    assert serialize_runtime_value(ch) == ch.to_dict()


def test_serialize_dict_and_list():
    assert serialize_runtime_value({"a": 1}) == {"a": 1}
    assert serialize_runtime_value([1, "b"]) == [1, "b"]


def test_serialize_fallback_repr():
    class _X:
        def __repr__(self) -> str:
            return "xrepr"

    assert serialize_runtime_value(_X()) == "xrepr"


def test_submission_to_dict_roundtrip_keys():
    s = Submission(op=UserTurnOp(prompt="p"))
    d = s.to_dict()
    assert "id" in d and "timestamp" in d and "op" in d


def test_runtime_event_is_terminal():
    e = RuntimeEvent(type="turn_completed", submission_id="s")
    assert e.is_terminal is True
    e2 = RuntimeEvent(type="stream_chunk", submission_id="s")
    assert e2.is_terminal is False


@dataclass
class _Plain:
    a: int


def test_serialize_plain_dataclass():
    out = serialize_runtime_value(_Plain(a=2))
    assert isinstance(out, dict)
    assert out.get("a") == 2


def test_nested_dict_with_chunk():
    out = serialize_runtime_value({"inner": StreamChunk(text="z")})
    assert isinstance(out["inner"], dict)


def test_list_of_datetimes():
    dt = datetime(2020, 5, 1)
    out = serialize_runtime_value([dt, {"k": dt}])
    assert isinstance(out[0], str)
    assert isinstance(out[1]["k"], str)


def test_runtime_event_to_dict_with_data():
    ev = RuntimeEvent(
        type="stream_chunk",
        submission_id="sid",
        data=StreamChunk(text="t"),
        message="m",
    )
    d = ev.to_dict()
    assert d["type"] == "stream_chunk"
    assert "data" in d


def test_tuple_serialized_as_list():
    assert serialize_runtime_value((1, 2)) == [1, 2]


def test_serialize_user_turn_op():
    op = UserTurnOp(prompt="hello", mode="chat", approval_policy="deny")
    ser = serialize_runtime_value(op)
    assert isinstance(ser, dict)
    assert ser.get("prompt") == "hello"
