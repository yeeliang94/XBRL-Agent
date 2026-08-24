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

ASSEMBLED_EXAMPLES = re.compile(
    r'<section id="ex-face">.*?</section><section id="ex-notes">.*?</section>',
    re.S,
)

ASSEMBLED_REPLACEMENT = (
    '<section id="runtime-prompts"><h2>Runtime-assembled prompts</h2>'
    '<p>Runtime prompts are intentionally not copied into this document. They '
    'contain live template catalogs and source-specific data, so a pasted '
    'snapshot becomes stale as soon as either input changes. Inspect the '
    'agent trace for the exact prompt used by a run; traces also record model, '
    'provider, transport, configured reasoning effort, and effective reasoning '
    'effort. The mechanically synchronized <code>(verbatim)</code> blocks above '
    'remain the static instruction source of truth.</p></section>'
)

COPY_REPLACEMENTS = (
    (
        '<a href="#ex-face">★ Full example: Face</a>\n'
        '<a href="#ex-notes">★ Full example: Notes</a>\n',
        '<a href="#runtime-prompts">Runtime prompts</a>\n',
    ),
    (
        'Separate vision prompts exist for TOC extraction and scanned-PDF '
        'notes discovery.',
        'Separate helper prompts handle TOC vision, scanned-note discovery, '
        'page calibration, and scanned-PDF source transcription; all are '
        'listed in the matrix above.',
    ),
    (
        'A bird\'s-eye view of the system prompt each agent receives, with the '
        'agent-specific instructions, tools, and settings inputs. Blocks '
        'labelled <b>verbatim</b> are mechanically synchronized with the live '
        '<code>prompts/</code> files. The two assembled examples are illustrative '
        'snapshots and are not the source of truth; runtime prompts also '
        'include source-specific data and live template catalogs.',
        'A bird\'s-eye view of every agent prompt in the pipeline, including '
        'helper vision/transcription roles. Blocks labelled <b>verbatim</b> '
        'are mechanically synchronized with the live <code>prompts/</code> '
        'files. Runtime prompts include source-specific data and live template '
        'catalogs, so the exact assembled prompt belongs in the run trace, not '
        'in a copied documentation snapshot.',
    ),
    (
        'Generated from the live repository on the current branch. Static '
        '<code>.md</code> bodies are read verbatim; the two ★ examples are '
        'produced by calling the real prompt-assembly functions, so they match '
        'what an agent actually receives. Re-run the generator after editing '
        'any <code>prompts/*.md</code> or the assembly code to refresh.',
        'Generated from the live repository on the current branch. Static '
        '<code>.md</code> bodies marked verbatim are synchronized by '
        '<code>scripts/refresh_prompt_audit.py</code> and pinned by tests. '
        'Runtime-assembled prompts are retained in per-run traces.',
    ),
)


def refresh(text: str) -> tuple[str, list[str]]:
    changed: list[str] = []

    for old, new in COPY_REPLACEMENTS:
        if old in text:
            text = text.replace(old, new)
            changed.append("audit copy")

    text, example_count = ASSEMBLED_EXAMPLES.subn(ASSEMBLED_REPLACEMENT, text)
    if example_count:
        changed.append("runtime example policy")

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
