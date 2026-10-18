import logging
import re
from decimal import Decimal

logger = logging.getLogger(__name__)

# Matches individual numbered or bulleted line items ending with or containing a Naira amount
# Handles both plain and bold markdown, e.g.:
# 1. Oil & gas filter (2 pcs) - ₦8,000
# 2) Clutch plate (1 pc) - ₦25,000
# 4. **Oil pump packing** (1 pc) - **₦3,000**
# - Front shock (1 set): ₦65,000
LINE_ITEM_REGEX = re.compile(
    r"^\s*(?:\d+[\.\)]|[-•*])\s*(.+?)\s*[-:]\s*(?:\*{0,2})₦?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|\d+(?:\.[0-9]{1,2})?)(?:\*{0,2})\s*$",
    re.MULTILINE,
)

# Matches stated Grand Total or Total expressions
# Handles plain and bold markdown:
# 💰 Grand Total: ₦1,000,000
# 💰 **Grand Total:** ₦652,000
# 💰 **Grand Total:** **₦652,000**
# Total: ₦500,000
GRAND_TOTAL_REGEX = re.compile(
    r"((?:💰\s*)?(?:\*{0,2})(?:Grand\s+Total|Total)(?::)?(?:\*{0,2})[:\-]?\s*(?:\*{0,2})₦?\s*)([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|\d+(?:\.[0-9]{1,2})?)(?:\*{0,2})",
    re.IGNORECASE,
)



def verify_and_reconcile_financial_totals(text: str) -> str:
    """
    Deterministic Arithmetic Validator:
    Scans conversational or extraction text for itemized lists of goods/services with prices.
    Computes the exact mathematical sum using Python Decimal arithmetic.
    If the LLM hallucinated a Grand Total that does not match the sum of its items,
    this function intercepts and replaces the total with the exact verified figure.
    """
    if not text or not str(text).strip():
        return text

    # Standardize newlines
    normalized = text.replace("\r\n", "\n")

    # Extract all line item amounts
    item_matches = LINE_ITEM_REGEX.findall(normalized)
    if len(item_matches) < 2:
        return text

    item_amounts: list[Decimal] = []
    for _desc, amt_str in item_matches:
        try:
            cleaned = amt_str.replace(",", "").strip()
            amt = Decimal(cleaned)
            if amt > 0:
                item_amounts.append(amt)
        except Exception:
            continue

    if len(item_amounts) < 2:
        return text

    exact_sum = sum(item_amounts, Decimal("0"))
    formatted_exact = f"{exact_sum:,.0f}" if exact_sum % 1 == 0 else f"{exact_sum:,.2f}"

    # Search for an existing stated total
    m_tot = GRAND_TOTAL_REGEX.search(normalized)
    if m_tot:
        prefix = m_tot.group(1)
        stated_str = m_tot.group(2).replace(",", "").strip()
        try:
            stated_amt = Decimal(stated_str)
            if stated_amt != exact_sum:
                logger.warning(
                    "Deterministic Reconciler: Corrected LLM hallucinated total ₦%s to exact sum ₦%s across %d items",
                    stated_str,
                    formatted_exact,
                    len(item_amounts),
                )
                start_idx = m_tot.start()
                end_idx = m_tot.end()
                # Reconstruct with exact verified sum
                text = normalized[:start_idx] + f"{prefix}{formatted_exact}" + normalized[end_idx:]
        except Exception as exc:
            logger.debug("Could not parse stated total amount '%s': %s", stated_str, exc)
    else:
        # If no grand total was stated on an itemized breakdown of 3+ items, append exact sum
        if len(item_amounts) >= 3 and any(k in normalized.lower() for k in ("breakdown", "extracted", "items", "sales", "expenses")):
            text = f"{normalized.rstrip()}\n\n💰 *Grand Total:* ₦{formatted_exact}"

    return text
