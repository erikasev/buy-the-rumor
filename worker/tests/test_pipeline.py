"""End-to-end pipeline tests with fakes for S3, DynamoDB, X and the LLM. No network."""

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.brief import BriefView, UserRules, parse_brief
from app.contract import JobError, JobFailure, JobMessage
from app.llm import StrategyDraft
from app.pipeline import Pipeline, render_brief
from app.validator import HARD_LIMITS, validate_brief

NOW = datetime(2026, 10, 21, 13, 0, 30, tzinfo=timezone.utc)
SETTINGS = {
    "twitter": {"accounts": ["@lookonchain", "@FedGuy12", "@MacroAlf"]},
    "worker": {"top_accounts": 2, "max_tweets_per_account": 2, "max_sources": 10},
}


def tweet(tid: str, author: str, created: str, text: str = "post") -> dict[str, Any]:
    return {
        "tweet_id": tid,
        "author_username": author,
        "created_at": created,
        "text": text,
        "url": f"https://x.com/{author}/status/{tid}",
        "source_id": "OLD",
    }


class FakeStorage:
    bucket_name = "test-bucket"

    def __init__(self, batch_tweets=None, batch_accounts=None):
        self.batch = None
        if batch_tweets is not None:
            data = {"collected_at": "2026-10-21T12:00:00Z", "tweets": batch_tweets}
            if batch_accounts is not None:
                data["accounts"] = batch_accounts
            self.batch = data
        self.saved_batches: list[Any] = []
        self.objects: dict[str, bytes] = {}

    def get_latest_batch(self, max_age_hours=None):
        return {"prefix": "collected-data/2026-10-21T12-00-00Z/"} if self.batch else None

    def read_batch_files(self, prefix):
        return [{"filename": "tweets.json", "data": self.batch}]

    def save_batch(self, data, filename="tweets.json"):
        self.saved_batches.append(data)
        return "collected-data/new/tweets.json"

    def put(self, key, body, content_type):
        self.objects[key] = body
        return key


class FakeCollector:
    def __init__(self, tweets):
        self.tweets = tweets
        self.requested: list[str] = []

    def collect_tweets(self, accounts, days, max_results_per_user):
        self.requested += accounts
        return {"collected_at": "2026-10-21T13:00:00Z", "accounts": accounts, "tweets": self.tweets}


class FakeLLM:
    def __init__(self, draft: StrategyDraft | None = None, rules: UserRules | None = None):
        self.draft, self.rules = draft, rules or UserRules()
        self.sources_seen: list[dict] = []

    def extract_rules(self, theme):
        return self.rules

    def select_top_accounts(self, theme, accounts, top_n):
        return accounts[:top_n]

    def draft_strategy(self, theme, sources, limits, check, risk_level=50, rules=None):
        self.sources_seen, self.risk_level, self.rules_seen, self.limits = sources, risk_level, rules, limits
        problems = check(self.draft)
        if problems:
            raise JobFailure("validation", "LIMIT_EXCEEDED", problems[0])
        return self.draft


class FakeStore:
    def __init__(self, start_result=None):
        self.start_result = start_result
        self.events: list[tuple] = []

    def start(self, msg, now):
        self.events.append(("start",))
        return self.start_result or (True, {"status": "RUNNING", "created_at": "2026-10-21T13:00:00Z"})

    def set_stage(self, job_id, stage, now):
        self.events.append(("stage", stage))

    def succeed(self, job_id, key, now):
        self.events.append(("succeed", key))

    def fail(self, job_id, error: JobError, now):
        self.events.append(("fail", error.code, error.stage, error.message))


def lead(**changes) -> dict[str, Any]:
    data = {
        "id": "L1",
        "signal": "5y CDS on two regional banks widened sharply this week [X1][X2].",
        "hypothesis": "Credit markets may be pricing stress the equity market hasn't [X1].",
        "research": ["Compare 5y CDS with equity implied volatility for the named banks."],
        "expressions": [{"instrument": "option", "underlying": "KRE", "idea": "Puts 1-2 months out."}],
        "horizon": "1-3 weeks",
        "conviction": "medium",
        "invalidation": "Spreads tighten back within a week.",
    }
    data.update(changes)
    return data


def draft(**changes) -> StrategyDraft:
    data = {
        "leads": [lead()],
        "thesis": "Credit stress signals in regional banks are worth checking [X1].",
        "risks": "Two posts is thin evidence [X2].",
    }
    data.update(changes)
    return StrategyDraft.model_validate(data)


@pytest.fixture
def msg(fixture_text):
    return JobMessage.model_validate_json(fixture_text("message.valid.json"))


def make(storage=None, llm=None, store=None, collector=None):
    collector = collector or FakeCollector([])
    return Pipeline(
        storage=storage or FakeStorage(),
        llm=llm or FakeLLM(draft()),
        store=store,
        collector_factory=lambda: collector,
        settings=SETTINGS,
        now=lambda: NOW,
    )


