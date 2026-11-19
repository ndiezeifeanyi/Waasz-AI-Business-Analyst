import re
from datetime import UTC, datetime
from decimal import Decimal
import logging
from app.services.open_banking.base import BankTransaction, POSSettlementRecord

logger = logging.getLogger(__name__)


class POSSettlementParser:
    """
    Parses and categorizes Nigerian POS settlement batches from Moniepoint,
    OPay, PalmPay, and commercial bank acquiring switches.
    """

    PATTERNS = {
        "MONIEPOINT": [
            re.compile(r"MONIEPOINT.*?(?:POS|SETTLEMENT|CASHOUT)", re.IGNORECASE),
            re.compile(r"TEAMAPT.*?(?:SETTLE|MERCHANT)", re.IGNORECASE),
            re.compile(r"MP[A-Z0-9]{6,16}", re.IGNORECASE),
        ],
        "OPAY": [
            re.compile(r"OPAY.*?(?:POS|SETTLEMENT|CASHOUT|MERCHANT)", re.IGNORECASE),
            re.compile(r"PAYCOM.*?(?:SETTLE|POS)", re.IGNORECASE),
        ],
        "PALMPAY": [
            re.compile(r"PALMPAY.*?(?:POS|SETTLEMENT|AGENT|MERCHANT)", re.IGNORECASE),
            re.compile(r"TRANSSNET.*?(?:SETTLE)", re.IGNORECASE),
        ],
        "COMMERCIAL_BANK": [
            re.compile(r"(?:FIRSTMONIE|ZENITH\s*POS|GTB\s*HABARI|UBA\s*POS|STANBIC\s*POS).*?(?:SETTLE|TRF)", re.IGNORECASE),
            re.compile(r"POS\s*SETTLEMENT", re.IGNORECASE),
        ],
    }

    TERMINAL_REGEX = re.compile(r"(?:TERM|TID|POSID)[:\s-]*([A-Z0-9]{6,12})", re.IGNORECASE)

    @classmethod
    def identify_pos_provider(cls, narration: str) -> str | None:
        """Determines if narration represents a recognized Nigerian POS merchant payout."""
        if not narration:
            return None
        text = narration.strip()
        for provider, regexes in cls.PATTERNS.items():
            for r in regexes:
                if r.search(text):
                    return provider
        return None

    @classmethod
    def parse_transaction(cls, tx: BankTransaction) -> POSSettlementRecord | None:
        """
        Extracts POS settlement parameters from bank transaction if narration indicates
        a POS settlement batch credit.
        """
        if tx.transaction_type != "credit":
            return None

        provider = cls.identify_pos_provider(tx.narration)
        if not provider:
            return None

        terminal_id = None
        term_match = cls.TERMINAL_REGEX.search(tx.narration)
        if term_match:
            terminal_id = term_match.group(1).upper()

        # In Nigerian POS acquiring, typical transaction fees are 0.5% (capped at ₦100) or 0.75%
        # Net payout is the settled amount; we estimate gross volume
        net_amount = tx.amount
        # Reconstruct standard fee cap (~ 0.5% max NGN 100-200)
        fee_est = min(net_amount * Decimal("0.0075"), Decimal("200.00"))
        gross_est = net_amount + fee_est

        return POSSettlementRecord(
            pos_provider=provider,
            gross_volume=gross_est,
            net_payout=net_amount,
            service_charge=fee_est,
            settlement_reference=tx.provider_tx_id,
            settled_at=tx.occurred_at or datetime.now(UTC),
            terminal_id=terminal_id,
        )
