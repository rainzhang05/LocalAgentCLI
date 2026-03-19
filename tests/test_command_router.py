"""Tests for localagentcli.commands.router."""

from __future__ import annotations

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter


class StubHandler(CommandHandler):
    """Minimal handler for testing."""

    def __init__(self, response: str = "ok"):
        self._response = response

    def execute(self, args: list[str]) -> CommandResult:
        return CommandResult.ok(self._response, data={"args": args})

    def help_text(self) -> str:
        return f"Stub: {self._response}"


class TestCommandResult:
    """Tests for CommandResult."""

    def test_ok(self):
        r = CommandResult.ok("success")
        assert r.success is True
        assert r.message == "success"
        assert r.data is None

    def test_ok_with_data(self):
        r = CommandResult.ok("success", data={"key": "val"})
        assert r.data == {"key": "val"}

    def test_error(self):
        r = CommandResult.error("failed")
        assert r.success is False
        assert r.message == "failed"


class TestCommandRouter:
    """Tests for CommandRouter."""

    def test_register_and_dispatch(self):
        router = CommandRouter()
        router.register("test", StubHandler("handled"))
        result = router.dispatch("test")
        assert result.success
        assert result.message == "handled"

    def test_dispatch_with_args(self):
        router = CommandRouter()
        router.register("cmd", StubHandler())
        result = router.dispatch("cmd arg1 arg2")
        assert result.data["args"] == ["arg1", "arg2"]

    def test_dispatch_subcommand(self):
        router = CommandRouter()
        router.register("group sub", StubHandler("sub handled"))
        result = router.dispatch("group sub extra")
        assert result.success
        assert result.message == "sub handled"
        assert result.data["args"] == ["extra"]

    def test_dispatch_subcommand_over_parent(self):
        router = CommandRouter()
        router.register("group", StubHandler("parent"))
        router.register("group sub", StubHandler("child"))
        result = router.dispatch("group sub")
        assert result.message == "child"

    def test_dispatch_parent_when_no_subcommand_match(self):
        router = CommandRouter()
        router.register("group", StubHandler("parent"))
        router.register("group sub", StubHandler("child"))
        result = router.dispatch("group other")
        assert result.message == "parent"

    def test_dispatch_empty_input(self):
        router = CommandRouter()
        result = router.dispatch("")
        assert not result.success
        assert "Empty command" in result.message

    def test_dispatch_whitespace_only(self):
        router = CommandRouter()
        result = router.dispatch("   ")
        assert not result.success

    def test_dispatch_unknown_command(self):
        router = CommandRouter()
        result = router.dispatch("unknown")
        assert not result.success
        assert "Unknown command" in result.message

    def test_dispatch_suggests_subcommands(self):
        router = CommandRouter()
        router.register("session save", StubHandler())
        router.register("session load", StubHandler())
        result = router.dispatch("session")
        assert not result.success
        assert "subcommand" in result.message
        assert "save" in result.message
        assert "load" in result.message

    def test_get_commands(self):
        router = CommandRouter()
        router.register("a", StubHandler())
        router.register("b", StubHandler())
        cmds = router.get_commands()
        assert "a" in cmds
        assert "b" in cmds

    def test_get_completions(self):
        router = CommandRouter()
        router.register("help", StubHandler())
        router.register("session save", StubHandler())
        router.register("session", StubHandler(), visible_in_menu=False)
        completions = router.get_completions()
        assert "/help" in completions
        assert "/session save" in completions
        assert "/session" not in completions
        # Should be sorted
        assert completions == sorted(completions)
