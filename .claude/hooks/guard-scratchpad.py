#!/usr/bin/env python3
"""PreToolUse(Write|Edit): refuse a bare filename in the shared session scratchpad.

CLAUDE.md, "Your scratchpad is shared. Namespace it." This has cost the project
twice -- C4 and C14b -- and prose did not prevent the second one.
"""
import json, re, sys

try:
    ev = json.load(sys.stdin)
except Exception:
    sys.exit(0)                      # never break a session on malformed input

path = (ev.get("tool_input") or {}).get("file_path") or ""
if re.search(r"/scratchpad/[^/]+$", path):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "CLAUDE.md: never write a bare filename into the scratchpad root. The "
            "scratchpad is shared by every agent of a pair and a common name "
            "(probe.py, check.py, fixture.py) will collide -- it has twice. Put this "
            "in a subdirectory named for your role and chunk, e.g. "
            "scratchpad/c14b-impl/. Retry with that path."),
    }}))
sys.exit(0)
