"""Offline mock LLM (LLM_PROVIDER=mock): deterministic, no network, clearly labelled.

It produces schema-valid JSON from the same input a real model gets, so the
whole pipeline (validation, rendering) can be demonstrated without API keys.
Responses are marked ``generation_method="mock_llm"`` in the API.
"""

from __future__ import annotations

import json

from app.services.llm.base import LLMRequest, LLMResponse
from app.services.llm.prompts.recommendation_prompt import extract_input
from app.services.recommendation.fallback import templated_response


class MockLLMProvider:
    name = "mock"
    model = "deterministic-template-v1"

    def __init__(self, max_results: int = 8) -> None:
        self.max_results = max_results

    async def generate(self, request: LLMRequest) -> LLMResponse:
        payload = extract_input(request.user)
        return LLMResponse(text=json.dumps(templated_response(payload, self.max_results)),
                           provider=self.name, model=self.model, finish_reason="STOP")
