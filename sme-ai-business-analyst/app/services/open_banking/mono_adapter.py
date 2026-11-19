from datetime import datetime
from decimal import Decimal
import logging
from typing import Any
from uuid import UUID

import httpx

from app.core.config import settings
from app.services.open_banking.base import (
    BankAccountInfo,
    BankTransaction,
    OpenBankingAdapter,
)

logger = logging.getLogger(__name__)


class MonoOpenBankingAdapter(OpenBankingAdapter):
    """
    Open Banking adapter implementation for Mono (Nigeria).
    Handles Mono Connect widget URLs, account linking, historical statement retrieval,
    and webhook parsing.
    """

    BASE_URL = "https://api.withmono.com/v2"

    def __init__(self, secret_key: str | None = None) -> None:
        self.secret_key = secret_key or getattr(settings, "MONO_SECRET_KEY", "")

    def _headers(self) -> dict[str, str]:
        return {
            "mono-sec-key": self.secret_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def create_auth_link(
        self,
        business_id: UUID,
        phone_number: str,
        redirect_url: str | None = None,
    ) -> str:
        """
        Generates a Mono Connect widget authorization URL or link.
        Merchants can authenticate securely via their bank app or USSD.
        """
        app_id = getattr(settings, "MONO_PUBLIC_KEY", "test_pk_waasz")
        redir = redirect_url or f"{getattr(settings, 'APP_URL', 'https://waasz-sme-api.duckdns.org')}/api/v1/openbanking/callback"
        return f"https://connect.withmono.com/?key={app_id}&reference={business_id}&redirect_url={redir}"

    async def exchange_token(self, auth_code: str) -> str:
        """Exchanges temporary authorization code from Mono Connect for a permanent account ID."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self.BASE_URL}/accounts/auth",
                headers=self._headers(),
                json={"code": auth_code},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["id"]

    async def get_account_details(self, account_id: str) -> BankAccountInfo:
        """Fetches bank account information and verified current balance."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/accounts/{account_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            acc = resp.json().get("account", {})
            institution = acc.get("institution", {})
            bvn = acc.get("bvn")
            bvn_last4 = str(bvn)[-4:] if bvn else None

            # Balance in Mono is in kobo (NGN * 100)
            raw_balance = acc.get("balance", 0)
            balance_ngn = Decimal(str(raw_balance)) / Decimal("100")

            return BankAccountInfo(
                account_id=account_id,
                account_number=acc.get("accountNumber", ""),
                account_name=acc.get("name", ""),
                bank_name=institution.get("name", "Unknown Bank"),
                bank_code=institution.get("bankCode", ""),
                currency=acc.get("currency", "NGN"),
                balance=balance_ngn,
                bvn_last4=bvn_last4,
            )

    async def get_transactions(
        self,
        account_id: str,
        start_date: datetime,
        end_date: datetime,
    ) -> list[BankTransaction]:
        """Fetches bank transactions within a date range for cash flow verification."""
        params = {
            "start": start_date.strftime("%d-%m-%Y"),
            "end": end_date.strftime("%d-%m-%Y"),
            "paginate": "false",
        }
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/accounts/{account_id}/transactions",
                headers=self._headers(),
                params=params,
            )
            resp.raise_for_status()
            raw_data = resp.json().get("data", [])

            results: list[BankTransaction] = []
            for item in raw_data:
                # Mono amounts are in kobo
                amount_ngn = Decimal(str(item.get("amount", 0))) / Decimal("100")
                tx_date_raw = item.get("date")
                tx_dt = datetime.fromisoformat(tx_date_raw.replace("Z", "+00:00")) if tx_date_raw else datetime.now()

                results.append(
                    BankTransaction(
                        provider_tx_id=str(item.get("_id") or item.get("id")),
                        account_id=account_id,
                        amount=amount_ngn,
                        transaction_type=item.get("type", "credit").lower(),
                        narration=item.get("narration", ""),
                        occurred_at=tx_dt,
                        balance_after=Decimal(str(item.get("balance", 0))) / Decimal("100") if "balance" in item else None,
                        raw_payload=item,
                    )
                )
            return results

    async def parse_webhook(self, payload: dict[str, Any]) -> list[BankTransaction]:
        """Parses Mono real-time webhook payloads."""
        event_type = payload.get("event")
        if event_type != "mono.events.account_updated":
            return []

        data = payload.get("data", {})
        tx_data = data.get("transaction", {})
        if not tx_data:
            return []

        account_id = data.get("account", {}).get("_id", "")
        amount_ngn = Decimal(str(tx_data.get("amount", 0))) / Decimal("100")
        tx_date_raw = tx_data.get("date")
        tx_dt = datetime.fromisoformat(tx_date_raw.replace("Z", "+00:00")) if tx_date_raw else datetime.now()

        return [
            BankTransaction(
                provider_tx_id=str(tx_data.get("_id") or tx_data.get("id")),
                account_id=account_id,
                amount=amount_ngn,
                transaction_type=tx_data.get("type", "credit").lower(),
                narration=tx_data.get("narration", ""),
                occurred_at=tx_dt,
                balance_after=Decimal(str(tx_data.get("balance", 0))) / Decimal("100") if "balance" in tx_data else None,
                raw_payload=payload,
            )
        ]
