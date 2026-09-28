"""
Tests for Audit Closing Items:
1. Voice transcription honest failure behavior (no swallowed errors, plain-language notices).
2. Local deterministic hash embeddings tagged with needs_reembedding=True and re-embedded via service/admin.
3. Pending confirmation replies correctly passing through unrelated messages to intent classification.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from app.core.exceptions import VoiceTranscriptionError
from app.models.business import Business
from app.models.confirmation import Confirmation
from app.models.knowledge import UserKnowledgeChunk, UserKnowledgeDocument
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage
from app.services.confirmation_service import (
    ConfirmationService,
    normalize_confirmation_reply,
)
from app.services.embedding_service import EmbeddingService
from app.services.knowledge_service import KnowledgeService
from app.services.voice_service import VoiceService
from app.services.webhook_processor import WhatsAppWebhookProcessor


# --------------------------------------------------------------------------
# Item 1: Voice transcription failure behavior
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_voice_service_empty_audio_raises_honest_error():
    service = VoiceService()
    with pytest.raises(VoiceTranscriptionError, match="Empty audio payload"):
        await service.transcribe(b"")


@pytest.mark.asyncio
async def test_voice_service_missing_or_placeholder_key_raises_honest_error():
    service = VoiceService()
    with patch("app.services.voice_service.settings.groq_api_key", ""):
        with patch("app.services.voice_service.settings.openai_api_key", ""):
            with pytest.raises(VoiceTranscriptionError, match="transcription services are unconfigured"):
                await service.transcribe(b"fake_audio_bytes")

        with patch("app.services.voice_service.settings.openai_api_key", "placeholder_openai_key"):
            with pytest.raises(VoiceTranscriptionError, match="transcription services are unconfigured"):
                await service.transcribe(b"fake_audio_bytes")


@pytest.mark.asyncio
async def test_voice_service_quota_exhausted_raises_honest_error():
    service = VoiceService()
    with patch("app.services.voice_service.settings.groq_api_key", ""):
        with patch("app.services.voice_service.settings.openai_api_key", "sk-valid-key"):
            with patch("openai.AsyncOpenAI") as mock_openai_cls:
                mock_client = MagicMock()
                mock_client.audio.transcriptions.create = AsyncMock(
                    side_effect=Exception("Error code: 429 - {'error': {'message': 'You exceeded your current quota', 'code': 'insufficient_quota'}}")
                )
                mock_openai_cls.return_value = mock_client

                with pytest.raises(VoiceTranscriptionError, match="credits required"):
                    await service.transcribe(b"fake_audio_bytes")


@pytest.mark.asyncio
async def test_webhook_processor_voice_error_returns_honest_warning():
    mock_ledger = AsyncMock()
    biz = Business(id=uuid4(), name="Test Biz", phone_number="+2348000000000")
    user = User(id=uuid4(), business_id=biz.id, phone_number="+2348000000000", niche="personal")
    mock_ledger.get_or_create_business_and_user.return_value = (biz, user)
    mock_ledger.record_inbound_message.return_value = MagicMock(id=uuid4())
    mock_ledger.record_outbound_message.return_value = MagicMock(id=uuid4())

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=MagicMock(message_id="msg_out_1"))

    mock_media = AsyncMock()
    mock_media.download.return_value = b"fake_audio_bytes"

    mock_voice = AsyncMock()
    mock_voice.transcribe.side_effect = VoiceTranscriptionError(
        "Voice note transcription is currently unavailable (OpenAI billing credits required). Please type your message instead."
    )

    mock_confirmations = AsyncMock()
    mock_confirmations.latest_actionable.return_value = None

    processor = WhatsAppWebhookProcessor(
        ledger=mock_ledger,
        whatsapp=mock_whatsapp,
        media_downloader=mock_media,
        voice=mock_voice,
        confirmations=mock_confirmations,
    )

    parsed = ParsedWhatsAppMessage(
        message_id="wamid.voice1",
        from_phone="+2348000000000",
        timestamp=int(datetime.now(UTC).timestamp()),
        message_type="audio",
        media_id="media_123",
        body="",
    )

    mock_db = AsyncMock()
    await processor.process_message(mock_db, parsed, MagicMock())

    # Confirm user received honest plain-language warning
    mock_whatsapp.send_text.assert_awaited_once()
    call_args = mock_whatsapp.send_text.call_args[0]
    recipient, sent_text = call_args[0], call_args[1]
    assert recipient == "+2348000000000"
    assert "⚠️" in sent_text
    assert "OpenAI billing credits required" in sent_text
    mock_ledger.record_outbound_message.assert_awaited_once()


# --------------------------------------------------------------------------
# Item 2: Embedding deterministic fallback & needs_reembedding flag
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embedding_service_meta_and_needs_reembedding_flag():
    # When keys are placeholders or absent, fallback is used and returns is_fallback=True
    service = EmbeddingService(api_key="placeholder_gemini_key")
    with patch("app.services.embedding_service.settings.openai_api_key", "placeholder_openai_key"):
        vectors, is_fallback = await service.embed_documents_with_meta(["First document chunk", "Second chunk"])
        assert is_fallback is True
        assert len(vectors) == 2
        assert len(vectors[0]) == 768


@pytest.mark.asyncio
async def test_knowledge_service_store_sets_needs_reembedding_on_fallback():
    mock_embeddings = AsyncMock()
    mock_embeddings.embed_documents_with_meta.return_value = (
        [[0.1] * 768],
        True,  # is_fallback = True
    )

    ks = KnowledgeService(embedding_service=mock_embeddings)
    mock_db = AsyncMock()
    user_id = uuid4()
    biz_id = uuid4()

    doc = await ks.store_user_document(
        db=mock_db,
        user_id=user_id,
        business_id=biz_id,
        title="Price List",
        content="Indomie is 7000 NGN per carton.",
        source_type="chat_upload",
    )

    # Verify chunks added to DB have needs_reembedding=True
    added_objects = [call[0][0] for call in mock_db.add.call_args_list]
    chunks = [o for o in added_objects if isinstance(o, UserKnowledgeChunk)]
    assert len(chunks) == 1
    assert chunks[0].needs_reembedding is True
    assert chunks[0].extra_metadata.get("needs_reembedding") is True


@pytest.mark.asyncio
async def test_knowledge_service_reembed_flagged_chunks_updates_records():
    # Setup mock chunks flagged with needs_reembedding=True
    chunk1 = UserKnowledgeChunk(
        id=uuid4(),
        document_id=uuid4(),
        user_id=uuid4(),
        chunk_content="Indomie is 7000 NGN.",
        needs_reembedding=True,
        extra_metadata={"needs_reembedding": True},
    )

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [chunk1]
    mock_db.execute.return_value = mock_result

    # Mock an active provider recovering (is_fallback=False)
    mock_embeddings = AsyncMock()
    real_vec = [0.05] * 768
    mock_embeddings.embed_documents_with_meta.return_value = ([real_vec], False)

    ks = KnowledgeService(embedding_service=mock_embeddings)
    count = await ks.reembed_flagged_chunks(mock_db)

    assert count == 1
    assert chunk1.needs_reembedding is False
    assert chunk1.extra_metadata["needs_reembedding"] is False
    assert chunk1.embedding == real_vec
    mock_db.commit.assert_awaited_once()


# --------------------------------------------------------------------------
# Item 3: Pending confirmation & unrelated message pass-through
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pending_confirmation_unrelated_message_passes_through():
    """
    When a confirmation is pending (status='pending') and the user sends an unrelated message
    such as 'remind me tomorrow at 5pm', _try_handle_confirmation_reply returns None,
    allowing the message to pass through to intent classification.
    """
    biz_id = uuid4()
    user_id = uuid4()
    user = User(id=user_id, business_id=biz_id, phone_number="+2348011112222", niche="personal")
    business = Business(id=biz_id, name="Personal", phone_number="+2348011112222")

    pending = Confirmation(
        id=uuid4(),
        business_id=biz_id,
        status="pending",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        extracted_record={"item_name": "rice", "amount": 250000},
        confirmation_text="I understood: Sold rice for ₦250,000. Reply 1=Yes, 2=Edit.",
    )

    mock_confirmations = AsyncMock()
    mock_confirmations.latest_actionable.return_value = pending

    mock_ledger = AsyncMock()
    mock_ledger.get_or_create_business_and_user.return_value = (business, user)
    mock_ledger.record_inbound_message.return_value = MagicMock(id=uuid4())
    mock_ledger.record_outbound_message.return_value = MagicMock(id=uuid4())

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=MagicMock(message_id="msg_remind_1"))

    processor = WhatsAppWebhookProcessor(
        ledger=mock_ledger,
        confirmations=mock_confirmations,
        whatsapp=mock_whatsapp,
    )

    unrelated_msg = ParsedWhatsAppMessage(
        message_id="wamid.unrelated1",
        from_phone="+2348011112222",
        timestamp=int(datetime.now(UTC).timestamp()),
        message_type="text",
        body="remind me tomorrow at 5pm to call doctor",
    )

    mock_db = AsyncMock()
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    with patch("app.services.task_service.TaskService.create_task", new_callable=AsyncMock) as mock_task_create:
        mock_task_create.return_value = MagicMock(id=uuid4(), title="call doctor")
        await processor.process_message(mock_db, unrelated_msg, MagicMock())

        # Verify task was created and reminder question sent
        mock_task_create.assert_awaited_once()
        mock_whatsapp.send_text.assert_awaited()
        sent_body = mock_whatsapp.send_text.call_args[0][1]
        assert "What time should I remind you?" in sent_body or "call doctor" in sent_body


@pytest.mark.asyncio
async def test_needs_edit_confirmation_unrelated_message_passes_through():
    """
    When a confirmation is in 'needs_edit' status and the user sends an unrelated command
    (e.g. 'what is the capital of France?' or 'remind me in 2 hours'),
    it must NOT be erroneously extracted as a transaction edit, but passed through.
    """
    biz_id = uuid4()
    pending = Confirmation(
        id=uuid4(),
        business_id=biz_id,
        status="needs_edit",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    processor = WhatsAppWebhookProcessor()
    unrelated_msg = ParsedWhatsAppMessage(
        message_id="wamid.qa1",
        from_phone="+2348011112222",
        timestamp=int(datetime.now(UTC).timestamp()),
        message_type="text",
        body="remind me tomorrow at 3pm",
    )

    mock_db = AsyncMock()
    # Should detect as unrelated intent and return None
    res = await processor._try_handle_confirmation_reply(mock_db, unrelated_msg, pending)
    assert res is None


@pytest.mark.asyncio
async def test_confirmation_cancel_discards_pending_record():
    """
    When a user with a pending confirmation sends 'cancel', 'stop', or 'abort',
    the confirmation is transitioned to 'cancelled' and a discard confirmation is returned.
    """
    biz_id = uuid4()
    pending = Confirmation(
        id=uuid4(),
        business_id=biz_id,
        status="pending",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    conf_service = ConfirmationService()
    processor = WhatsAppWebhookProcessor(confirmations=conf_service)

    cancel_msg = ParsedWhatsAppMessage(
        message_id="wamid.cancel1",
        from_phone="+2348011112222",
        timestamp=int(datetime.now(UTC).timestamp()),
        message_type="text",
        body="cancel",
    )

    mock_db = AsyncMock()
    res = await processor._try_handle_confirmation_reply(mock_db, cancel_msg, pending)
    assert res == "Record discarded. What else can I help you with?"
    assert pending.status == "cancelled"
