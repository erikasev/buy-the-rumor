from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.brief import BriefFormatError, parse_brief
from app.validator import validate_brief, validate_citations

UTC = timezone.utc

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
    buy = {**data["positions"][0], "allocation_pct": 25}
    data["positions"] = [{**buy, "asset": f"ASSET-{c}-USD"} for c in "VWXYZ"]  # 5 x 25% = 125%
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


@pytest.mark.parametrize(
    "changes,rule",
    [
        ({"constraints__max_total_usd": 1000}, "budget_too_high"),
        ({"constraints__max_position_pct": 100}, "position_limit_too_high"),
        ({"constraints__allowed_order_types": ["limit", "market"]}, "order_types_too_loose"),
        ({"constraints__stop_loss_pct": 30}, "stop_loss_too_wide"),
    ],
)
def test_brief_cannot_loosen_hard_limits(changes, rule):
    _, violations = brief_with(**changes)
    assert rule in rules(violations)


def test_market_order_fails_even_when_brief_allows_it():
    _, violations = brief_with(
        constraints__allowed_order_types=["limit", "market"], positions__0__order_type="market"
    )
    assert "order_types_too_loose" in rules(violations)


def test_stricter_constraints_are_fine():
    _, violations = brief_with(constraints__max_total_usd=50, constraints__max_position_pct=20)
    assert violations == []


@pytest.mark.parametrize(
    "body,sources,rule",
    [
        ("No citations at all.", {"X1"}, "no_citations"),
        ("Cites [X1] and [X9].", {"X1", "X2"}, "unknown_source"),
        ("A range [X1-X2] is not a citation.", {"X1", "X2"}, "no_citations"),
    ],
)
def test_citation_rules(body, sources, rule):
    assert rules(validate_citations(body, sources)) == {rule}


def test_valid_citations_pass():
    assert validate_citations("Up [X1][X2], down [X2].", {"X1", "X2", "X3"}) == []


def test_hold_only_brief_may_skip_citations():
    assert validate_citations("Nothing relevant; holding.", {"X1"}, require=False) == []
    assert rules(validate_citations("Bad [X9].", {"X1"}, require=False)) == {"unknown_source"}


def _with_leads(rules=None, leads=None):
    from app.brief import Lead, UserRules

    fm, _ = parse_brief(VALID_BRIEF)
    data = fm.model_dump()
    data["user_rules"] = (rules or UserRules()).model_dump()
    data["leads"] = leads or [
        Lead(
            id="L1",
            signal="s",
            hypothesis="h",
            research=["r"],
            expressions=[{"instrument": "option", "underlying": "KRE", "idea": "puts"}],
            horizon="weeks",
            conviction="low",
            invalidation="i",
        ).model_dump()
    ]
    return type(fm).model_validate(data)


def test_effective_limits_take_the_tighter_value():
    from app.brief import UserRules
    from app.validator import effective_limits

    limits = effective_limits(UserRules(max_total_usd=40, max_position_pct=90, stop_loss_pct=5))
    assert (limits.max_total_usd, limits.max_position_pct, limits.stop_loss_pct) == (40, 25, 5)


def test_constraints_looser_than_user_rules_fail():
    from app.brief import UserRules

    fm = _with_leads(rules=UserRules(max_position_pct=10))  # fixture constraints say 25%
    assert "position_limit_too_high" in rules(validate_brief(fm, NOW))


@pytest.mark.parametrize(
    "user_rules,ok",
    [
        ({}, True),
        ({"allowed_instruments": ["option"]}, True),
        ({"allowed_instruments": ["stock"]}, False),
        ({"excluded_instruments": ["option"]}, False),
    ],
)
def test_lead_instruments_follow_user_rules(user_rules, ok):
    from app.brief import UserRules

    found = rules(validate_brief(_with_leads(rules=UserRules(**user_rules)), NOW))
    assert ("instrument_not_allowed" not in found) is ok


def test_duplicate_lead_ids_fail():
    fm = _with_leads()
    lead = fm.leads[0].model_dump()
    fm = _with_leads(leads=[lead, lead])
    assert "duplicate_lead" in rules(validate_brief(fm, NOW))


@pytest.mark.parametrize(
    "text,leaks",
    [
        ("A trader @repo_desk notes repo stress.", True),
        ("Details at https://example.com/post", True),
        ("Posted on x.com yesterday.", True),
        ("Email ops@company.com about it.", False),
        ("CDS spreads widened 40bp.", False),
    ],
)
def test_public_text_must_not_name_sources(text, leaks):
    from app.validator import validate_public_text

    assert bool(validate_public_text([text])) is leaks


@pytest.mark.parametrize(
    "instrument,idea,user_rules,ok",
    [
        ("etf", "Hedge with puts if recession fears grow.", {"excluded_instruments": ["option"]}, False),
        ("etf", "Hedge with puts if recession fears grow.", {"allowed_instruments": ["etf", "option"]}, True),
        ("stock", "Buy calls into earnings.", {"allowed_instruments": ["stock"]}, False),
        ("etf", "Go long via index futures.", {"excluded_instruments": ["future"]}, False),
        (
            "stock",
            "Buy after the earnings call if guidance holds.",
            {"excluded_instruments": ["option"]},
            True,
        ),
        ("etf", "Underweight vs the index.", {"excluded_instruments": ["option", "future"]}, True),
    ],
)
def test_mislabeled_instrument_cannot_dodge_user_rules(instrument, idea, user_rules, ok):
    from app.brief import Lead, UserRules

    lead = Lead(
        id="L1",
        signal="s",
        hypothesis="h",
        research=["r"],
        horizon="weeks",
        conviction="low",
        invalidation="i",
        expressions=[{"instrument": instrument, "underlying": "SPY", "idea": idea}],
    )
    fm = _with_leads(rules=UserRules(**user_rules), leads=[lead.model_dump()])
    assert ("instrument_not_allowed" not in rules(validate_brief(fm, NOW))) is ok
