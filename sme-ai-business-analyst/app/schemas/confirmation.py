from pydantic import BaseModel


class ConfirmationDecision(BaseModel):
    token: str
    decision: str
    correction_text: str | None = None
