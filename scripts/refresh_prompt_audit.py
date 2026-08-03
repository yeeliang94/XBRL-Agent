"""Re-sync the verbatim prompt excerpts in `docs/agent-prompt-audit.html`.

The audit page quotes several prompt bodies "verbatim" inside `<pre>` blocks,
with a `N lines` count in the summary. Nothing kept them in step with the
files, so they drifted: at the 2026-08-02 peer review all four were stale —
`_notes_base.md` was quoting 320 lines against a 460-line file, and the
`_base.md` excerpt still showed the MFRS-only persona that had just been made
standard-neutral. A reader trusting the word "verbatim" was reading fiction.

Run this after editing any quoted prompt:

    python scripts/refresh_prompt_audit.py

`tests/test_prompt_audit_matches_live.py` fails the build when they diverge,
so this script is the fix, not a nicety.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "docs" / "agent-prompt-audit.html"
PROMPTS = ROOT / "prompts"

# <summary>…<code>prompts/X.md</code> (verbatim)…<span class="meta">N lines · REST
# </span></summary><pre …>BODY</pre>
BLOCK = re.compile(
    r'(?P<head><summary><span>[^<]*<code>(?:prompts/)?'
    r'(?P<name>[a-z0-9_]+\.md)</code>\s*\(verbatim\)</span>'
    r'<span class="meta">)(?P<count>\d+)(?P<mid> lines[^<]*</span></summary>'
    r'<pre[^>]*>)(?P<body>.*?)(?P<tail></pre>)',
    re.S,
)


def refresh(text: str) -> tuple[str, list[str]]:
    changed: list[str] = []

    def replace(m: re.Match) -> str:
        name = m.group("name")
        path = PROMPTS / name
        if not path.exists():
            # A deleted prompt is the audit's own problem to describe; leave
            # the historical record alone rather than blanking it.
            return m.group(0)
        live = path.read_text(encoding="utf-8").strip()
        escaped = html.escape(live, quote=False)
        if escaped == m.group("body").strip() and m.group("count") == str(
            len(live.splitlines())
        ):
            return m.group(0)
        changed.append(name)
        return (
            m.group("head")
            + str(len(live.splitlines()))
            + m.group("mid")
            + escaped
            + m.group("tail")
        )

    return BLOCK.sub(replace, text), changed


def main() -> int:
    text = AUDIT.read_text(encoding="utf-8")
    updated, changed = refresh(text)
    if not changed:
        print("Audit excerpts already match the live prompts.")
        return 0
    AUDIT.write_text(updated, encoding="utf-8")
    for name in changed:
        print(f"refreshed {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
