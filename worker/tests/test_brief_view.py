from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.brief import BriefView
from app.validator import validate_brief


def test_brief_view_fixture_parses_and_passes_the_validator(fixture_text):
    view = BriefView.model_validate_json(fixture_text("brief_view.json"))
    assert validate_brief(view, datetime(2026, 10, 21, 14, 0, tzinfo=UTC)) == []


def test_brief_view_rejects_a_sources_field(fixture_text):
    # Sources stay internal (ADR-0001); the schema forbids unknown keys, so one can't slip in.
    data = BriefView.model_validate_json(fixture_text("brief_view.json")).model_dump()
    data["sources"] = [{"id": "S1", "url": "https://example.com"}]
    with pytest.raises(ValidationError):
        BriefView.model_validate(data)
