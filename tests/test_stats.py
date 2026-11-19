"""`/apersona:stats` counts, on this machine, how much he types for every 100 replies of the AI."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "plugin/scripts/apersona_stats.py"


def test_it_counts_his_typing_against_the_replies_he_sees_and_what_he_said_in_another_conversation(
    tmp_path: Path, monkeypatch, capsys
):
    now = datetime.now(timezone.utc)

    def row(kind: str, uuid: str, content: object, minutes: int, **more: object) -> dict[str, object]:
        stamp = (now - timedelta(minutes=minutes)).isoformat()
        message = {"role": kind, "content": content, "id": more.pop("reply", uuid)}
        return {"type": kind, "uuid": uuid, "timestamp": stamp, "message": message, **more}

    human = {"origin": {"kind": "human"}}
    first = [
        row("user", "u1", "先講結果，不要停下來問我", 50, sessionId="a", **human),
        row("assistant", "a1", [{"type": "text", "text": "好，結果是這樣"}], 49, reply="m1"),
        row("assistant", "a2", [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}], 48, reply="m1"),
        row("assistant", "a3", [{"type": "text", "text": "做完了"}], 47, reply="m2"),
        row("assistant", "a4", [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {}}], 46, reply="m3"),
        row("assistant", "side", [{"type": "text", "text": "子代理說的"}], 45, reply="m4", isSidechain=True),
        row(
            "user",
            "task",
            "<task-notification>背景工作做完了</task-notification>",
            44,
            origin={"kind": "task-notification"},
        ),
        row("user", "old", "三個月前打的", 60 * 24 * 90, sessionId="a", **human),
    ]
    second = [
        first[0],  # a continued conversation carries the earlier rows again
        row("user", "u2", "先講結果，不要停下來問我。", 30, sessionId="b", **human),
        row("assistant", "b1", [{"type": "text", "text": "結果如下"}], 29, reply="m5"),
        row("user", "u3", "這一句只講過一次", 28, sessionId="b", **human),
        row("assistant", "b2", [{"type": "text", "text": "收到"}], 27, reply="m6"),
    ]
    project = tmp_path / "projects" / "one"
    project.mkdir(parents=True)
    (project / "a.jsonl").write_text("\n".join(json.dumps(r) for r in first), encoding="utf-8")
    (project / "b.jsonl").write_text("\n".join(json.dumps(r) for r in second), encoding="utf-8")
    (project / "a" / "subagents").mkdir(parents=True)
    (project / "a" / "subagents" / "x.jsonl").write_text(json.dumps(second[1]), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("stats_under_test", SCRIPT)
    stats = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stats)

    days, files = stats.count(now - timedelta(days=30))

    assert files == 2
    assert [sum(day[i] for day in days.values()) for i in range(3)] == [3, 4, 1]
    assert stats.main(["--days", "30"]) == 0
    assert "每 100 則 AI 回覆他打 75.0 則；在別段對話講過的 1 則（33%）" in capsys.readouterr().out

    cache = tmp_path / "apersona" / "stand_in.json"
    cache.parent.mkdir()
    for payload in (
        None,
        {},
        {"stood_in": 4},
        {"stood_in": False, "overturned": 0},
        {"stood_in": 1, "overturned": 2},
    ):
        if payload is not None:
            cache.write_text(json.dumps(payload), encoding="utf-8")
        assert stats.main(["--plugin-data", str(tmp_path)]) == 0
        assert capsys.readouterr().out.endswith("代答：還在累積。\n")
    cache.write_text('{"stood_in":4,"overturned":0}', encoding="utf-8")
    assert stats.main(["--plugin-data", str(tmp_path)]) == 0
    assert "代答 4 次，被他推翻 0 次（0%）" in capsys.readouterr().out
