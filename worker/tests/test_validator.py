from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.brief import BriefFormatError, parse_brief
from app.validator import validate_brief

VALID_BRIEF = (Path(__file__).parent / "fixtures" / "brief.valid.md").read_text()
NOW = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)  # inside the fixture's validity window


def brief_with(**changes):
    """Parse the valid fixture, apply changes, and return (frontmatter, violations)."""
    fm, _ = parse_brief(VALID_BRIEF)
    data = fm.model_dump()
    for path, value in changes.items():
        target = data
        keys = path.split("__")
        for key in keys[:-1]:
            target = target[int(key)] if isinstance(target, list) else target[key]
        last = keys[-1]
        if isinstance(target, list):
            target[int(last)] = value
        else:
            target[last] = value
    fm = type(fm).model_validate(data)
    return fm, validate_brief(fm, NOW)


def rules(violations):
    return {v.rule for v in violations}


def test_valid_brief_has_no_violations():
    fm, body = parse_brief(VALID_BRIEF)
    assert validate_brief(fm, NOW) == []
    assert "Instructions for the trading agent" in body


@pytest.mark.parametrize(
    "changes,rule",
    [
        ({"expires_at": datetime(2026, 10, 21, 12, 0, tzinfo=UTC)}, "expiry_order"),
        ({"expires_at": datetime(2026, 10, 23, 13, 0, tzinfo=UTC)}, "expiry_too_long"),
        ({"positions__0__allocation_pct": 30}, "position_too_large"),
        ({"positions__1__allocation_pct": 5}, "hold_with_allocation"),
        ({"positions__0__allocation_pct": 0}, "trade_without_allocation"),
        ({"positions__0__order_type": None}, "missing_order_type"),
        ({"positions__0__order_type": "market"}, "order_type_not_allowed"),
        ({"positions__1__asset": "ASSET-A-USD"}, "duplicate_asset"),
    ],
)
def test_each_rule_catches_its_violation(changes, rule):
    _, violations = brief_with(**changes)
    assert rule in rules(violations)


def test_expired_brief_is_rejected():
    fm, _ = parse_brief(VALID_BRIEF)
    later = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
    assert "expired" in rules(validate_brief(fm, later))


def test_total_allocation_over_100_is_rejected():
    fm, _ = parse_brief(VALID_BRIEF)
    data = fm.model_dump()
    data["constraints"]["max_position_pct"] = 100
    data["positions"][0]["allocation_pct"] = 60
    data["positions"].append({**data["positions"][0], "asset": "ASSET-C-USD", "allocation_pct": 50})
    fm = type(fm).model_validate(data)
    assert rules(validate_brief(fm, NOW)) == {"total_allocation"}


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter here",
        "---\ntheme: x\n",  # never closed
        "---\n- just\n- a list\n---\n",  # not a mapping
        "---\nbrief_id: [unclosed\n---\n",  # bad YAML
        VALID_BRIEF.replace("confidence: medium", "confidence: certain"),  # bad enum
        VALID_BRIEF.replace("theme:", "surprise_key: 1\ntheme:"),  # unknown key
    ],
)
def test_malformed_briefs_raise_format_error(text):
    with pytest.raises(BriefFormatError):
        parse_brief(text)
