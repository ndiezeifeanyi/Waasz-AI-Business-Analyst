from datetime import date

from pydantic import BaseModel


class ReportRequest(BaseModel):
    business_id: int
    period_start: date
    period_end: date
    report_type: str
