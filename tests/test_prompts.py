import pytest

from orc import prompts
from orc.board import Board


def test_extract_json_tolerates_fences_and_chatter():
    assert prompts.extract_json('Sure!\n```json\n{"a": 1}\n```\nDone') == {"a": 1}


def test_extract_json_rejects_text_without_object():
    with pytest.raises(ValueError):
        prompts.extract_json("no json here")


def test_parse_plan_matches_workers_case_insensitively_and_drops_unknown():
    reply = '{"tasks": [{"worker": "grunk", "task": " do a "}, {"worker": "nobody", "task": "x"}, {"worker": "Uzgash", "task": ""}]}'
    assert prompts.parse_plan(reply, ["Grunk", "Uzgash"]) == [("Grunk", "do a")]


def test_parse_plan_errors_when_nothing_assigned():
    with pytest.raises(ValueError):
        prompts.parse_plan('{"tasks": [{"worker": "x", "task": "y"}]}', ["Grunk"])


def test_parse_review_approve_and_findings():
    v = prompts.parse_review('{"approve": false, "findings": ["bug", "  ", "other"]}')
    assert not v.approve and v.findings == ["bug", "other"]
    assert prompts.parse_review('{"approve": true, "findings": []}').approve


def test_code_prompt_focus_depends_on_role():
    assert "Write or improve tests" in prompts.code("U", "tester", "t")
    assert "Implement this." in prompts.code("G", "coder", "t")


def test_fix_prompt_handles_empty_findings():
    assert "(no details given)" in prompts.fix("G", "t", [])
    assert "- missing test" in prompts.fix("G", "t", ["missing test"])


def test_board_apply_and_snapshot_roundtrip():
    b = Board()
    orc = {"id": "o1", "name": "Grunk", "role": "coder", "backend": "claude", "autonomy": False, "hut": {"x": 0, "z": 0}}
    b.apply({"type": "orc.spawned", "orc": orc})
    b.apply({"type": "orc.state", "id": "o1", "state": "working", "task": "t"})
    b.apply({"type": "gold.banked", "amount": 5})
    b.apply({"type": "orc.awaiting", "id": "o1", "pending": {"k": 1}})
    b.apply({"type": "orc.resolved", "id": "o1"})
    assert b.orcs["o1"].state == "awaiting" and b.orcs["o1"].pending is None
    b2 = Board()
    for e in b.snapshot():
        b2.apply(e)
    assert b2.gold == 5 and b2.orcs["o1"].task == "t"
    assert list(b2.orcs) == ["o1"]


def test_log_is_capped_and_unknown_orc_ignored():
    b = Board()
    b.apply({"type": "orc.log", "id": "ghost", "line": "x"})
    b.apply({"type": "orc.spawned", "orc": {"id": "o", "name": "n", "role": "coder", "backend": "c", "autonomy": True, "hut": {}}})
    for i in range(250):
        b.apply({"type": "orc.log", "id": "o", "line": str(i)})
    assert len(b.orcs["o"].log) == 200 and b.orcs["o"].log[-1]["text"] == "249"
