"""`/hf-token` command handler."""

from __future__ import annotations

import os

from rich.prompt import Prompt

from localagentcli.commands.router import CommandHandler, CommandResult, CommandRouter
from localagentcli.providers.keys import KeyManager
from localagentcli.shell.prompt import supports_interactive_prompt

HF_TOKEN_KEY_NAME = "hf_token"
HF_TOKEN_ENV_NAMES = (
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "HUGGINGFACEHUB_API_TOKEN",
)


def restore_hf_token_environment(key_manager: KeyManager) -> str | None:
    """Populate HF token environment variables from secure storage when available."""
    token = None
    for name in HF_TOKEN_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            token = value
            break
    if token is None:
        token = key_manager.retrieve_key(HF_TOKEN_KEY_NAME)
    if not token:
        return None
    _set_hf_token_environment(token)
    return token


class HFTokenHandler(CommandHandler):
    """Store the Hugging Face access token used by model discovery and downloads."""

    def __init__(self, key_manager: KeyManager):
        self._key_manager = key_manager

    def execute(self, args: list[str]) -> CommandResult:
        token = " ".join(args).strip()
        if not token:
            if not supports_interactive_prompt():
                return CommandResult.error("Usage: /hf-token <token>")
            try:
                token = Prompt.ask("Hugging Face token", password=True)
            except (KeyboardInterrupt, EOFError):
                return CommandResult.ok("HF token setup cancelled.")
        if not token:
            return CommandResult.error("A Hugging Face token is required.")

        self._key_manager.store_key(HF_TOKEN_KEY_NAME, token)
        _set_hf_token_environment(token)
        return CommandResult.ok("HF token saved.")

    def help_text(self) -> str:
        return (
            "Store or replace the Hugging Face token used for model discovery and downloads.\n"
            "Usage: /hf-token [token]"
        )


def register(router: CommandRouter, key_manager: KeyManager) -> None:
    """Register the /hf-token command."""
    router.register("hf-token", HFTokenHandler(key_manager))


def _set_hf_token_environment(token: str) -> None:
    """Apply one token value consistently across supported HF env vars."""
    for name in HF_TOKEN_ENV_NAMES:
        os.environ[name] = token
