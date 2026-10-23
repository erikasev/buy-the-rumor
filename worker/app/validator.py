"""Hard risk limits for a brief, enforced in code.

This is the last stage before a brief is stored. Whatever the LLM stages
produced, a brief that breaks any rule here never leaves the system.
Each rule returns a Violation with a stable `rule` name so tests and error
messages can refer to it.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.brief import BriefFrontmatter, Constraints, UserRules

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

# ADR-0001: nothing users receive may name a source. Catches URLs and @handles (but not
# email-like text such as a@b); the source's own host is passed in from the private config.
_SOURCE_LEAK = re.compile(r"https?://|(?<![\w.])@[A-Za-z0-9_]{2,15}\b")

# An idea can describe a different instrument than its label ("etf on SPY: buy puts" is an
# option trade). These catch the clear cases so a mislabel can't slip past the user's rules.
_IMPLIED_INSTRUMENTS = {
    "option": re.compile(r"\b(puts?|calls|call options?|options?|straddles?|strangles?|collars?)\b", re.I),
    "future": re.compile(r"\bfutures?\b", re.I),
}

CITATION = re.compile(r"\[([A-Z]+\d+)\]")
# Any citation-like marker, including ranges and lists such as [S1-S5] or [S1, S2].
_MARKER = re.compile(r"[ \t]*\[[A-Z]+\d+(?:\s*[-\u2013,]\s*[A-Z]*\d+)*\]")


def strip_citations(text: str) -> str:
    """Remove citation markers. Sources stay internal (ADR-0001), so users never see them."""
    return _MARKER.sub("", text).strip()


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str


def effective_limits(rules: UserRules | None) -> Constraints:
    """The hard limits, tightened by any numeric limit the user stated. Never looser."""
    h = HARD_LIMITS
    if rules is None:
        return h
    return Constraints(
        max_total_usd=min(h.max_total_usd, rules.max_total_usd or h.max_total_usd),
        max_position_pct=min(h.max_position_pct, rules.max_position_pct or h.max_position_pct),
        allowed_order_types=h.allowed_order_types,
        stop_loss_pct=min(h.stop_loss_pct, rules.stop_loss_pct or h.stop_loss_pct),
    )


def validate_brief(fm: BriefFrontmatter, now: datetime) -> list[Violation]:
    """Return every violation found. An empty list means the brief is valid."""
    v: list[Violation] = []
    c = fm.constraints
    h = effective_limits(fm.user_rules)

    # The brief's own limits must sit inside the hard limits and the user's rules
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

    # Research leads: unique ids, and only instruments the user's rules allow
    lead_ids = [lead.id for lead in fm.leads]
    if len(set(lead_ids)) != len(lead_ids):
        v.append(Violation("duplicate_lead", "Each lead needs its own id."))
    rules = fm.user_rules or UserRules()
    for lead in fm.leads:
        for e in lead.expressions:
            for implied, pattern in _IMPLIED_INSTRUMENTS.items():
                if implied != e.instrument and pattern.search(e.idea) and not rules.allows(implied):
                    v.append(
                        Violation(
                            "instrument_not_allowed",
                            f"{lead.id} describes a {implied} trade on {e.underlying}, "
                            "which the user's rules don't allow.",
                        )
                    )
            if not rules.allows(e.instrument):
                v.append(
                    Violation(
                        "instrument_not_allowed",
                        f"{lead.id} suggests a {e.instrument} on {e.underlying}, "
                        "which the user's rules don't allow.",
                    )
                )

    # Totals
    total = sum(p.allocation_pct for p in fm.positions if p.action == "buy")
    if total > 100 + ALLOCATION_TOLERANCE:
        v.append(Violation("total_allocation", f"Buy allocations sum to {total:g}%, above 100%."))

    return v


def validate_citations(body: str, source_ids: set[str], require: bool = True) -> list[Violation]:
    """Every [ID] cited in the body must be a source we collected. With `require` (any brief
    that trades), there must be at least one. Ranges like [S1-S5] don't count as citations."""
    cited = set(CITATION.findall(body))
    if not cited and require:
        return [
            Violation(
                "no_citations", "Every trade must be backed by a cited source, written like [S3] (no ranges)."
            )
        ]
    unknown = sorted(cited - source_ids)
    if unknown:
        return [
            Violation("unknown_source", f"The brief cites sources that do not exist: {', '.join(unknown)}.")
        ]
    return []


def validate_public_text(texts: list[str], hosts: tuple[str, ...] = ()) -> list[Violation]:
    """Text users receive must not name a source: no URLs, @handles, or the source's host (ADR-0001)."""
    names = [h.removeprefix("www.") for h in hosts if h]
    host_pattern = re.compile("|".join(rf"\b{re.escape(n)}\b" for n in names), re.I) if names else None
    for text in texts:
        if _SOURCE_LEAK.search(text) or (host_pattern and host_pattern.search(text)):
            return [
                Violation(
                    "source_named", "The brief must not name accounts, handles or links; describe the signal."
                )
            ]
    return []
