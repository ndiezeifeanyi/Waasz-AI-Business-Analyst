from datetime import UTC, datetime
from decimal import Decimal
import pytest
from app.services.open_banking.base import BankTransaction
from app.services.open_banking.pos_settlement_parser import POSSettlementParser


def test_pos_settlement_parser_moniepoint():
    tx = BankTransaction(
        provider_tx_id="tx_mp_1001",
        account_id="acc_123",
        amount=Decimal("450000.00"),
        transaction_type="credit",
        narration="NIP/MONIEPOINT POS SETTLEMENT TID:MP99281A/TEAMAPT",
        occurred_at=datetime.now(UTC),
    )

    record = POSSettlementParser.parse_transaction(tx)
    assert record is not None
    assert record.pos_provider == "MONIEPOINT"
    assert record.net_payout == Decimal("450000.00")
    assert record.gross_volume > Decimal("450000.00")
    assert record.terminal_id == "MP99281A"


def test_pos_settlement_parser_opay():
    tx = BankTransaction(
        provider_tx_id="tx_op_2002",
        account_id="acc_123",
        amount=Decimal("185000.00"),
        transaction_type="credit",
        narration="OPAY POS SETTLEMENT CASHOUT BATCH REF 898129",
        occurred_at=datetime.now(UTC),
    )

    record = POSSettlementParser.parse_transaction(tx)
    assert record is not None
    assert record.pos_provider == "OPAY"
    assert record.net_payout == Decimal("185000.00")


def test_pos_settlement_parser_palmpay():
    tx = BankTransaction(
        provider_tx_id="tx_pp_3003",
        account_id="acc_123",
        amount=Decimal("320000.00"),
        transaction_type="credit",
        narration="TRF FRM TRANSSNET PALMPAY MERCH SETTLEMENT AGENT 55",
        occurred_at=datetime.now(UTC),
    )

    record = POSSettlementParser.parse_transaction(tx)
    assert record is not None
    assert record.pos_provider == "PALMPAY"
    assert record.net_payout == Decimal("320000.00")


def test_pos_settlement_parser_ignores_debits():
    tx = BankTransaction(
        provider_tx_id="tx_db_4004",
        account_id="acc_123",
        amount=Decimal("50000.00"),
        transaction_type="debit",
        narration="MONIEPOINT POS CHARGE",
        occurred_at=datetime.now(UTC),
    )

    record = POSSettlementParser.parse_transaction(tx)
    assert record is None
