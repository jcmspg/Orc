"""Docs stay honest: required sections exist, links resolve, mentioned commands are real."""

import argparse
import re
from pathlib import Path

import pytest

from orc import __main__ as cli

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)|!\[[^\]]*\]\(([^)\s]+)\)")


def _headings(path: Path) -> list[str]:
    return [m.group(1).strip().lower() for m in re.finditer(r"^#{1,6}\s+(.+)$", path.read_text(), re.M)]


def test_getting_started_and_troubleshooting_exist():
    heads = _headings(ROOT / "README.md")
    assert any("quick start" in h or "getting started" in h for h in heads)
    assert "troubleshooting" in heads


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(doc: Path):
    text = re.sub(r"```.*?```", "", doc.read_text(), flags=re.S)
    for m in LINK.finditer(text):
        target = m.group(1) or m.group(2)
        if re.match(r"^(\w+:|#)", target):  # http:, mailto:, in-page anchors
            continue
        path = target.split("#", 1)[0]
        assert (doc.parent / path).exists(), f"{doc.name}: broken link {target}"


def _cli_subcommands(monkeypatch) -> set[str]:
    captured: dict[str, argparse.ArgumentParser] = {}
    real = argparse.ArgumentParser.parse_args

    def spy(self, *a, **k):
        captured["p"] = self
        raise SystemExit(0)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", spy)
    with pytest.raises(SystemExit):
        cli.main()
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", real)
    (action,) = [a for a in captured["p"]._actions if isinstance(a, argparse._SubParsersAction)]
    return set(action.choices)


def test_documented_orc_commands_exist(monkeypatch):
    commands = _cli_subcommands(monkeypatch)
    assert {"serve", "build-image"} <= commands
    # only code spans count: prose like "orc peons" is not a command
    code = set()
    for doc in DOCS:
        for span in re.findall(r"`([^`\n]+)`", doc.read_text()):
            code |= set(re.findall(r"(?:^|/)orc ([a-z][a-z-]+)", span))
    assert code, "expected docs to mention orc commands"
    assert code <= commands, f"docs mention unknown commands: {code - commands}"
