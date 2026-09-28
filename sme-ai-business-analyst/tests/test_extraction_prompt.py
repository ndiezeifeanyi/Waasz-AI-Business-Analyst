from pathlib import Path

import pytest

from app.services.ai_extraction import AiExtractionService
from app.services.prompt_loader import load_prompt, render_prompt


def test_extraction_prompt_exists() -> None:
    prompt = Path("app/prompts/extraction.yaml")
    assert prompt.exists()
    assert "strict JSON" in prompt.read_text(encoding="utf-8")


def test_extraction_prompt_loads_and_renders() -> None:
    prompt = load_prompt("extraction")
    assert prompt["version"] == 1
    assert "strict JSON" in prompt["system"]
    _, user_prompt = render_prompt("extraction", message="Sold rice")
    assert "Sold rice" in user_prompt


@pytest.mark.asyncio
async def test_local_extraction_fallback_extracts_sale(sample_sale_message: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def mock_ai(*args, **kwargs):
        return None
    monkeypatch.setattr(AiExtractionService, "_extract_with_ai", mock_ai)
    record = await AiExtractionService().extract(sample_sale_message)
    assert record.record_type == "sale"
    assert record.item_name == "rice"
    assert record.quantity == 5
    assert record.amount == 250000
    assert record.needs_clarification is False


@pytest.mark.asyncio
async def test_local_extraction_fallback_requests_amount_for_expense(monkeypatch: pytest.MonkeyPatch) -> None:
    async def mock_ai(*args, **kwargs):
        return None
    monkeypatch.setattr(AiExtractionService, "_extract_with_ai", mock_ai)
    record = await AiExtractionService().extract("Bought fuel")
    assert record.record_type == "expense"
    assert record.needs_clarification is True
    assert record.clarification_question is not None
