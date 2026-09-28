"""
Tests for Live Dynamic Model Resolution, Self-Healing on 404 Errors,
and Full Fallback Cascade Across Gemini, Groq, and OpenAI.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.core.exceptions import VoiceTranscriptionError
from app.core.model_resolver import (
    DEFAULT_MODELS,
    ModelResolver,
    is_model_not_found_error,
)
from app.services.ai_client import AiClient, ProviderConfig
from app.services.embedding_service import EmbeddingService
from app.services.qa_service import QaService
from app.services.voice_service import VoiceService


# --------------------------------------------------------------------------
# Step 1 & 4: Model Resolver Discovery, Caching, Env Overrides, & Resilience
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_resolver_env_override_takes_precedence():
    resolver = ModelResolver()
    with patch.dict("os.environ", {"GEMINI_CHAT_MODEL": "custom-gemini-test"}):
        model = resolver.get_model("gemini", "chat")
        assert model == "custom-gemini-test"

    with patch.dict("os.environ", {"GROQ_CHAT_MODEL": "custom-groq-test"}):
        model = resolver.get_model("groq", "chat")
        assert model == "custom-groq-test"

    with patch.dict("os.environ", {"OPENAI_CHAT_MODEL": "custom-openai-test"}):
        model = resolver.get_model("openai", "chat")
        assert model == "custom-openai-test"


@pytest.mark.asyncio
async def test_model_resolver_startup_resilience_broken_provider_does_not_crash():
    """
    If one provider's model list endpoint throws or returns an error,
    refresh_all() logs a warning and falls back to safe defaults without crashing.
    """
    resolver = ModelResolver()

    # Simulate Gemini failing with a network error, Groq succeeding, OpenAI failing with 500
    with patch.object(resolver, "_resolve_gemini", side_effect=Exception("Connection refused")):
        with patch.object(resolver, "_resolve_groq", return_value="openai/gpt-oss-120b"):
            with patch.object(resolver, "_resolve_openai", side_effect=Exception("500 Internal Error")):
                results = await resolver.refresh_all()

                assert "gemini_chat" in results
                assert results["gemini_chat"] == "gemini-3.8-flash (default)"
                assert resolver.get_model("gemini", "chat") == "gemini-3.8-flash"

                assert results["groq_chat"] == "openai/gpt-oss-120b"
                assert resolver.get_model("groq", "chat") == "openai/gpt-oss-120b"

                assert results["openai_chat"] == "gpt-4o-mini (default)"
                assert resolver.get_model("openai", "chat") == "gpt-4o-mini"


# --------------------------------------------------------------------------
# Step 2: Self-Healing on Mid-Run 404 Model Failures
# --------------------------------------------------------------------------

def test_is_model_not_found_error_detection():
    assert is_model_not_found_error(Exception("404: model not found")) is True
    assert is_model_not_found_error(Exception("model_decommissioned")) is True
    assert is_model_not_found_error(Exception("The model `gemini-1.5-flash` does not exist")) is True
    assert is_model_not_found_error(Exception("429 rate limit exceeded")) is False
    assert is_model_not_found_error(Exception("401 invalid api key")) is False
    assert is_model_not_found_error(None) is False


@pytest.mark.asyncio
async def test_ai_client_self_heals_on_404_error():
    """
    When a call fails with a 404 model not found error, AiClient triggers self-healing,
    re-resolves to a newer model, and retries the call before falling back.
    """
    client = AiClient()
    call_counts = {"attempts": 0}

    async def mock_call(provider, sys_p, user_p):
        call_counts["attempts"] += 1
        if call_counts["attempts"] == 1:
            # First attempt with stale model fails with 404
            raise Exception("404: Model 'stale-gemini' was decommissioned and not found")
        # Second attempt with healed model succeeds
        return '{"record_type": "sale", "item_name": "healed item", "amount": 1000}'

    with patch.object(client, "provider_chain", return_value=[ProviderConfig("gemini", "stale-gemini", "dummy_key")]):
        with patch.object(client, "_call_provider", side_effect=mock_call):
            with patch("app.core.model_resolver.model_resolver.handle_mid_run_failure", new_callable=AsyncMock) as mock_heal:
                mock_heal.return_value = (True, "gemini-3.8-flash")
                res = await client.complete_json("system", "user", operation="test_heal")

                assert res is not None
                assert res.content["item_name"] == "healed item"
                assert call_counts["attempts"] == 2
                mock_heal.assert_awaited_once()


# --------------------------------------------------------------------------
# Step 3: Failure Type Cascade Matrix (401, 429 quota, 429 rate, 404, timeout)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "error_cause",
    [
        Exception("401: Invalid or expired API key"),
        Exception("403: Forbidden - permissions missing"),
        Exception("429: You exceeded your current quota, please check your plan and billing details"),
        Exception("429: Rate limit reached. Requests per minute exceeded"),
        Exception("404: Model not found"),
        asyncio.TimeoutError("Request timed out"),
        ConnectionError("Network unreachable"),
    ],
)
@pytest.mark.asyncio
async def test_ai_client_cascades_across_all_failure_types(error_cause):
    """
    Verify for all failure types that when Gemini fails, it cascades to Groq,
    and if Groq fails, it cascades to OpenAI.
    """
    client = AiClient()
    providers_called = []

    async def mock_call(provider, sys_p, user_p):
        providers_called.append(provider.provider)
        if provider.provider == "gemini":
            raise error_cause
        if provider.provider == "groq":
            raise error_cause
        if provider.provider == "openai":
            return '{"record_type": "sale", "item_name": "final fallback", "amount": 5000}'
        raise ValueError(f"Unknown {provider}")

    chain = [
        ProviderConfig("gemini", "gemini-2.5-flash", "k1"),
        ProviderConfig("groq", "openai/gpt-oss-120b", "k2"),
        ProviderConfig("openai", "gpt-4o-mini", "k3"),
    ]

    with patch.object(client, "provider_chain", return_value=chain):
        with patch.object(client, "_call_provider", side_effect=mock_call):
            with patch("app.core.model_resolver.model_resolver.handle_mid_run_failure", new_callable=AsyncMock) as mock_heal:
                # If 404, mock heal returns False so it cascades
                mock_heal.return_value = (False, "")
                res = await client.complete_json("system", "user", operation="test_cascade")

                assert res is not None
                assert res.provider == "openai"
                assert res.content["item_name"] == "final fallback"
                assert providers_called == ["gemini", "groq", "openai"]


# --------------------------------------------------------------------------
# Step 5: Audio Transcription Fallback Chain (Groq Whisper <-> OpenAI Whisper)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_voice_service_openai_primary_success():
    service = VoiceService()
    with patch("app.services.voice_service.settings.openai_api_key", "valid_openai_key"):
        with patch("openai.AsyncOpenAI") as mock_oai_cls:
            mock_client = MagicMock()
            mock_client.audio.transcriptions.create = AsyncMock(return_value="OpenAI transcribed text")
            mock_oai_cls.return_value = mock_client

            text = await service.transcribe(b"valid_audio_bytes")
            assert text == "OpenAI transcribed text"


@pytest.mark.asyncio
async def test_voice_service_cascades_from_openai_to_groq_on_failure():
    """
    When OpenAI Whisper fails (e.g. 429 quota/billing, auth, timeout), VoiceService
    immediately cascades to Groq Whisper without disrupting the user.
    """
    service = VoiceService()
    with patch("app.services.voice_service.settings.openai_api_key", "valid_openai_key"):
        with patch("app.services.voice_service.settings.groq_api_key", "valid_groq_key"):
            with patch("openai.AsyncOpenAI") as mock_oai_cls:
                mock_oai = MagicMock()
                mock_oai.audio.transcriptions.create = AsyncMock(
                    side_effect=Exception("OpenAI 429 You exceeded your current quota, please check your plan and billing details.")
                )
                mock_oai_cls.return_value = mock_oai

                with patch("groq.AsyncGroq") as mock_groq_cls:
                    mock_groq = MagicMock()
                    mock_groq.audio.transcriptions.create = AsyncMock(return_value="Groq fallback transcribed text")
                    mock_groq_cls.return_value = mock_groq

                    text = await service.transcribe(b"valid_audio_bytes")
                    assert text == "Groq fallback transcribed text"


@pytest.mark.asyncio
async def test_voice_service_both_fail_raises_honest_error():
    """
    When both OpenAI Whisper and Groq Whisper fail, VoiceService raises
    VoiceTranscriptionError with an honest, plain-language explanation.
    """
    service = VoiceService()
    with patch("app.services.voice_service.settings.openai_api_key", "valid_openai_key"):
        with patch("app.services.voice_service.settings.groq_api_key", "valid_groq_key"):
            with patch("openai.AsyncOpenAI") as mock_oai_cls:
                mock_oai = MagicMock()
                mock_oai.audio.transcriptions.create = AsyncMock(side_effect=Exception("OpenAI 429 insufficient_quota"))
                mock_oai_cls.return_value = mock_oai

                with patch("groq.AsyncGroq") as mock_groq_cls:
                    mock_groq = MagicMock()
                    mock_groq.audio.transcriptions.create = AsyncMock(side_effect=Exception("Groq quota exceeded"))
                    mock_groq_cls.return_value = mock_groq

                    with pytest.raises(VoiceTranscriptionError, match="credits required"):
                        await service.transcribe(b"valid_audio_bytes")


@pytest.mark.asyncio
async def test_needs_edit_genuine_edit_reply_not_diverted_by_intent_router():
    """
    Verify that when a transaction confirmation is in 'needs_edit' status,
    a genuine edit reply like 'Change amount to 5000' or 'Make it 5000' is NOT
    diverted to general Q&A by intent classification, but processed as an edit.
    Conversely, if the user explicitly sends an unrelated command (e.g. 'Remind me tomorrow...'),
    it cleanly diverts.
    """
    from app.services.webhook_processor import WhatsAppWebhookProcessor
    from app.schemas.whatsapp import ParsedWhatsAppMessage
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    processor = WhatsAppWebhookProcessor()
    processor.extractor = MagicMock()
    processor.extractor.extract = AsyncMock()
    processor.extractor.last_provider = "groq"
    processor.extractor.last_model = "gpt-oss-120b"
    processor.extractor.last_input_tokens = 50
    processor.extractor.last_output_tokens = 20
    processor.extractor.last_estimated_cost_usd = 0.0001

    mock_db = AsyncMock()
    pending = MagicMock()
    pending.status = "needs_edit"
    pending.business_id = uuid4()
    pending.source_message_id = uuid4()

    mock_corrected_data = MagicMock()
    mock_corrected_data.needs_clarification = False
    processor.extractor.extract.return_value = mock_corrected_data

    processor.ledger = MagicMock()
    processor.ledger.create_ai_extraction = AsyncMock()
    mock_new_conf = MagicMock()
    mock_new_conf.confirmation_text = "Updated: Amount changed to 5000. Reply YES to confirm."
    processor.ledger.stage_record_for_confirmation = AsyncMock(return_value=mock_new_conf)

    # 1. Genuine edit reply: 'Change amount to 5000'
    edit_msg = ParsedWhatsAppMessage(
        from_phone="2348011111111",
        body="Change amount to 5000",
        message_id="msg_edit_1",
        message_type="text",
    )
    result = await processor._try_handle_confirmation_reply(mock_db, edit_msg, pending)
    assert result == "Updated: Amount changed to 5000. Reply YES to confirm."
    assert pending.status == "corrected"

    # 2. Genuine edit reply: 'Make it 3000'
    pending.status = "needs_edit"
    edit_msg2 = ParsedWhatsAppMessage(
        from_phone="2348011111111",
        body="Make it 3000",
        message_id="msg_edit_2",
        message_type="text",
    )
    result2 = await processor._try_handle_confirmation_reply(mock_db, edit_msg2, pending)
    assert result2 == "Updated: Amount changed to 5000. Reply YES to confirm."

    # 3. Unrelated explicit command: 'Remind me tomorrow at 9am to call vendor'
    pending.status = "needs_edit"
    unrelated_msg = ParsedWhatsAppMessage(
        from_phone="2348011111111",
        body="Remind me tomorrow at 9am to call vendor",
        message_id="msg_unrelated_1",
        message_type="text",
    )
    unrelated_result = await processor._try_handle_confirmation_reply(mock_db, unrelated_msg, pending)
    # Must return None so intent router picks up the reminder!
    assert unrelated_result is None
    assert pending.status == "needs_edit"


# --------------------------------------------------------------------------
# Step 5: Embeddings Dynamic Fallback & Self-Healing
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embedding_service_self_heals_on_404():
    service = EmbeddingService(api_key="valid_key")
    call_counts = {"attempts": 0}

    class MockGeminiEmbeddings:
        def __init__(self, model, google_api_key):
            self.model = model

        async def aembed_documents(self, texts):
            call_counts["attempts"] += 1
            if self.model == "stale-embedding-model":
                raise Exception("404: models/stale-embedding-model is not found")
            return [[0.2] * 768 for _ in texts]

    with patch("app.services.embedding_service.GoogleGenerativeAIEmbeddings", side_effect=MockGeminiEmbeddings):
        with patch("app.core.model_resolver.model_resolver.handle_mid_run_failure", new_callable=AsyncMock) as mock_heal:
            mock_heal.return_value = (True, "models/gemini-embedding-001")
            with patch.object(service, "_custom_model", "stale-embedding-model"):
                vectors, is_fallback = await service.embed_documents_with_meta(["Test embedding document"])

                assert is_fallback is False
                assert len(vectors) == 1
                assert len(vectors[0]) == 768
                mock_heal.assert_awaited_once()
