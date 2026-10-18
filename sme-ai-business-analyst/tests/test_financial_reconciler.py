import pytest
from app.utils.financial_reconciler import verify_and_reconcile_financial_totals


def test_reconcile_hallucinated_one_million_to_exact_sum():
    llm_output = (
        "Let's extract the sales from the receipt you provided. Here’s the breakdown:\n\n"
        "📅 Date: 2026-10-06\n\n"
        "1. Oil & gas filter (2 pcs) - ₦8,000\n"
        "2. Clutch plate (1 pc) - ₦25,000\n"
        "3. Wheel rim (1 set) - ₦90,000\n"
        "4. Oil pump parting (1 pc) - ₦30,000\n"
        "5. Front shock (1 set) - ₦65,000\n"
        "6. Back linkage (1 pc) - ₦10,000\n"
        "7. Tie rod end (1 pc) - ₦10,000\n"
        "8. Set up & down clutch (1 set) - ₦95,000\n"
        "9. Front brake disc (1 set) - ₦30,000\n"
        "10. STD Con rod (1 set) - ₦40,000\n"
        "11. V-Cover plugs (1 set) - ₦25,000\n"
        "12. Back ABS wire (1 set) - ₦30,000\n"
        "13. Front ABS wire (2 pcs) - ₦25,000\n"
        "14. Front linkage (1 set) - ₦30,000\n"
        "15. Back shock (1 pc) - ₦43,000\n"
        "16. Clutch plate (1 pc) - ₦30,000\n"
        "17. Down clutch (1 set) - ₦20,000\n"
        "18. Complete gasket (1 set) - ₦40,000\n"
        "19. Fuel filter (1 set) - ₦3,000\n"
        "20. Oil filter (1 set) - ₦3,000\n"
        "21. Starter pump (1 pc) - ₦15,000\n"
        "22. Valve seal (1 set) - ₦12,000\n\n"
        "💰 Grand Total: ₦1,000,000\n\n"
        "Please confirm if this breakdown is correct or if you need any edits."
    )

    reconciled = verify_and_reconcile_financial_totals(llm_output)

    assert "679,000" in reconciled
    assert "1,000,000" not in reconciled
    assert "💰 Grand Total: ₦679,000" in reconciled


def test_reconcile_bold_grand_total():
    llm_output = (
        "Here is your updated breakdown:\n\n"
        "1. Item A - ₦50,000\n"
        "2. Item B - ₦100,000\n"
        "3. Item C - ₦20,000\n\n"
        "💰 **Grand Total:** ₦300,000"
    )

    reconciled = verify_and_reconcile_financial_totals(llm_output)

    assert "170,000" in reconciled
    assert "300,000" not in reconciled
    assert "💰 **Grand Total:** ₦170,000" in reconciled


def test_turn_two_bold_item_name_and_amount():
    llm_output = (
        "Got it, updated **Oil pump packing** to **₦3,000**.\n\n"
        "Here is your updated breakdown:\n\n"
        "📅 **Date:** 2026-10-06\n\n"
        "1. Oil & gas filter (2 pcs) - ₦8,000\n"
        "2. Clutch plate (1 pc) - ₦25,000\n"
        "3. Wheel rim (1 set) - ₦90,000\n"
        "4. **Oil pump packing** (1 pc) - **₦3,000**\n"
        "5. Front shock (1 set) - ₦65,000\n"
        "6. Back linkage (1 pc) - ₦10,000\n"
        "7. Tie rod end (1 pc) - ₦10,000\n"
        "8. Set up & down clutch (1 set) - ₦95,000\n"
        "9. Front brake disc (1 set) - ₦30,000\n"
        "10. STD Con rod (1 set) - ₦40,000\n"
        "11. V-Cover plugs (1 set) - ₦25,000\n"
        "12. Back ABS wire (1 set) - ₦30,000\n"
        "13. Front ABS wire (2 pcs) - ₦25,000\n"
        "14. Front linkage (1 set) - ₦30,000\n"
        "15. Back shock (1 pc) - ₦43,000\n"
        "16. Clutch plate (1 pc) - ₦30,000\n"
        "17. Down clutch (1 set) - ₦20,000\n"
        "18. Complete gasket (1 set) - ₦40,000\n"
        "19. Fuel filter (1 set) - ₦3,000\n"
        "20. Oil filter (1 set) - ₦3,000\n"
        "21. Starter pump (1 pc) - ₦15,000\n"
        "22. Valve seal (1 set) - ₦12,000\n\n"
        "💰 **Grand Total:** ₦652,000\n\n"
        "Should I go ahead and record these 22 sales now?"
    )

    reconciled = verify_and_reconcile_financial_totals(llm_output)
    # Sum is 652,000, so it remains 652,000
    assert "652,000" in reconciled


def test_already_accurate_total_is_preserved():
    llm_output = (
        "Breakdown:\n"
        "1. Item A - ₦10,000\n"
        "2. Item B - ₦20,000\n\n"
        "Grand Total: ₦30,000"
    )

    reconciled = verify_and_reconcile_financial_totals(llm_output)
    assert "Grand Total: ₦30,000" in reconciled
