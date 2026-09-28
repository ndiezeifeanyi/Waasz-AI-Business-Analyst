import pytest
import uuid
import matplotlib
matplotlib.use("Agg")
from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete
from app.core.database import async_session_factory
from app.models import Business, User, Transaction
from app.services.unified_report_service import UnifiedReportService
from app.services.visual_reports import VisualReportService

import random
import time

@pytest.mark.asyncio
async def test_visual_reports_generation_and_cadences():
    test_id = f"{random.randint(100000, 999999)}"
    test_phone = f"2347088{test_id}"

    async with async_session_factory() as async_session:
        # 1. Create a business with multiple months of transactions
        biz = Business(
            name=f"Chart Test Enterprise {test_id}",
            currency="NGN",
            business_type="retail",
            phone_number=test_phone,
        )
        async_session.add(biz)
        await async_session.flush()

        now = datetime.now(UTC)

        user = User(
            business_id=biz.id,
            phone_number=test_phone,
            display_name="Tester Chart",
            role="owner",
            locale="en-NG",
            last_inbound_at=now,
        )
        async_session.add(user)
        await async_session.flush()

        # Add transactions spanning last 60 days
        t1 = Transaction(
            business_id=biz.id,
            amount=150000.0,
            transaction_type="sale",
            item_name="Sales Item A",
            status="confirmed",
            occurred_at=now - timedelta(days=45),
            description="Sale 45d ago",
        )
        t2 = Transaction(
            business_id=biz.id,
            amount=50000.0,
            transaction_type="expense",
            item_name="Supplies",
            status="confirmed",
            occurred_at=now - timedelta(days=40),
            description="Expense 40d ago",
        )
        t3 = Transaction(
            business_id=biz.id,
            amount=250000.0,
            transaction_type="sale",
            item_name="Sales Item B",
            status="confirmed",
            occurred_at=now - timedelta(days=10),
            description="Sale 10d ago",
        )
        t4 = Transaction(
            business_id=biz.id,
            amount=30000.0,
            transaction_type="expense",
            item_name="Logistics",
            status="confirmed",
            occurred_at=now - timedelta(days=5),
            description="Expense 5d ago",
        )
        async_session.add_all([t1, t2, t3, t4])
        await async_session.commit()

        try:
            # 2. Test VisualReportService generate_trend_chart directly
            visual_service = VisualReportService()
            png_bytes_monthly = await visual_service.generate_trend_chart(async_session, str(biz.id), days=30)
            assert png_bytes_monthly is not None
            assert len(png_bytes_monthly) > 0
            assert png_bytes_monthly[:4] == b"\x89PNG"

            png_bytes_quarterly = await visual_service.generate_trend_chart(async_session, str(biz.id), days=90)
            assert png_bytes_quarterly is not None
            assert len(png_bytes_quarterly) > 0
            assert png_bytes_quarterly[:4] == b"\x89PNG"

            # 3. Test UnifiedReportService delivers both text report and chart image for monthly cadence
            report_service = UnifiedReportService()

            from app.schemas.whatsapp import WhatsAppSendResult

            with patch.object(report_service.whatsapp, "send_text", new_callable=AsyncMock) as mock_send_text, \
                 patch.object(report_service.whatsapp, "upload_media", new_callable=AsyncMock) as mock_upload, \
                 patch.object(report_service.whatsapp, "send_image", new_callable=AsyncMock) as mock_send_image:

                mock_send_text.side_effect = lambda *args, **kwargs: WhatsAppSendResult(message_id=f"wamid.text_{uuid.uuid4().hex[:8]}")
                mock_upload.side_effect = lambda *args, **kwargs: f"media.img_{uuid.uuid4().hex[:8]}"
                mock_send_image.side_effect = lambda *args, **kwargs: WhatsAppSendResult(message_id=f"wamid.img_{uuid.uuid4().hex[:8]}")

                # Generate and deliver monthly report
                delivered = await report_service.generate_and_deliver_report(
                    async_session,
                    user_id=user.id,
                    cadence="monthly",
                )

                assert delivered.get("status") == "sent"
                assert delivered.get("chart", {}).get("delivered") is True
                # Verify text summary was delivered
                assert mock_send_text.called
                text_args, text_kwargs = mock_send_text.call_args
                assert user.phone_number in text_args

                # Verify chart was uploaded and sent via send_image
                assert mock_upload.called
                assert mock_send_image.called
                img_args, img_kwargs = mock_send_image.call_args
                assert img_args[0] == user.phone_number
                sent_media_id = img_kwargs.get("media_id") or (img_args[1] if len(img_args) > 1 else None)
                assert sent_media_id is not None
                assert "media.img_" in sent_media_id

                # Verify quarterly cadence works as well
                mock_send_text.reset_mock()
                mock_upload.reset_mock()
                mock_send_image.reset_mock()

                delivered_q = await report_service.generate_and_deliver_report(
                    async_session,
                    user_id=user.id,
                    cadence="quarterly",
                )
                assert delivered_q.get("status") == "sent"
                assert delivered_q.get("chart", {}).get("delivered") is True
                assert mock_send_text.called
                assert mock_upload.called
                assert mock_send_image.called

                # Verify yearly cadence works as well
                mock_send_text.reset_mock()
                mock_upload.reset_mock()
                mock_send_image.reset_mock()

                delivered_y = await report_service.generate_and_deliver_report(
                    async_session,
                    user_id=user.id,
                    cadence="yearly",
                )
                assert delivered_y.get("status") == "sent"
                assert delivered_y.get("chart", {}).get("delivered") is True
                assert mock_send_text.called
                assert mock_upload.called
                assert mock_send_image.called
        finally:
            # Clean up
            await async_session.execute(delete(Transaction).where(Transaction.business_id == biz.id))
            await async_session.execute(delete(User).where(User.business_id == biz.id))
            await async_session.execute(delete(Business).where(Business.id == biz.id))
            await async_session.commit()
