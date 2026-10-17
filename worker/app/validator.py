"""Hard risk limits for a brief, enforced in code.

This is the last stage before a brief is stored. Whatever the LLM stages
produced, a brief that breaks any rule here never leaves the system.
Each rule returns a Violation with a stable `rule` name so tests and error
messages can refer to it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.brief import BriefFrontmatter

MAX_VALIDITY = timedelta(hours=24)
ALLOCATION_TOLERANCE = 1e-6  # floats like 33.3 + 33.3 + 33.4


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str


def validate_brief(fm: BriefFrontmatter, now: datetime) -> list[Violation]:
    """Return every violation found. An empty list means the brief is valid."""
    v: list[Violation] = []
    c = fm.constraints

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
