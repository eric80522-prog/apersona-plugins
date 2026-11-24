#!/usr/bin/env python3
"""How much he types in Claude Code, counted on this machine: per day, his typed messages for every 100 replies
of the AI, and how many of his messages say again what he typed in another conversation.

Reads the main conversations Claude Code keeps on this machine (`claude_conversations`, also what `/apersona:read`
with no folder sends as they are), prints counts and never text, sends nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apersona_hook import (
    block_text,
    content_blocks,
    marks_origin,
    stand_in_counts,
    transcript_rows,
    typed_text,
)  # noqa: E402
from apersona_ignore import Walk  # noqa: E402

#: two of his messages say the same when this share of their character pairs is common to both
SAME_SHARE = 0.8
#: longer than this is a paste, not a sentence he says again
PASTE_CHARS = 2_000


def claude_conversations() -> tuple[list[Path], int, list[tuple[str, str]]]:
    """The main conversations Claude Code keeps on this machine, how many an `.apersonaignore` there (or the
    always-excluded names) left out, and the folders (in full, why) left out whole because their rule file could not
    be used; a subagent's file lives one folder deeper and is not one."""
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    walk = Walk(root, {".jsonl"})
    unread = [(str(root if folder == "." else root / folder), why) for folder, why in walk.rules_unread]
    return [root / relative for relative in walk.files if relative.count("/") == 1], walk.left_out, unread


def conversations(since: datetime) -> list[Path]:
    "The main conversations written to since then."
    return [path for path in claude_conversations()[0] if path.stat().st_mtime >= since.timestamp()]


def written_at(row: dict[str, object]) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(row.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else None


def reply_id(row: dict[str, object]) -> str:
    "The id of the reply a row belongs to when he can see words in it; one reply is written as several rows."
    message = row.get("message")
    if row.get("type") != "assistant" or row.get("isSidechain") or row.get("isApiErrorMessage"):
        return ""
    if not isinstance(message, dict) or not block_text(content_blocks(row)).strip():
        return ""
    return str(cast("dict[str, object]", message).get("id") or row.get("uuid") or "")


def pairs(text: str) -> frozenset[str]:
    letters = "".join(text.split()).lower()
    return frozenset(letters[i : i + 2] for i in range(len(letters) - 1)) or frozenset({letters})


def messages(since: datetime) -> tuple[list[tuple[datetime, str, str, str, str]], int]:
    """Each typed message of his and each row of a reply he can see words in, once: (when, conversation, role,
    text, the reply's id or "" for his), and how many conversations were read."""
    seen: set[str] = set()
    out: list[tuple[datetime, str, str, str, str]] = []
    files = conversations(since)
    for path in files:
        with path.open("rb") as stream:
            rows = transcript_rows(stream.read().decode("utf-8", errors="replace").split("\n"))
        marked = marks_origin(rows)
        for row in rows:
            stamp = written_at(row)
            if stamp is None or stamp < since:
                continue
            text, reply = typed_text(row, marked), reply_id(row)
            key = str(row.get("uuid") or "")
            if not (text or reply) or not key or key in seen:  # a continued conversation carries earlier rows again
                continue
            seen.add(key)
            session = str(row.get("sessionId") or path.stem)
            if text:
                out.append((stamp, session, "user", text, ""))
            else:
                out.append((stamp, session, "assistant", block_text(content_blocks(row)).strip(), reply))
    return sorted(out), len(files)


def count(since: datetime) -> tuple[dict[str, list[int]], int]:
    "Per local day [his messages, AI replies, his repeats], and how many conversations were read."
    replies: set[str] = set()
    typed: list[tuple[datetime, str, str]] = []
    days: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    said, files = messages(since)
    for stamp, session, role, text, reply in said:
        day = stamp.astimezone().date().isoformat()
        if role == "user":
            days[day][0] += 1
            typed.append((stamp, session, text))
        elif reply not in replies:  # one reply is written as several rows
            replies.add(reply)
            days[day][1] += 1
    earlier: list[tuple[str, frozenset[str]]] = []
    for stamp, session, text in sorted(typed):
        if len(text) > PASTE_CHARS:
            continue
        mine = pairs(text)
        if any(
            s != session
            and SAME_SHARE * len(mine) <= len(other) <= len(mine) / SAME_SHARE  # sizes this far apart cannot match
            and len(mine & other) >= SAME_SHARE * len(mine | other)
            for s, other in earlier
        ):
            days[stamp.astimezone().date().isoformat()][2] += 1
        earlier.append((session, mine))
    return dict(days), files


def per_hundred(his: int, replies: int) -> str:
    return f"{100 * his / replies:.1f}" if replies else "—"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--plugin-data", type=Path, help="the plugin's data folder: adds the 代答 counts kept there")
    args = parser.parse_args(argv)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)  # noqa: UP017
    days, files = count(since)
    print("日期\t他打\tAI回覆\t每100則AI回覆他打\t重複講過的")
    for day in sorted(days):
        his, replies, again = days[day]
        print(f"{day}\t{his}\t{replies}\t{per_hundred(his, replies)}\t{again}")
    his, replies, again = (sum(day[i] for day in days.values()) for i in range(3))
    share = f"{100 * again / his:.0f}%" if his else "—"
    print(
        f"合計 {args.days} 天、{files} 段對話：他打 {his} 則，AI 回覆 {replies} 則，"
        f"每 100 則 AI 回覆他打 {per_hundred(his, replies)} 則；在別段對話講過的 {again} 則（{share}）。"
    )
    if args.plugin_data is not None:
        try:
            kept = json.loads((args.plugin_data / "apersona" / "stand_in.json").read_text(encoding="utf-8"))
        except OSError:  # separate clauses: the formatter would drop the parentheses python 3.9 needs
            kept = {}
        except ValueError:
            kept = {}
        counts = cast("dict[str, object]", kept) if isinstance(kept, dict) else {}
        totals = stand_in_counts(counts.get("stood_in"), counts.get("overturned"))
        if totals is None or totals[0] == 0:
            print("代答：還在累積。")
        else:
            given, taken_back = totals
            print(f"代答 {given} 次，被他推翻 {taken_back} 次（{100 * taken_back / given:.0f}%）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
