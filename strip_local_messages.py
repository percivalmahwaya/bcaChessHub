"""
Remove per-page Django message blocks, now that base.html renders them once.

WHY
===
Nine of the forty-one templates rendered messages themselves, each with its own
copy of a Bootstrap alert:

    {% for msg in messages %}
    <div class="alert alert-{{ msg.tags }} ...">{{ msg }}...</div>
    {% endfor %}

Every other page had none, so any view that set a message and redirected to one
of the other thirty-two dropped it in silence.

base.html now renders them once for the whole site. That fixes the thirty-two
and breaks the nine, which would show every message twice. This removes the
local copies.

Run once. Kept in the repo because it documents what was removed and why, and
because --check makes it a standing assertion that no page has grown its own
message block again.

    python strip_local_messages.py --check    # report, change nothing
    python strip_local_messages.py --apply
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"
BASE = TEMPLATES / "base.html"

# The loop, its body, and the endfor. Non-greedy so it stops at the first
# endfor, and anchored on the whole line so surrounding indentation goes too.
BLOCK = re.compile(
    r"[ \t]*\{%\s*for\s+msg\s+in\s+messages\s*%\}.*?\{%\s*endfor\s*%\}[ \t]*\n?",
    re.S)


def main():
    apply_it = "--apply" in sys.argv
    if not apply_it and "--check" not in sys.argv:
        print(__doc__)
        return 1

    hits = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        if path == BASE:
            continue
        text = path.read_text(encoding="utf-8")
        matches = BLOCK.findall(text)
        if not matches:
            continue
        hits.append((path, len(matches)))
        if apply_it:
            path.write_text(BLOCK.sub("", text), encoding="utf-8")

    rel = lambda p: p.relative_to(ROOT).as_posix()
    if not hits:
        print("  No template renders its own message block. base.html has it.")
        return 0

    for path, n in hits:
        verb = "stripped" if apply_it else "would strip"
        print(f"  {verb} {n} block(s)  {rel(path)}")
    print(f"\n  {len(hits)} template(s).")

    if not apply_it:
        print("  Every one of these would render each message TWICE, because "
              "base.html now renders them site-wide.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
