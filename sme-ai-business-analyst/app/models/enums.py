"""
PostgreSQL Enum types for SQLAlchemy models.
All enums set create_type=False to map directly to existing PostgreSQL schema types.
"""

from sqlalchemy.dialects.postgresql import ENUM as PgEnum

UserRoleEnum = PgEnum(
    "owner",
    "staff",
    "admin",
    name="user_role",
    create_type=False,
)

SubscriptionPlanEnum = PgEnum(
    "free",
    "pro",
    "pilot",
    name="subscription_plan",
    create_type=False,
)

MessageDirectionEnum = PgEnum(
    "inbound",
    "outbound",
    name="message_direction",
    create_type=False,
)

WhatsAppMessageTypeEnum = PgEnum(
    "text",
    "image",
    "audio",
    "document",
    "interactive",
    "button",
    "unknown",
    name="whatsapp_message_type",
    create_type=False,
)

MessageStatusEnum = PgEnum(
    "received",
    "queued",
    "processed",
    "failed",
    "sent",
    "delivered",
    "read",
    name="message_status",
    create_type=False,
)

BusinessRecordTypeEnum = PgEnum(
    "sale",
    "expense",
    "inventory_update",
    "unknown",
    name="business_record_type",
    create_type=False,
)

ExtractionStatusEnum = PgEnum(
    "pending",
    "succeeded",
    "failed",
    "needs_clarification",
    "blocked_by_cost",
    name="extraction_status",
    create_type=False,
)

ConfirmationStatusEnum = PgEnum(
    "pending",
    "confirmed",
    "needs_edit",
    "corrected",
    "expired",
    "rejected",
    name="confirmation_status",
    create_type=False,
)

TransactionStatusEnum = PgEnum(
    "pending_confirmation",
    "confirmed",
    "rejected",
    "void",
    name="transaction_status",
    create_type=False,
)

InventoryMovementTypeEnum = PgEnum(
    "stock_in",
    "stock_out",
    "adjustment",
    name="inventory_movement_type",
    create_type=False,
)

MediaStatusEnum = PgEnum(
    "pending",
    "downloaded",
    "processed",
    "failed",
    name="media_status",
    create_type=False,
)

ReportTypeEnum = PgEnum(
    "daily",
    "weekly",
    name="report_type",
    create_type=False,
)

ReportStatusEnum = PgEnum(
    "generated",
    "sent",
    "failed",
    name="report_status",
    create_type=False,
)

AiProviderEnum = PgEnum(
    "gemini",
    "groq",
    "openai",
    "google_vision",
    "tesseract",
    "whisper",
    "local_heuristic",
    name="ai_provider",
    create_type=False,
)

AiOperationEnum = PgEnum(
    "extraction",
    "confirmation",
    "correction",
    "report",
    "ocr",
    "voice",
    "image_gen",
    "grounded_search",
    name="ai_operation",
    create_type=False,
)
