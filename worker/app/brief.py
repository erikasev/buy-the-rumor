"""The brief: our core contract with the agent that acts on it (design.md, "The brief").

A brief is Markdown with YAML frontmatter. The frontmatter is checked by code;
the body is prose for the agent. This module only checks shape and types.
Hard risk limits live in validator.py.

A brief is a research brief (docs/adr/0002-briefs-are-research-leads.md): `leads` are
hypotheses drawn from market chatter for another agent to research, bounded by the
user's own rules (`user_rules`) and our fixed caps (`constraints`). `positions` stays
for contract v1 compatibility and is normally empty.
"""

from __future__ import annotations

from typing import Annotated, Literal

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

OrderType = Literal["limit", "market"]
InstrumentClass = Literal["stock", "etf", "option", "future", "crypto", "bond", "fx", "commodity"]
ShortText = Annotated[str, Field(min_length=1, max_length=300)]


class BriefFormatError(ValueError):
    """The text is not a brief at all: no frontmatter, bad YAML, or wrong types."""


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_total_usd: float = Field(gt=0)
    max_position_pct: float = Field(gt=0, le=100)
    allowed_order_types: list[OrderType] = Field(min_length=1)
    stop_loss_pct: float = Field(gt=0, le=50)


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]{0,19}-USD$")
    action: Literal["buy", "sell", "hold"]
    allocation_pct: float = Field(ge=0, le=100)
    order_type: OrderType | None = None
    entry: str | None = None
    confidence: Literal["low", "medium", "high"]


class UserRules(BaseModel):
    """Trading rules the user stated in their request. They carry over, unchanged, to the
    agent that acts on the brief. An empty list or None means the user didn't say."""

    model_config = ConfigDict(extra="forbid")

    allowed_instruments: list[InstrumentClass] = Field(default_factory=list)  # empty: not restricted
    excluded_instruments: list[InstrumentClass] = Field(default_factory=list)
    sectors: list[ShortText] = Field(default_factory=list, max_length=10)
    excluded_sectors: list[ShortText] = Field(default_factory=list, max_length=10)
    max_total_usd: float | None = Field(default=None, gt=0)
    max_position_pct: float | None = Field(default=None, gt=0, le=100)
    stop_loss_pct: float | None = Field(default=None, gt=0, le=50)
    trade_frequency: ShortText | None = None
    horizon: ShortText | None = None
    other: list[ShortText] = Field(default_factory=list, max_length=10)  # any other stated rule, verbatim

    def allows(self, instrument: str) -> bool:
        if instrument in self.excluded_instruments:
            return False
        return not self.allowed_instruments or instrument in self.allowed_instruments


class Expression(BaseModel):
    """One way a lead could be expressed, e.g. put options on KRE."""

    model_config = ConfigDict(extra="forbid")

    instrument: InstrumentClass
    underlying: str = Field(min_length=1, max_length=40)
    idea: ShortText


class Lead(BaseModel):
    """A signal worth researching. Not a trade instruction."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^L\d{1,2}$")
    signal: str = Field(min_length=1, max_length=600)
    hypothesis: str = Field(min_length=1, max_length=600)
    research: list[ShortText] = Field(min_length=1, max_length=6)
    expressions: list[Expression] = Field(default_factory=list, max_length=4)
    horizon: str = Field(min_length=1, max_length=100)
    conviction: Literal["low", "medium", "high"]
    invalidation: str = Field(min_length=1, max_length=600)


class BriefFrontmatter(BaseModel):
    # forbid: an unexpected key in a brief is a bug, not something to ignore.
    model_config = ConfigDict(extra="forbid")

    brief_id: str = Field(min_length=1)
    schema_version: Literal[1]
    theme: str = Field(min_length=1, max_length=1000)
    generated_at: AwareDatetime
    expires_at: AwareDatetime
    constraints: Constraints
    # Added in v1 (additive, ADR-0002): absent in briefs written before research leads.
    user_rules: UserRules | None = None
    leads: list[Lead] = Field(default_factory=list, max_length=6)
    positions: list[Position] = Field(default_factory=list)


class BriefView(BriefFrontmatter):
    """The structured brief the web page renders: <job_id>.json beside <job_id>.md.

    Same fields as the frontmatter, plus the risk level and the two prose sections
    (`thesis` holds the overview of the leads).
    It never contains sources: they stay internal (docs/adr/0001-data-sources-stay-internal.md).
    """

    risk_level: int = Field(ge=0, le=100)
    thesis: str = Field(min_length=1)
    risks: str = Field(min_length=1)


def split_frontmatter(markdown: str) -> tuple[str, str]:
    """Return (yaml_text, body). The file must start with a '---' line."""
    lines = markdown.lstrip("﻿").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise BriefFormatError("brief must start with a '---' frontmatter line")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1 :])
    raise BriefFormatError("frontmatter has no closing '---' line")


def parse_brief(markdown: str) -> tuple[BriefFrontmatter, str]:
    """Parse a brief into its typed frontmatter and its Markdown body."""
    yaml_text, body = split_frontmatter(markdown)
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise BriefFormatError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise BriefFormatError("frontmatter must be a YAML mapping")
    try:
        return BriefFrontmatter.model_validate(data), body
    except ValueError as exc:  # pydantic.ValidationError is a ValueError
        raise BriefFormatError(str(exc)) from exc
