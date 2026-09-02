"""Presentation layer: design tokens and HTML component builders.

**Visual language adapted from obvious.ai**, measured from the live site rather
than eyeballed: a warm bone canvas, near-black ink, a single deep forest-green
accent, hairline borders, 16px cards and pill controls. Palette and proportions
only — no logo, no wordmark, and nothing claiming affiliation.

Their typeface (Booton) is licensed and not ours to redistribute, so Figtree
stands in for it: a geometric sans with comparable warmth, loaded through
Streamlit's native font setting in `.streamlit/config.toml`.

The same tokens live in that config file, so Streamlit themes its own widgets
natively — borders, radii, focus rings, alerts — and the CSS below only styles
the custom blocks.

Every function is a pure `data -> html string` transform. Nothing here reads
state or calls the compiler.
"""

from __future__ import annotations

import html
from typing import List, Sequence, Tuple

from core.ears import PATTERN_TEMPLATES

# --- surfaces -------------------------------------------------------------- #
CANVAS = "#f7f6f2"       # page
SURFACE = "#ffffff"      # cards, inputs
SURFACE_SUBTLE = "#edece8"
LINE = "#e3e2de"         # hairline
LINE_STRONG = "#d6d4ce"

# --- ink ------------------------------------------------------------------- #
INK = "#1c1d1b"
INK_MUTED = "#605d58"
INK_FAINT = "#89857f"

# --- accent (verified / primary) ------------------------------------------- #
ACCENT = "#3d5638"
ACCENT_HOVER = "#33482f"
ACCENT_ON = "#ffffff"    # foreground on an accent fill
ACCENT_TINT = "#eef2ed"
ACCENT_LINE = "#c7d3c4"

# --- warn (assumed default) ------------------------------------------------ #
WARN = "#8a6a1f"
WARN_TINT = "#faf4e6"
WARN_LINE = "#e6dcc2"

# --- danger (blocking) ----------------------------------------------------- #
DANGER = "#9b3a2b"
DANGER_TINT = "#fbeeeb"
DANGER_LINE = "#ecd3cd"

EARS_TEMPLATES: List[str] = list(PATTERN_TEMPLATES.values())


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


