"""Antigravity guidance only: no network, credentials, transcript reads or capture."""

import json
import sys
from typing import cast

INPUT_ERRORS = (ValueError, OSError)

REMINDER = (
    "Apersona: if this chat has no complete opening packet, call this plugin's "
    "opening tool and follow its cursors; retain the returned conversation_id. "
    "If opening is already complete, do not fetch it again. For the current task, "
    "use lookup or recall when relevant evidence is needed, following their schemas. "
    "Only acknowledge complete replies actually received through this connection. "
    "This is tool guidance, not the owner's opening packet or a capture record."
)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except INPUT_ERRORS:
        print("{}")
        return
    if not isinstance(payload, dict):
        print("{}")
        return
    conversation = cast(dict[str, object], payload).get("conversationId")
    if not isinstance(conversation, str) or not conversation.strip():
        print("{}")
        return
    print(json.dumps({"injectSteps": [{"ephemeralMessage": REMINDER}]}))


if __name__ == "__main__":
    main()
