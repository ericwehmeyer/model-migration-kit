#!/usr/bin/env python3
"""PreToolUse(Bash): refuse `git checkout --` / `git restore` as a restore mechanism.

CLAUDE.md, "Mutating code you are reviewing": restore from a byte-verified backup.
"""
import json
import re
import sys

try:
    ev = json.load(sys.stdin)
except Exception:
    sys.exit(0)

cmd = (ev.get("tool_input") or {}).get("command") or ""
# Split on the shell operators, so the rule cannot be smuggled past inside a
# compound command.
for part in re.split(r"&&|\|\||;|\|&|\||&|\n", cmd):
    p = part.strip()
    if re.match(r"^git\s+checkout\s+--(\s|$)", p) or re.match(r"^git\s+restore(\s|$)", p):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "CLAUDE.md: restore from a byte-verified backup (copy the file, "
                "compare hashes), never `git checkout --` / `git restore`. Copy the "
                "backup back, compare shasum, then confirm `git status` is clean."),
        }}))
        sys.exit(0)
sys.exit(0)
