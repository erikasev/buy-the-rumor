"""Hard risk limits for a brief, enforced in code.

This is the last stage before a brief is stored. Whatever the LLM stages
produced, a brief that breaks any rule here never leaves the system.
Each rule returns a Violation with a stable `rule` name so tests and error
messages can refer to it.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.brief import BriefFrontmatter, Constraints

MAX_VALIDITY = timedelta(hours=24)
ALLOCATION_TOLERANCE = 1e-6  # floats like 33.3 + 33.3 + 33.4

# The loosest constraints any brief may carry (design.md: $100 test budget, 25% per
# position, limit orders only, 8% stop-loss). A brief's own `constraints` block may be
# stricter, never looser, so an LLM can't relax the rules by rewriting them.
HARD_LIMITS = Constraints(
    max_total_usd=100,
    max_position_pct=25,
    allowed_order_types=["limit"],
    stop_loss_pct=8,
)

CITATION = re.compile(r"\[([A-Z]+\d+)\]")
# Any citation-like marker, including ranges and lists such as [X1-X5] or [X1, X2].
_MARKER = re.compile(r"[ \t]*\[[A-Z]+\d+(?:\s*[-\u2013,]\s*[A-Z]*\d+)*\]")


def strip_citations(text: str) -> str:
    """Remove citation markers. Sources stay internal (ADR-0001), so users never see them."""
    return _MARKER.sub("", text).strip()


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str


def validate_brief(fm: BriefFrontmatter, now: datetime) -> list[Violation]:
    """Return every violation found. An empty list means the brief is valid."""
    v: list[Violation] = []
    c = fm.constraints
    h = HARD_LIMITS

    # The brief's own limits must sit inside the hard limits
    if c.max_total_usd > h.max_total_usd:
        v.append(Violation("budget_too_high", f"The budget may be at most ${h.max_total_usd:g}."))
    if c.max_position_pct > h.max_position_pct:
        v.append(
            Violation(
                "position_limit_too_high", f"The per-position limit may be at most {h.max_position_pct:g}%."
            )
        )
    if not set(c.allowed_order_types) <= set(h.allowed_order_types):
        v.append(
            Violation("order_types_too_loose", f"Only {', '.join(h.allowed_order_types)} orders are allowed.")
        )
    if c.stop_loss_pct > h.stop_loss_pct:
        v.append(Violation("stop_loss_too_wide", f"The stop-loss may be at most {h.stop_loss_pct:g}%."))

    # Time window
    if fm.expires_at <= fm.generated_at:
        v.append(Violation("expiry_order", "expires_at must be after generated_at."))
    elif fm.expires_at - fm.generated_at > MAX_VALIDITY:
        v.append(Violation("expiry_too_long", "A brief may be valid for at most 24 hours."))
    if fm.expires_at <= now:
        v.append(Violation("expired", "The brief has already expired."))

    # Positions, one at a time
    seen: set[str] = set()
    for p in fm.positions:
        if p.asset in seen:
            v.append(Violation("duplicate_asset", f"{p.asset} appears more than once."))
        seen.add(p.asset)

        if p.allocation_pct > c.max_position_pct + ALLOCATION_TOLERANCE:
            v.append(
                Violation(
                    "position_too_large",
                    f"{p.asset} is {p.allocation_pct:g}%, above the "
                    f"{c.max_position_pct:g}% per-position limit.",
                )
            )

        if p.action == "hold":
            if p.allocation_pct != 0:
                v.append(Violation("hold_with_allocation", f"{p.asset} is a hold but has an allocation."))
            continue

        if p.allocation_pct == 0:
            v.append(Violation("trade_without_allocation", f"{p.asset} is a {p.action} with 0% allocation."))
        if p.order_type is None:
            v.append(Violation("missing_order_type", f"{p.asset} needs an order_type."))
        elif p.order_type not in c.allowed_order_types:
            v.append(
                Violation(
                    "order_type_not_allowed",
                    f"{p.asset} uses a {p.order_type} order, which this brief does not allow.",
                )
            )

    # Totals
    total = sum(p.allocation_pct for p in fm.positions if p.action == "buy")
    if total > 100 + ALLOCATION_TOLERANCE:
        v.append(Violation("total_allocation", f"Buy allocations sum to {total:g}%, above 100%."))

    return v


def validate_citations(body: str, source_ids: set[str], require: bool = True) -> list[Violation]:
    """Every [ID] cited in the body must be a source we collected. With `require` (any brief
    that trades), there must be at least one. Ranges like [X1-X5] don't count as citations."""
    cited = set(CITATION.findall(body))
    if not cited and require:
        return [
            Violation(
                "no_citations", "Every trade must be backed by a cited source, written like [X3] (no ranges)."
            )
        ]
    unknown = sorted(cited - source_ids)
    if unknown:
        return [
            Violation("unknown_source", f"The brief cites sources that do not exist: {', '.join(unknown)}.")
        ]
    return []