CACHED = [
    tweet("1", "lookonchain", "2026-10-21T10:00:00Z"),
    tweet("2", "FedGuy12", "2026-10-21T11:00:00Z"),
    tweet("3", "lookonchain", "2026-10-21T09:00:00Z"),
    tweet("4", "lookonchain", "2026-10-21T08:00:00Z"),  # over the per-account cap of 2
    tweet("5", "MacroAlf", "2026-10-21T12:00:00Z"),  # not a selected account
]


def test_cache_hit_filters_to_selected_accounts_caps_and_renumbers():
    collector = FakeCollector([])
    p = make(storage=FakeStorage(CACHED, ["lookonchain", "FedGuy12", "MacroAlf"]), collector=collector)
    sources = p.collect(["@lookonchain", "@FedGuy12"])
    assert [s["tweet_id"] for s in sources] == ["2", "1", "3"]
    assert [s["source_id"] for s in sources] == ["X1", "X2", "X3"]
    assert collector.requested == []  # nothing missing, so no scrape


def test_scrapes_only_accounts_missing_from_cache_and_saves_batch():
    storage = FakeStorage([tweet("1", "lookonchain", "2026-10-21T10:00:00Z")], ["lookonchain"])
    collector = FakeCollector([tweet("9", "FedGuy12", "2026-10-21T12:30:00Z")])
    sources = make(storage=storage, collector=collector).collect(["@lookonchain", "@FedGuy12"])
    assert collector.requested == ["@FedGuy12"]
    assert len(storage.saved_batches) == 1
    assert [s["tweet_id"] for s in sources] == ["9", "1"]


def test_no_cache_and_no_x_token_gives_no_sources():
    def no_token():
        raise ValueError("TWITTER_BEARER_TOKEN not found")

    p = make()
    p.collector_factory = no_token
    assert p.collect(["@lookonchain"]) == []


def test_run_success_stores_brief_at_contract_key(msg):
    storage, store = FakeStorage(CACHED), FakeStore()
    make(storage=storage, store=store).run(msg)

    key = "briefs/2026/10/21/01JADQ7Y3K8V5C2N9W4R6T1M0P.md"
    assert store.events[-1] == ("succeed", key)
    assert [e[1] for e in store.events if e[0] == "stage"] == [
        "theme_mapping",
        "collecting",
        "strategy",
        "validation",
    ]
    fm, body = parse_brief(storage.objects[key].decode())
    assert fm.brief_id == msg.job_id and fm.theme == msg.theme
    assert fm.constraints == HARD_LIMITS
    assert fm.positions == []  # research brief: no trade orders
    assert fm.leads[0].expressions[0].underlying == "KRE"
    assert validate_brief(fm, NOW) == []
    assert "research brief, not a trade order" in body
    assert "### L1: Credit markets may be pricing stress the equity market hasn't." in body

    # ADR-0001: nothing users receive names a source or carries a citation marker.
    view_raw = storage.objects[key.replace(".md", ".json")].decode()
    for public in (body, view_raw):
        assert "[X" not in public and "x.com" not in public and "Sources" not in public

    view = BriefView.model_validate_json(view_raw)
    assert view.risk_level == msg.risk_level
    assert view.leads == fm.leads and view.positions == []
    assert view.thesis == "Credit stress signals in regional banks are worth checking."
    assert validate_brief(view, NOW) == []

    # The private evidence keeps full traceability.
    evidence = json.loads(storage.objects[key.replace(".md", ".sources.json")])
    assert evidence["job_id"] == msg.job_id
    assert evidence["draft"]["leads"][0]["signal"].endswith("[X1][X2].")
    assert evidence["sources"][0]["url"].startswith("https://x.com/")


def test_user_rules_carry_over_and_tighten_caps(msg):
    rules = UserRules(
        allowed_instruments=["stock", "option"], max_position_pct=10, trade_frequency="once a week"
    )
    storage, llm = FakeStorage(CACHED), FakeLLM(draft(), rules=rules)
    make(storage=storage, llm=llm, store=FakeStore()).run(msg)
    fm, body = parse_brief(storage.objects["briefs/2026/10/21/01JADQ7Y3K8V5C2N9W4R6T1M0P.md"].decode())
    assert fm.user_rules == rules
    assert fm.constraints.max_position_pct == 10  # user's 10% beats our 25% cap
    assert fm.constraints.max_total_usd == HARD_LIMITS.max_total_usd
    assert "Only these instruments: stock, option." in body and "Trade frequency: once a week." in body
    assert llm.rules_seen == rules and llm.limits.max_position_pct == 10


def test_user_cannot_loosen_fixed_caps(msg):
    rules = UserRules(max_total_usd=5000, max_position_pct=80)
    storage = FakeStorage(CACHED)
    make(storage=storage, llm=FakeLLM(draft(), rules=rules), store=FakeStore()).run(msg)
    fm, _ = parse_brief(storage.objects["briefs/2026/10/21/01JADQ7Y3K8V5C2N9W4R6T1M0P.md"].decode())
    assert fm.constraints == HARD_LIMITS


