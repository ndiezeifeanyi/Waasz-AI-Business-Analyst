from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest

from app.models.knowledge import UserKnowledgeChunk, UserKnowledgeDocument
from app.services.embedding_service import EmbeddingService
from app.services.knowledge_service import KnowledgeService, chunk_text


def test_chunk_text_splits_properly():
    text = (
        "Introduction to inventory.\n\n"
        "We have 50 bags of rice in the warehouse. Price is 45,000 NGN per bag.\n\n"
        "We also have 20 cartons of vegetable oil at 35,000 NGN per carton.\n\n"
        "Delivery takes 24 hours within Lagos."
    )
    chunks = chunk_text(text, max_chars=120, overlap=20)
    assert len(chunks) >= 2
    assert "warehouse" in "".join(chunks)


@pytest.mark.asyncio
async def test_store_user_document():
    user_id = uuid4()
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_embeddings = MagicMock()
    mock_embeddings.embed_documents = AsyncMock(return_value=[[0.1] * 768])

    service = KnowledgeService(embedding_service=mock_embeddings)
    doc = await service.store_user_document(
        db=mock_db,
        user_id=user_id,
        title="Price List 2026",
        content="Rice bag: 45000 NGN. Oil carton: 35000 NGN.",
        source_type="chat_upload",
    )

    assert doc.title == "Price List 2026"
    assert doc.user_id == user_id
    mock_db.add.assert_called()
    assert mock_db.flush.await_count >= 2


@pytest.mark.asyncio
async def test_cross_user_knowledge_isolation():
    """
    CRITICAL SECURITY TEST:
    Assert that User B querying the knowledge base CANNOT retrieve User A's private documents,
    even with matching query terms.
    """
    user_a = uuid4()
    user_b = uuid4()

    mock_db = AsyncMock()
    mock_embeddings = MagicMock()
    mock_embeddings.embed_query = AsyncMock(return_value=[0.1] * 768)

    service = KnowledgeService(embedding_service=mock_embeddings)

    # When user_b queries, mock_db.execute is called with parameters including {"user_id": user_b}
    result_mock = MagicMock()
    result_mock.fetchall.return_value = []  # No records for user_b
    mock_db.execute.return_value = result_mock

    matches_user_b = await service.search_user_knowledge(
        db=mock_db,
        user_id=user_b,
        query="What is the rice price?",
    )

    assert len(matches_user_b) == 0

    # Verify that the executed SQL statement parameter was bound strictly to user_b
    call_args = mock_db.execute.await_args
    sql_params = call_args[0][1]
    assert sql_params["user_id"] == user_b
    assert sql_params["user_id"] != user_a
