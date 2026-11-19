"""Structured-output resilience: fenced / chatty / truncated JSON, alias keys, salvage and fast fallback."""

from __future__ import annotations

import json

import pytest

from app.services.llm.base import LLMRateLimitError, LLMRequest, LLMResponse, LLMTruncatedError
from app.services.llm.json_repair import JSONExtractionError, extract_json
from app.services.llm.llm_service import LLMService
from tests.unit.test_validator import make_validator, rec

FULL = {"recommendations": [{"poi_id": "A", "rank": 1}, {"poi_id": "B", "rank": 2}], "summary": "ok"}


@pytest.mark.parametrize("text", [
    json.dumps(FULL),
    "```json\n" + json.dumps(FULL) + "\n```",
    "Here are my picks:\n```JSON\n" + json.dumps(FULL) + "\n```\nEnjoy your trip!",
    "Sure! " + json.dumps(FULL) + " Let me know if you need more.",
    '{"recommendations": [{"poi_id": "A", "rank": 1}, {"poi_id": "B", "rank": 2},], "summary": "ok",}',
])
def test_extract_json_recovers_wrapped_output(text: str) -> None:
    value, repaired = extract_json(text)
    assert value == FULL and repaired is False


def test_extract_json_repairs_truncation_keeping_complete_items() -> None:
    cut = '```json\n{"recommendations": [{"poi_id": "A", "rank": 1}, {"poi_id": "B", "ra'
    value, repaired = extract_json(cut)
    assert repaired is True
    assert value["recommendations"][0] == {"poi_id": "A", "rank": 1}


@pytest.mark.parametrize("text", ["", "no json here", "{not json", "```\n```"])
def test_extract_json_gives_up_cleanly(text: str) -> None:
    with pytest.raises(JSONExtractionError):
        extract_json(text)


def test_validator_accepts_explanation_alias_and_drops_only_the_broken_item() -> None:
    good = rec("POI000001")
    aliased = {k: v for k, v in rec("POI000003", 2).items() if k != "reason"} | {
        "explanation": "Quiet lakeside paths for a relaxed walk."}
    broken = {"poi_id": "POI000002", "rank": 3}  # no reason / explanation at all
    text = json.dumps({"recommendations": [good, aliased, broken], "summary": "s"})
    out = make_validator()(LLMResponse(text=text, provider="groq", model="m"))
    assert [r.poi_id for r in out.items] == ["POI000001", "POI000003"]
    assert any("dropped malformed recommendation #3" in w for w in out.warnings)


def test_validator_salvages_truncated_output_with_warning() -> None:
    text = json.dumps({"recommendations": [rec("POI000001"), rec("POI000003", 2)], "summary": "s"})
    cut = text[: text.index('"POI000003"') + 20]
    out = make_validator()(LLMResponse(text=cut, provider="gemini", model="m", finish_reason="MAX_TOKENS"))
    assert out.items[0].poi_id == "POI000001"
    assert any("cut off" in w for w in out.warnings)


# ------------------------------------------------------------------ LLMService behaviour
REQ = LLMRequest(system="s", user="u", json_schema={})
GOOD = json.dumps({"ok": True})


class Scripted:
    def __init__(self, name: str, outcomes: list) -> None:
        self.name, self.model, self.outcomes, self.calls = name, "m", outcomes, 0

    async def generate(self, request: LLMRequest) -> LLMResponse:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResponse(text=outcome, provider=self.name, model=self.model)


def parse(response: LLMResponse) -> dict:
    return extract_json(response.text)[0]


@pytest.mark.anyio
async def test_rate_limit_goes_straight_to_fallback_provider() -> None:
    groq = Scripted("groq", [LLMRateLimitError("429", provider="groq"), GOOD])
    gemini = Scripted("gemini", [GOOD])
    result = await LLMService([groq, gemini], max_attempts=3).generate(REQ, parse)
    assert groq.calls == 1 and result.response.provider == "gemini"  # no backoff sleep on groq


@pytest.mark.anyio
async def test_long_retry_after_is_not_waited_for_on_last_provider() -> None:
    exc = LLMRateLimitError("429", provider="gemini")
    exc.retry_after = 30.0
    gemini = Scripted("gemini", [exc, GOOD])
    with pytest.raises(Exception, match="All LLM providers failed"):
        await LLMService([gemini], max_attempts=3, max_retry_wait=2.0).generate(REQ, parse)
    assert gemini.calls == 1


@pytest.mark.anyio
async def test_truncated_answer_is_salvaged_instead_of_discarded() -> None:
    partial = LLMResponse(text='{"ok": true, "items": [1, 2', provider="groq", model="m", finish_reason="length")
    groq = Scripted("groq", [LLMTruncatedError("cut", provider="groq", partial=partial)])
    gemini = Scripted("gemini", [GOOD])
    result = await LLMService([groq, gemini]).generate(REQ, parse)
    assert result.response.provider == "groq" and gemini.calls == 0
    assert result.value["ok"] is True
    assert result.attempts[-1].outcome == "ok_salvaged"
