from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ActorContext:
    """
    Immutable actor context resolved strictly server-side from the verified WhatsApp sender.
    STRICT GUARDRAIL: Models never provide or see tenant IDs or member IDs.
    """
    business_id: UUID
    member_id: UUID
    role: str  # 'owner', 'manager', 'staff'
    wa_id: str  # E.164 phone number
    display_name: str | None = None