CSS = f"""
<style>
:root {{
    --canvas: {CANVAS}; --surface: {SURFACE}; --surface-subtle: {SURFACE_SUBTLE};
    --line: {LINE}; --line-strong: {LINE_STRONG};
    --ink: {INK}; --ink-muted: {INK_MUTED}; --ink-faint: {INK_FAINT};
    --accent: {ACCENT}; --accent-hover: {ACCENT_HOVER}; --accent-on: {ACCENT_ON};
    --accent-tint: {ACCENT_TINT}; --accent-line: {ACCENT_LINE};
    --warn: {WARN}; --warn-tint: {WARN_TINT}; --warn-line: {WARN_LINE};
    --danger: {DANGER}; --danger-tint: {DANGER_TINT}; --danger-line: {DANGER_LINE};
    --r-card: 16px; --r-control: 12px;
}}

header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
[data-testid="stToolbar"], [data-testid="stDecoration"] {{ display: none; }}

/* One centred column. Line length is the whole point. */
.block-container {{ max-width: 780px; padding: 2.4rem 1.5rem 6rem; margin: 0 auto; }}
p, li {{ line-height: 1.62; }}

/* ---- rhythm ---------------------------------------------------------- */
.ob-step {{ color: var(--ink-faint); font-size: 0.78rem; margin-bottom: 0.5rem; }}
.ob-title {{
    font-size: 2rem; font-weight: 600; letter-spacing: -0.02em;
    line-height: 1.15; margin: 0 0 0.6rem; color: var(--ink);
}}
.ob-lede {{
    color: var(--ink-muted); font-size: 1rem; line-height: 1.6;
    margin: 0 0 1.8rem; max-width: 62ch;
}}
.ob-quiet {{ color: var(--ink-muted); font-size: 0.88rem; line-height: 1.55; }}
.ob-mono {{
    font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 0.85rem;
}}
.ob-rule {{ height: 1px; background: var(--line); margin: 2rem 0 1.6rem; }}

.ob-progress {{ display: flex; gap: 5px; margin-bottom: 1.6rem; }}
.ob-progress span {{
    height: 3px; flex: 1; border-radius: 999px; background: var(--line);
}}
.ob-progress span.is-done {{ background: var(--accent); }}
.ob-progress span.is-now {{ background: var(--accent); opacity: 0.4; }}

.ob-strip {{
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 1.8rem; font-size: 0.8rem; color: var(--ink-muted);
}}
.ob-brand {{ font-weight: 600; color: var(--ink); letter-spacing: -0.01em; }}
.ob-conn {{ display: inline-flex; align-items: center; gap: 0.4rem; }}
.ob-dot {{ width: 7px; height: 7px; border-radius: 50%; display: inline-block; }}

/* ---- blocks ---------------------------------------------------------- */
.ob-item {{
    background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--r-card); padding: 1.05rem 1.15rem; margin-bottom: 0.7rem;
}}
.ob-quote {{
    font-size: 1rem; line-height: 1.55; color: var(--ink);
    border-left: 2px solid var(--accent); padding-left: 0.9rem;
}}
.ob-quote.is-ungrounded {{ border-left-color: var(--danger); }}
.ob-meta {{
    color: var(--ink-faint); font-size: 0.78rem; margin-top: 0.7rem;
    display: flex; gap: 0.55rem; flex-wrap: wrap; align-items: center;
}}
.ob-meta .ob-sep {{ opacity: 0.5; }}

.ob-verdict {{
    display: flex; gap: 1rem; align-items: flex-start;
    background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--r-card); padding: 1.2rem 1.3rem; margin-bottom: 1.4rem;
}}
.ob-verdict.is-pass {{ border-color: var(--accent-line); background: var(--accent-tint); }}
.ob-verdict.is-fail {{ border-color: var(--danger-line); background: var(--danger-tint); }}
.ob-verdict-mark {{ font-size: 1.35rem; line-height: 1.3; }}
.ob-verdict-title {{ font-size: 1.05rem; font-weight: 600; margin-bottom: 0.3rem; }}

.ob-stats {{ color: var(--ink-muted); font-size: 0.92rem; margin-bottom: 1.6rem; }}
.ob-stats b {{ color: var(--ink); font-weight: 600; }}

.ob-meter {{ margin-bottom: 1rem; }}
.ob-meter-head {{
    display: flex; justify-content: space-between; align-items: baseline;
    font-size: 0.88rem; margin-bottom: 0.4rem;
}}
.ob-meter-head span:last-child {{ color: var(--ink-faint); font-size: 0.82rem; }}
.ob-meter-track {{
    height: 5px; border-radius: 999px; background: var(--surface-subtle);
    overflow: hidden;
}}
.ob-meter-fill {{ height: 100%; border-radius: 999px; }}

.ob-answer {{
    border-left: 2px solid var(--warn); background: var(--warn-tint);
    padding: 0.75rem 0.9rem; border-radius: 0 var(--r-control) var(--r-control) 0;
    font-size: 0.9rem; margin: 1rem 0;
}}
.ob-answer b {{ color: var(--warn); }}
.ob-notice {{
    border: 1px solid var(--warn-line); background: var(--warn-tint);
    border-radius: var(--r-control); padding: 0.9rem 1.05rem;
    margin-bottom: 1.5rem; font-size: 0.9rem;
}}
.ob-ears {{
    font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 0.87rem;
    line-height: 1.6; padding: 0.7rem 0.9rem; margin-bottom: 0.6rem;
    background: var(--surface-subtle); border-radius: var(--r-control);
    border-left: 2px solid var(--accent);
}}
.ob-ears.is-weak {{ border-left-color: var(--warn); }}
.ob-wave {{
    display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0.6rem;
    font-size: 0.86rem;
}}
.ob-wave-bar {{
    height: 22px; border-radius: 999px; background: var(--accent);
    color: var(--accent-on); font-weight: 600; font-size: 0.74rem;
    display: flex; align-items: center; padding: 0 0.75rem;
}}

/* ---- the few widget tweaks config.toml cannot express ---------------- */
.stTextArea textarea {{ font-size: 0.95rem !important; line-height: 1.6 !important; }}
.stButton > button:hover, .stDownloadButton > button:hover {{
    border-color: var(--accent); color: var(--accent);
}}
[data-testid="stExpander"] {{ margin-bottom: 0.55rem; background: var(--surface); }}
[data-testid="stExpander"] summary:hover {{ color: var(--accent); }}
</style>
"""


# --------------------------------------------------------------------------- #
# Chrome
# --------------------------------------------------------------------------- #


