from typing import Literal
from app.services.open_banking.base import OpenBankingAdapter
from app.services.open_banking.mono_adapter import MonoOpenBankingAdapter
from app.services.open_banking.pos_settlement_parser import POSSettlementParser


class OpenBankingManager:
    """
    Factory and orchestration manager for Nigerian Open Banking adapters.
    Defaults to Mono and provides transparent fallback.
    """

    def __init__(self, provider: Literal["mono", "zeeh", "mock"] = "mono") -> None:
        self.provider = provider
        self.pos_parser = POSSettlementParser()

    def get_adapter(self) -> OpenBankingAdapter:
        if self.provider == "mono":
            return MonoOpenBankingAdapter()
        # Default fallback to Mono adapter
        return MonoOpenBankingAdapter()
