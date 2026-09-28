"""
Unit and integration tests for:
PART A — Image generation & editing (Gemini primary, OpenAI fallback, live resolution, daily image cap, WhatsApp delivery)
PART B — Live/current information (Isolated Gemini Google Search grounding, honest degradation, cost tracking)
"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from sqlalchemy import delete

from app.core.database import async_session_factory
from app.core.exceptions import CostLimitExceeded
from app.core.model_resolver import ModelResolver, model_resolver
from app.models.business import Business
from app.models.cost import AiCostEvent
from app.models.message import WhatsAppMessage
from app.models.user import User
from app.schemas.whatsapp import WhatsAppSendResult
from app.services.agent_service import AgentService
from app.services.cost_monitor import CostMonitor
from app.services.image_service import ImageService
from app.services.live_info_service import GROUNDING_FAIL_MESSAGE, LiveInformationService


# --------------------------------------------------------------------------
# PART A Tests: Model Resolver & Image Service Cascading
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_resolver_image_gen_resolution_and_env_override():
    resolver = ModelResolver()
    
    # 1. Defaults
    assert resolver.get_model("gemini", "image_gen") == "gemini-3.1-flash-image"
    assert resolver.get_model("openai", "image_gen") == "gpt-image-1"
    assert resolver.get_model("groq", "image_gen") == ""  # Groq has no image capability

    # 2. Env override takes precedence
    with patch.dict("os.environ", {"GEMINI_IMAGE_GEN_MODEL": "custom-imagen-test"}):
        assert resolver.get_model("gemini", "image_gen") == "custom-imagen-test"

    with patch.dict("os.environ", {"OPENAI_IMAGE_GEN_MODEL": "dall-e-custom"}):
        assert resolver.get_model("openai", "image_gen") == "dall-e-custom"


@pytest.mark.asyncio
async def test_image_service_generates_image_via_gemini():
    service = ImageService()
    fake_png = b"\x89PNG\r\n\x1a\nfakeimagebytes"
    
    with patch.object(service, "_call_gemini_generate", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = (fake_png, "image/png")
        
        img_bytes, mime = await service.generate_image("A cute puppy")
        assert img_bytes == fake_png
        assert mime == "image/png"
        mock_gemini.assert_awaited_once()


@pytest.mark.asyncio
async def test_image_service_cascades_from_gemini_to_openai_on_failure():
    service = ImageService()
    fake_openai_png = b"\x89PNG\r\n\x1a\nopenaitestbytes"

    with patch.object(service, "_call_gemini_generate", side_effect=Exception("Gemini 429 Quota Exceeded")):
        with patch.object(service, "_call_openai_generate", new_callable=AsyncMock) as mock_openai:
            mock_openai.return_value = (fake_openai_png, "image/png")

            img_bytes, mime = await service.generate_image("A red car on the highway")
            assert img_bytes == fake_openai_png
            assert mime == "image/png"
            mock_openai.assert_awaited_once()


@pytest.mark.asyncio
async def test_image_service_edit_cascades_gemini_to_openai():
    service = ImageService()
    orig_bytes = b"original_bytes"
    edited_bytes = b"edited_bytes"

    with patch.object(service, "_call_gemini_edit", side_effect=Exception("Gemini edit service unavailable")):
        with patch.object(service, "_call_openai_edit", new_callable=AsyncMock) as mock_openai_edit:
            mock_openai_edit.return_value = (edited_bytes, "image/png")

            res_bytes, mime = await service.edit_image("Make the sky blue", orig_bytes)
            assert res_bytes == edited_bytes
            assert mime == "image/png"
            mock_openai_edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cost_monitor_enforces_per_user_daily_image_limit():
    cost_monitor = CostMonitor()
    biz = Business(id=uuid4(), name="CostBiz", phone_number="+2348000000000", is_provisional=False)

    async with async_session_factory() as session:
        session.add(biz)
        await session.flush()
        try:
            # Record 3 image generation events
            for _ in range(3):
                await cost_monitor.record_cost(
                    db=session,
                    business_id=biz.id,
                    provider="gemini",
                    model="gemini-2.5-flash-image",
                    operation="image_gen",
                    input_tokens=0,
                    output_tokens=0,
                    estimated_cost_usd=Decimal("0.0400"),
                )
            await session.commit()

            # Under limit (cap = 5) -> passes
            await cost_monitor.ensure_user_image_budget(session, biz.id, limit=5)

            # Exceeds limit (cap = 3) -> raises CostLimitExceeded
            with pytest.raises(CostLimitExceeded) as exc_info:
                await cost_monitor.ensure_user_image_budget(session, biz.id, limit=3)
            assert "Daily image generation limit reached" in str(exc_info.value)
        finally:
            await session.execute(delete(AiCostEvent).where(AiCostEvent.business_id == biz.id))
            await session.execute(delete(Business).where(Business.id == biz.id))
            await session.commit()


# --------------------------------------------------------------------------
# PART A Tests: Agent Service WhatsApp Image Delivery & Tool Execution
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_tool_generate_image_uploads_and_sends_to_whatsapp():
    biz = Business(id=uuid4(), name="ImgBiz", phone_number="+2348000000001", is_provisional=False)
    user = User(id=uuid4(), business_id=biz.id, phone_number="+2348000000001", display_name="Chioma")

    fake_bytes = b"fake_png_image_content"
    mock_image_service = MagicMock(spec=ImageService)
    mock_image_service.generate_image = AsyncMock(return_value=(fake_bytes, "image/png"))

    mock_whatsapp = MagicMock()
    mock_whatsapp.upload_media = AsyncMock(return_value="meta_media_id_999")
    mock_whatsapp.send_image = AsyncMock(return_value=WhatsAppSendResult(message_id="wam_img_123"))

    agent = AgentService(
        image_service=mock_image_service,
        whatsapp=mock_whatsapp,
    )

    async with async_session_factory() as session:
        session.add(biz)
        session.add(user)
        await session.flush()
        try:
            result_text, was_delivered = await agent._execute_tool(
                db=session,
                business=biz,
                user=user,
                tool_name="generate_image",
                tool_args={"prompt": "A modern African bakery with fresh loaves"},
                raw_user_text="Generate an image of a bakery",
            )

            assert was_delivered is True
            assert "Image generated and sent directly" in result_text
            mock_image_service.generate_image.assert_awaited_once_with(
                "A modern African bakery with fresh loaves", db=session, business_id=biz.id
            )
            mock_whatsapp.upload_media.assert_awaited_once_with(fake_bytes, "image/png")
            mock_whatsapp.send_image.assert_awaited_once()
            assert mock_whatsapp.send_image.call_args[0][0] == user.phone_number
            assert mock_whatsapp.send_image.call_args[0][1] == "meta_media_id_999"
        finally:
            await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.business_id == biz.id))
            await session.execute(delete(User).where(User.id == user.id))
            await session.execute(delete(Business).where(Business.id == biz.id))
            await session.commit()


@pytest.mark.asyncio
async def test_agent_tool_edit_image_locates_reference_image():
    biz = Business(id=uuid4(), name="EditBiz", phone_number="+2348000000002", is_provisional=False)
    user = User(id=uuid4(), business_id=biz.id, phone_number="+2348000000002", display_name="Tunde")

    ref_bytes = b"initial_photo_bytes"
    edited_bytes = b"new_edited_bytes"

    mock_image_service = MagicMock(spec=ImageService)
    mock_image_service.edit_image = AsyncMock(return_value=(edited_bytes, "image/png"))

    mock_whatsapp = MagicMock()
    mock_whatsapp.upload_media = AsyncMock(return_value="meta_media_id_edited")
    mock_whatsapp.send_image = AsyncMock(return_value=WhatsAppSendResult(message_id="wam_edited_123"))

    agent = AgentService(
        image_service=mock_image_service,
        whatsapp=mock_whatsapp,
    )
    # Simulate turn 1 holding the inbound photo in recent image cache
    agent._recent_image_cache[user.id] = (ref_bytes, "image/jpeg")

    async with async_session_factory() as session:
        session.add(biz)
        session.add(user)
        await session.flush()
        try:
            result_text, was_delivered = await agent._execute_tool(
                db=session,
                business=biz,
                user=user,
                tool_name="edit_image",
                tool_args={"instruction": "Add a yellow chef hat to the baker"},
                raw_user_text="Add a yellow chef hat",
            )

            assert was_delivered is True
            assert "Edited image sent directly" in result_text
            mock_image_service.edit_image.assert_awaited_once_with(
                "Add a yellow chef hat to the baker",
                ref_bytes,
                reference_mime_type="image/jpeg",
                db=session,
                business_id=biz.id,
            )
            mock_whatsapp.send_image.assert_awaited_once()
        finally:
            await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.business_id == biz.id))
            await session.execute(delete(User).where(User.id == user.id))
            await session.execute(delete(Business).where(Business.id == biz.id))
            await session.commit()


# --------------------------------------------------------------------------
# PART B Tests: Live / Current Information with Isolated Google Search Grounding
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_information_service_executes_isolated_grounded_call():
    service = LiveInformationService()

    fake_response = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Nigeria won their recent football match 2-1 in Lagos."}]
                },
                "groundingMetadata": {
                    "webSearchQueries": ["Nigeria recent football match score"],
                    "groundingChunks": [
                        {
                            "web": {
                                "uri": "https://sportsnews.example.com/nigeria-match",
                                "title": "Nigeria Triumphs in Friendly",
                            }
                        }
                    ],
                },
            }
        ]
    }

    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = fake_response

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_http_resp

        result = await service.get_current_information("Who won the recent match?")
        assert "Nigeria won their recent football match" in result
        assert "Sources:" in result
        assert "Nigeria Triumphs in Friendly" in result
        mock_post.assert_awaited_once()
        # Verify isolated payload has only google_search and no other tools
        called_json = mock_post.call_args[1].get("json")
        assert "tools" in called_json
        assert called_json["tools"] == [{"google_search": {}}]


@pytest.mark.asyncio
async def test_live_information_service_grounding_failure_honest_degradation():
    service = LiveInformationService()

    # Simulate Gemini outage or 429 quota exhaustion
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 429
    mock_http_resp.text = "RESOURCE_EXHAUSTED"

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_http_resp

        result = await service.get_current_information("What are today's top stock headlines?")
        assert result == GROUNDING_FAIL_MESSAGE
        assert "I can't access live information right now" in result