def strip(connected: bool, model: str) -> str:
    """The status line: which model is configured, and whether it is usable."""
    color = ACCENT if connected else WARN
    label = model if connected else f"{model} · not configured"
    return f"""
<div class="ob-strip">
  <span class="ob-brand">◆ Spec-Engine</span>
  <span class="ob-conn"><span class="ob-dot" style="background:{color}"></span>
  {_e(label)}</span>
</div>
"""


def progress(step: int, total: int) -> str:
    bars = "".join(
        f'<span class="{"is-done" if i < step else "is-now" if i == step else ""}"></span>'
        for i in range(total)
    )
    return f'<div class="ob-progress">{bars}</div>'


def head(step: int, total: int, title: str, lede: str, sub_step: str = "") -> str:
    marker = f"Step {step + 1} of {total}"
    if sub_step:
        marker += f" · {sub_step}"
    return (
        progress(step, total)
        + f'<div class="ob-step">{_e(marker)}</div>'
        + f'<h1 class="ob-title">{_e(title)}</h1>'
        + f'<p class="ob-lede">{_e(lede)}</p>'
    )


def notice(message: str) -> str:
    return f'<div class="ob-notice">{_e(message)}</div>'


def quiet(message: str) -> str:
    return f'<div class="ob-quiet">{_e(message)}</div>'


def rule() -> str:
    return '<div class="ob-rule"></div>'


# --------------------------------------------------------------------------- #
# Content blocks
# --------------------------------------------------------------------------- #


def claim(quote: str, meta: Sequence[str], grounded: bool) -> str:
    parts = ' <span class="ob-sep">·</span> '.join(_e(m) for m in meta)
    return (
        '<div class="ob-item">'
        f'<div class="ob-quote{"" if grounded else " is-ungrounded"}">'
        f"“{_e(quote)}”</div>"
        f'<div class="ob-meta">{parts}</div>'
        "</div>"
    )


def verdict(passed: bool, headline: str, detail: str) -> str:
    return f"""
<div class="ob-verdict {"is-pass" if passed else "is-fail"}">
  <div class="ob-verdict-mark" style="color:{ACCENT if passed else DANGER}">
    {"✓" if passed else "✕"}</div>
  <div>
    <div class="ob-verdict-title">{_e(headline)}</div>
    <div class="ob-quiet">{_e(detail)}</div>
  </div>
</div>
"""


def stats(items: Sequence[Tuple[str, str]]) -> str:
    return (
        '<div class="ob-stats">'
        + ' <span style="opacity:0.45">·</span> '.join(
            f"<b>{_e(value)}</b> {_e(label)}" for value, label in items
        )
        + "</div>"
    )


def meters(rows: Sequence[Tuple[str, int, int]]) -> str:
    out = []
    for label, part, whole in rows:
        pct = (part / whole * 100) if whole else 100.0
        color = ACCENT if pct >= 99.9 else WARN if pct >= 60 else DANGER
        out.append(
            '<div class="ob-meter">'
            f'<div class="ob-meter-head"><span>{_e(label)}</span>'
            f"<span>{part} of {whole}</span></div>"
            f'<div class="ob-meter-track"><div class="ob-meter-fill" '
            f'style="width:{pct:.0f}%;background:{color}"></div></div></div>'
        )
    return "".join(out)


def answer_box(label: str, value: str) -> str:
    return f'<div class="ob-answer"><b>{_e(label)}</b><br/>{_e(value)}</div>'


def ears_line(statement: str, weak: bool, issues: Sequence[str]) -> str:
    """The criterion itself. Nothing is annotated unless something is wrong."""
    body = f'<div class="ob-ears{" is-weak" if weak else ""}">{_e(statement)}'
    if issues:
        body += (
            '<div class="ob-quiet" style="margin-top:0.4rem">'
            + " · ".join(_e(issue) for issue in issues)
            + "</div>"
        )
    return body + "</div>"


def waves(wave_lists: Sequence[Sequence[str]]) -> str:
    if not wave_lists:
        return ""
    widest = max(len(w) for w in wave_lists) or 1
    out = []
    for index, wave in enumerate(wave_lists, start=1):
        width = 18 + (len(wave) / widest) * 40
        out.append(
            f'<div class="ob-wave"><span class="ob-quiet" style="width:64px">'
            f"Wave {index}</span>"
            f'<div class="ob-wave-bar" style="width:{width:.0f}%">'
            f"{len(wave)} in parallel</div>"
            f'<span class="ob-quiet ob-mono">{_e(" ".join(wave))}</span></div>'
        )
    return "".join(out)
