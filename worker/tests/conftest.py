from pathlib import Path

import pytest

# Shared with the Go tests: both sides must agree on these files.
FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


@pytest.fixture
def fixture_text():
    def load(name: str) -> str:
        return (FIXTURES / name).read_text()

    return load
