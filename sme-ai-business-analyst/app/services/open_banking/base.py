from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass
class BankAccountInfo:
    account_id: str
    account_number: str
    account_name: str
    bank_name: str
    bank_code: str
    currency: str
    balance: Decimal
    bvn_last4: str | None = None


@dataclass
class BankTransaction:
    provider_tx_id: str
    account_id: str
    amount: Decimal
    transaction_type: str  # "credit" or "debit"
    narration: str
    occurred_at: datetime
    balance_after: Decimal | None = None
    is_pos_settlement: bool = False
    pos_terminal_id: str | None = None
    raw_payload: dict[str, Any] | None = None


@dataclass
class POSSettlementRecord:
    pos_provider: str  # "MONIEPOINT", "OPAY", "PALMPAY", "UNKNOWN"
    gross_volume: Decimal
    net_payout: Decimal
    service_charge: Decimal
    settlement_reference: str
    settled_at: datetime
    terminal_id: str | None = None


class OpenBankingAdapter(ABC):
    """
    Provider-agnostic contract for Nigerian Open Banking providers (Mono, Zeeh Africa, Indicina).
    Insulates Waasz from any single vendor shutdown or API change.
    """

    @abstractmethod
    async def create_auth_link(
        self,
        business_id: UUID,
        phone_number: str,
        redirect_url: str | None = None,
    ) -> str:
        """Generates a secure auth/widget link for merchant bank account authorization."""
        pass

    @abstractmethod
    async def exchange_token(self, auth_code: str) -> str:
        """Exchanges temporary auth code for permanent account ID or access token."""
        pass

    @abstractmethod
    async def get_account_details(self, account_id: str) -> BankAccountInfo:
        """Fetches verified bank account details and current balance."""
        pass

    @abstractmethod
    async def get_transactions(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime,
    ) -> list[BankTransaction]:
        """Fetches historical statements for underwriting and cash flow verification."""
        pass

    @abstractmethod
    async def parse_webhook(self, payload: dict[str, Any]) -> list[BankTransaction]:
        """Parses real-time transaction webhook events from provider."""
        pass