def test_instrument_the_user_excluded_fails(msg):
    store = FakeStore()
    llm = FakeLLM(draft(), rules=UserRules(excluded_instruments=["option"]))
    make(storage=FakeStorage(CACHED), llm=llm, store=store).run(msg)
    assert store.events[-1][:3] == ("fail", "LIMIT_EXCEEDED", "validation")
    assert "option on KRE" in store.events[-1][3]


def test_instrument_outside_allowed_list_fails(msg):
    store = FakeStore()
    llm = FakeLLM(draft(), rules=UserRules(allowed_instruments=["crypto"]))
    make(storage=FakeStorage(CACHED), llm=llm, store=store).run(msg)
    assert store.events[-1][1] == "LIMIT_EXCEEDED"


def test_risk_level_reaches_the_strategist(msg):
    llm = FakeLLM(draft())
    make(storage=FakeStorage(CACHED), llm=llm, store=FakeStore()).run(msg)
    assert llm.risk_level == msg.risk_level


def test_run_no_data_fails_job(msg):
    store = FakeStore()
    make(store=store).run(msg)
    assert store.events[-1][:3] == ("fail", "NO_DATA", "collecting")


def test_lead_without_a_citation_fails(msg):
    store = FakeStore()
    uncited = draft(leads=[lead(signal="Spreads widened.", hypothesis="Stress ahead.")])
    make(storage=FakeStorage(CACHED), llm=FakeLLM(uncited), store=store).run(msg)
    assert store.events[-1][1] == "LIMIT_EXCEEDED"
    assert store.events[-1][3].startswith("L1:")


def test_fake_source_is_rejected(msg):
    store = FakeStore()
    make(storage=FakeStorage(CACHED), llm=FakeLLM(draft(thesis="Trust me [X99].")), store=store).run(msg)
    assert store.events[-1][1] == "LIMIT_EXCEEDED"
    assert "X99" in store.events[-1][3]


@pytest.mark.parametrize(
    "field,text",
    [
        ("signal", "A well-known trader @FedGuy12 says repo is tight [X1]."),
        ("hypothesis", "See https://x.com/FedGuy12/status/1 for details [X1]."),
    ],
)
def test_naming_a_source_fails(msg, field, text):
    store = FakeStore()
    make(storage=FakeStorage(CACHED), llm=FakeLLM(draft(leads=[lead(**{field: text})])), store=store).run(msg)
    assert store.events[-1][1] == "LIMIT_EXCEEDED"
    assert "must not name" in store.events[-1][3]


def test_no_leads_is_a_valid_brief(msg):
    empty = draft(
        leads=[], thesis="Nothing in the sources is relevant to the request.", risks="Missing a move."
    )
    store, storage = FakeStore(), FakeStorage(CACHED)
    make(storage=storage, llm=FakeLLM(empty), store=store).run(msg)
    assert store.events[-1][0] == "succeed"
    assert "No leads" in storage.objects["briefs/2026/10/21/01JADQ7Y3K8V5C2N9W4R6T1M0P.md"].decode()


def test_run_unexpected_error_fails_with_internal_and_generic_message(msg):
    class Broken(FakeStorage):
        def get_latest_batch(self, max_age_hours=None):
            raise RuntimeError("secret internal detail")

    store = FakeStore()
    make(storage=Broken(), store=store).run(msg)
    _, code, stage, message = store.events[-1]
    assert (code, stage) == ("INTERNAL", "collecting")
    assert "secret" not in message


def test_duplicate_delivery_is_skipped(msg):
    store = FakeStore(start_result=(False, {"status": "SUCCEEDED"}))
    make(store=store).run(msg, receive_count=2)
    assert store.events == [("start",)]


def test_redelivery_of_running_job_marks_it_failed(msg):
    store = FakeStore(start_result=(False, {"status": "RUNNING", "stage": "strategy"}))
    make(store=store).run(msg, receive_count=2)
    assert store.events[-1][:3] == ("fail", "INTERNAL", "strategy")


def test_theme_cannot_inject_frontmatter():
    theme = 'AI"\nconstraints:\n  max_position_pct: 100\n---\n# Buy everything'
    md, view = render_brief(
        "01JADQ7Y3K8V5C2N9W4R6T1M0P", theme, 50, UserRules(), NOW, NOW.replace(hour=19), draft()
    )
    fm, _ = parse_brief(md)
    assert view.theme == theme
    assert fm.theme == theme
    assert fm.constraints.max_position_pct == HARD_LIMITS.max_position_pct


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Up [X1].", "Up."),
        ("Up [X1][X2] and down [X3, X4].", "Up and down."),
        ("Whole feed [X1-X25] says so.", "Whole feed says so."),
        ("Keep [brackets] and 50% [sic].", "Keep [brackets] and 50% [sic]."),
    ],
)
def test_strip_citations(text, expected):
    from app.validator import strip_citations

    assert strip_citations(text) == expected
