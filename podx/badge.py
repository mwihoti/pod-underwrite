"""Embeddable SVG badge.

Deliberately text-only (no fonts, no images) so it renders identically in a
GitHub README, a marketplace listing and a browser. The badge carries the block
and the netlist fingerprint, because a badge without provenance is decoration.
"""

from __future__ import annotations

from html import escape

from . import verdict as V

_COLORS = {
    "ok": "#1a7f37",
    "warn": "#a16207",
    "bad": "#b42318",
    "unknown": "#475467",
}

_TEXT = {
    V.PASS: "passes vectors",
    V.VECTOR_MISMATCH: "mismatch",
    V.BEST_FIRST_AT_RISK: "not best-first",
    V.TRIVIAL_TASK: "trivial task",
    V.INVALID: "malformed",
    V.GATE_LIMIT: "over limits",
    V.UNRESOLVED: "unresolved",
    V.INCONCLUSIVE: "inconclusive",
    V.UNSUPPORTED: "unsupported",
}


def _width(text: str, size: int = 11) -> int:
    # Approximate DejaVu Sans advance width; good enough for a static badge.
    return int(len(text) * size * 0.62) + 12


def render(audit: dict) -> str:
    report = audit.get("report") or {}
    code = report.get("verdict", audit.get("verdict", V.UNRESOLVED))
    tone = V.tone(code)
    color = _COLORS.get(tone, _COLORS["unknown"])
    right = _TEXT.get(code, code.lower())

    econ = report.get("economics") or {}
    cost = econ.get("cost")
    seat = econ.get("seat_best")
    if cost is not None and seat:
        right = f"{right} · C={cost} vs {seat}"
    elif cost is not None:
        right = f"{right} · C={cost}"

    left = "PoD audit"
    lw, rw = _width(left), _width(right)
    total = lw + rw
    height = 20

    prov = report.get("provenance") or {}
    block = prov.get("block")
    sha = (prov.get("netlist_sha256") or "")[:10]
    tip = f"block {block}" if block else "block unknown"
    if sha:
        tip += f" · sha256 {sha}"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{height}" role="img"
     aria-label="{escape(left)}: {escape(right)}">
  <title>{escape(left)}: {escape(right)} ({escape(tip)})</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#fff" stop-opacity=".12"/>
    <stop offset="1" stop-opacity=".12"/>
  </linearGradient>
  <rect width="{total}" height="{height}" rx="3" fill="#24292f"/>
  <rect x="{lw}" width="{rw}" height="{height}" rx="3" fill="{color}"/>
  <rect width="{lw}" height="{height}" rx="3" fill="url(#s)"/>
  <g fill="#fff" font-family="Verdana,DejaVu Sans,Geneva,sans-serif" font-size="11">
    <text x="{lw // 2}" y="14" text-anchor="middle">{escape(left)}</text>
    <text x="{lw + rw // 2}" y="14" text-anchor="middle">{escape(right)}</text>
  </g>
</svg>
"""


def inline(report: dict) -> str:
    """Same badge from a report dict (used on the report page itself)."""
    return render({"report": report})
