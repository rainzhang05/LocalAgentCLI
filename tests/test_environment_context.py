"""Tests for environment context gathering and formatting."""

import os
from datetime import datetime
from pathlib import Path

from localagentcli.session.environment_context import get_environment_context_xml


def test_environment_context_includes_cwd():
    workspace = "/fake/workspace/path"
    xml = get_environment_context_xml(workspace)
    expected = str(Path(workspace).resolve())
    assert f"<cwd>{expected}</cwd>" in xml


def test_environment_context_determines_cwd_if_not_provided():
    xml = get_environment_context_xml()
    cwd = os.getcwd()
    assert f"<cwd>{cwd}</cwd>" in xml


def test_environment_context_includes_shell(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    xml = get_environment_context_xml()
    assert "<shell>/bin/bash</shell>" in xml


def test_environment_context_includes_current_date():
    xml = get_environment_context_xml()
    assert "<current_date>" in xml

    # Contains a reasonable year
    current_year = str(datetime.now().year)
    assert current_year in xml


def test_environment_context_includes_timezone():
    xml = get_environment_context_xml()
    # It should emit a timezone block usually unless running in a weird minimal env
    assert "<timezone>" in xml


def test_environment_context_format():
    xml = get_environment_context_xml("/tmp/test")
    assert xml.startswith("<environment_context>")
    assert xml.endswith("</environment_context>")
    resolved = str(Path("/tmp/test").resolve())
    assert f"<cwd>{resolved}</cwd>" in xml
