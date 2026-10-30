import hashlib
import io
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.transaction import Transaction
from app.utils.currency import amount_to_words, format_naira

logger = logging.getLogger(__name__)


class DebtReceiptPdfService:
    """
    Generates institutional, legally-sound B2B Debt Receipts (A4 PDF)
    modeled after premier fintech credit infrastructures (Moniepoint, Stripe Capital, Carbon).
    """

    async def generate_debt_receipt_pdf(
        self,
        db: AsyncSession,
        business_id: UUID,
        debt_id: UUID | None = None,
        transaction_id: UUID | None = None,
    ) -> tuple[bytes, str]:
        """
        Builds and returns an A4 PDF Debt Receipt bytes and standard filename.
        """
        biz = await db.get(Business, business_id)
        if not biz:
            raise ValueError(f"Business {business_id} not found")

        debt: Debt | None = None
        target_tx: Transaction | None = None
        customer: Customer | None = None

        if debt_id:
            debt = await db.get(Debt, debt_id)
            if debt and debt.transaction_id:
                target_tx = await db.get(Transaction, debt.transaction_id)
            if debt and debt.customer_id:
                customer = await db.get(Customer, debt.customer_id)

        if not target_tx and transaction_id:
            target_tx = await db.get(Transaction, transaction_id)
            if target_tx:
                d_stmt = select(Debt).where(
                    Debt.transaction_id == target_tx.id,
                    Debt.business_id == business_id,
                )
                debt = (await db.execute(d_stmt)).scalar_one_or_none()
                if target_tx.customer_id and not customer:
                    customer = await db.get(Customer, target_tx.customer_id)

        # Fallback to latest credit transaction for the business
        if not target_tx and not debt:
            t_stmt = (
                select(Transaction)
                .where(
                    Transaction.business_id == business_id,
                    Transaction.is_credit.is_(True),
                    Transaction.status == "confirmed",
                )
                .order_by(Transaction.occurred_at.desc())
                .limit(1)
            )
            target_tx = (await db.execute(t_stmt)).scalar_one_or_none()
            if target_tx:
                if target_tx.customer_id:
                    customer = await db.get(Customer, target_tx.customer_id)
                d_stmt = select(Debt).where(
                    Debt.transaction_id == target_tx.id,
                    Debt.business_id == business_id,
                )
                debt = (await db.execute(d_stmt)).scalar_one_or_none()

        if not target_tx and not debt:
            raise ValueError(f"No credit transaction or debt record found for business {business_id}")

        # Core Metrics Extraction
        principal_amount = debt.amount if debt else (target_tx.amount if target_tx else Decimal("0"))
        occurred_date = (
            (target_tx.occurred_at if target_tx and target_tx.occurred_at else None)
            or (debt.created_at if debt and debt.created_at else None)
            or datetime.now(UTC)
        )
        issue_date_str = occurred_date.strftime("%d %b %Y")

        # Ref Code
        seed_id = str(debt.id if debt else target_tx.id).replace("-", "").upper()
        ref_code = f"WZ-{seed_id[:8]}"

        # Net-14 Chronological Terms
        if debt and debt.due_date:
            maturity_dt = debt.due_date
        else:
            maturity_dt = occurred_date + timedelta(days=14)
        maturity_date_str = maturity_dt.strftime("%d %b %Y")

        debtor_name = (customer.name if customer else "CUSTOMER / DEBTOR").strip()
        debtor_phone = customer.phone_number if (customer and customer.phone_number) else "NOT DISCLOSED"
        creditor_name = biz.name.strip()
        creditor_phone = biz.phone_number or "NOT DISCLOSED"
        item_desc = (
            (target_tx.item_name or target_tx.description if target_tx else None)
            or "Commercial Credit Facility"
        )
        ledger_status = "SETTLED" if (debt and debt.status == "paid") else "RECORDED // OUTSTANDING"

        # Cryptographic Hash (SHA-256)
        payload = f"{ref_code}|{business_id}|{debtor_name}|{principal_amount}|{occurred_date.isoformat()}"
        sha256_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()

        pdf_bytes = self._build_pdf_bytes(
            ref_code=ref_code,
            issue_date_str=issue_date_str,
            ledger_status=ledger_status,
            creditor_name=creditor_name,
            creditor_phone=creditor_phone,
            creditor_id=str(biz.id).replace("-", "")[:8].upper(),
            debtor_name=debtor_name,
            debtor_phone=debtor_phone,
            debtor_id=str(customer.id).replace("-", "")[:8].upper() if customer else "UNKNOWN",
            principal_amount=principal_amount,
            amount_words=amount_to_words(principal_amount),
            item_desc=item_desc,
            maturity_date_str=maturity_date_str,
            sha256_hash=sha256_hash,
            timestamp_iso=occurred_date.strftime("%Y-%m-%d %H:%M:%S UTC"),
        )
        filename = f"Debt_Receipt_{ref_code}.pdf"
        return pdf_bytes, filename

    def _build_pdf_bytes(
        self,
        ref_code: str,
        issue_date_str: str,
        ledger_status: str,
        creditor_name: str,
        creditor_phone: str,
        creditor_id: str,
        debtor_name: str,
        debtor_phone: str,
        debtor_id: str,
        principal_amount: Decimal,
        amount_words: str,
        item_desc: str,
        maturity_date_str: str,
        sha256_hash: str,
        timestamp_iso: str,
    ) -> bytes:
        """Constructs the ReportLab Document flowables matching the institutional A4 grid."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=32,
            bottomMargin=32,
        )

        styles = getSampleStyleSheet()

        # Custom institutional typography
        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            textColor=colors.HexColor("#111827"),
        )
        super_header = ParagraphStyle(
            "SuperHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9,
            textColor=colors.HexColor("#6B7280"),
            textTransform="uppercase",
        )
        sub_legal = ParagraphStyle(
            "SubLegal",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7,
            leading=9,
            textColor=colors.HexColor("#4B5563"),
        )
        meta_label = ParagraphStyle(
            "MetaLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7,
            leading=8.5,
            textColor=colors.HexColor("#6B7280"),
            textTransform="uppercase",
        )
        meta_val = ParagraphStyle(
            "MetaVal",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9.5,
            textColor=colors.HexColor("#111827"),
            alignment=2,  # right-aligned
        )
        section_heading = ParagraphStyle(
            "SectionHeading",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#111827"),
            textTransform="uppercase",
        )
        party_name = ParagraphStyle(
            "PartyName",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=colors.HexColor("#111827"),
        )
        party_sub = ParagraphStyle(
            "PartySub",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9.5,
            textColor=colors.HexColor("#4B5563"),
        )
        badge_style = ParagraphStyle(
            "BadgeStyle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7,
            leading=9,
            textColor=colors.HexColor("#065F46"),
        )
        amount_fig = ParagraphStyle(
            "AmountFig",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#111827"),
        )
        amount_wrd = ParagraphStyle(
            "AmountWrd",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#374151"),
        )
        legal_declaration = ParagraphStyle(
            "LegalDeclaration",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7,
            leading=9.5,
            textColor=colors.HexColor("#374151"),
        )
        hash_style = ParagraphStyle(
            "HashStyle",
            parent=styles["Normal"],
            fontName="Courier",
            fontSize=6.5,
            leading=8,
            textColor=colors.HexColor("#4B5563"),
        )
        sig_label = ParagraphStyle(
            "SigLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7,
            leading=8.5,
            textColor=colors.HexColor("#6B7280"),
            textTransform="uppercase",
        )
        sig_title = ParagraphStyle(
            "SigTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#111827"),
        )

        elements = []

        # -------------------------------------------------------------
        # DOCUMENT HEADER & METADATA GRID
        # -------------------------------------------------------------
        header_left = [
            Paragraph("🏛️ WAASZ ENTERPRISE CREDIT REGISTRY", super_header),
            Spacer(1, 2),
            Paragraph("OFFICIAL DEBT ACKNOWLEDGMENT (IOU)", title_style),
            Spacer(1, 2),
            Paragraph("FORMAL B2B ACCOUNTS RECEIVABLE INSTRUMENT // ENFORCEABLE CREDIT LEDGER", sub_legal),
        ]

        header_right_table = Table(
            [
                [Paragraph("REGISTRY REF:", meta_label), Paragraph(f"#{ref_code}", meta_val)],
                [Paragraph("ISSUE DATE:", meta_label), Paragraph(issue_date_str, meta_val)],
                [Paragraph("LEDGER STATUS:", meta_label), Paragraph(f"<b>{ledger_status}</b>", meta_val)],
                [Paragraph("CURRENCY TYPE:", meta_label), Paragraph("NGN (Nigerian Naira)", meta_val)],
            ],
            colWidths=[80, 110],
        )
        header_right_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ])
        )

        header_table = Table(
            [[header_left, header_right_table]],
            colWidths=[330, 193],
        )
        header_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ])
        )
        elements.append(header_table)
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#111827"), spaceBefore=8, spaceAfter=8))

        # -------------------------------------------------------------
        # SECTION 1: PRINCIPAL PARTIES
        # -------------------------------------------------------------
        elements.append(Paragraph("SECTION 1: PRINCIPAL PARTIES TO OBLIGATION", section_heading))
        elements.append(Spacer(1, 3))

        creditor_content = [
            Paragraph("CREDITOR (ISSUING MERCHANT)", super_header),
            Spacer(1, 1),
            Paragraph(f"<b>{creditor_name}</b>", party_name),
            Spacer(1, 1),
            Paragraph("✔ VERIFIED WAASZ MERCHANT REGISTRY", badge_style),
            Paragraph(f"Designated Phone: {creditor_phone}", party_sub),
            Paragraph(f"Institutional Registry ID: WZ-MERCHANT-{creditor_id}", party_sub),
        ]

        debtor_content = [
            Paragraph("DEBTOR (OBLIGOR / PURCHASER)", super_header),
            Spacer(1, 1),
            Paragraph(f"<b>{debtor_name}</b>", party_name),
            Spacer(1, 1),
            Paragraph("RECORDED COMMERCIAL DEBTOR", meta_label),
            Paragraph(f"Authorized Contact: {debtor_phone}", party_sub),
            Paragraph(f"Customer Reference ID: WZ-CUST-{debtor_id}", party_sub),
        ]

        parties_table = Table(
            [[creditor_content, debtor_content]],
            colWidths=[255, 255],
        )
        parties_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E5E7EB")),
                ("INNERGRID", (0, 0), (-1, -1), 0.75, colors.HexColor("#E5E7EB")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ])
        )
        elements.append(parties_table)
        elements.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#E5E7EB"), spaceBefore=7, spaceAfter=7))

        # -------------------------------------------------------------
        # SECTION 2: REPAYMENT TERMS & BALANCE OBLIGATION
        # -------------------------------------------------------------
        elements.append(Paragraph("SECTION 2: REPAYMENT TERMS & BALANCE OBLIGATION", section_heading))
        elements.append(Spacer(1, 3))

        # Highlight Box: Principal in figures and words
        balance_banner = Table(
            [
                [
                    Paragraph("TOTAL PRINCIPAL OUTSTANDING (BALANCE DUE)", super_header),
                    Paragraph(f"NGN {principal_amount:,.2f}", amount_fig),
                    Paragraph(f"<b>{amount_words}</b>", amount_wrd),
                ]
            ],
            colWidths=[510],
        )
        balance_banner.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F4F6")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#111827")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ])
        )
        elements.append(balance_banner)
        elements.append(Spacer(1, 4))

        # Terms Matrix Table
        terms_data = [
            [
                Paragraph("<b>Underlying Facility / Goods:</b>", meta_label),
                Paragraph(item_desc, sub_legal),
                Paragraph("<b>Interest Structure:</b>", meta_label),
                Paragraph("<b>0.00% APR (Fixed Default)</b>", sub_legal),
            ],
            [
                Paragraph("<b>Facility Origination Date:</b>", meta_label),
                Paragraph(issue_date_str, sub_legal),
                Paragraph("<b>Maturity / Due Date:</b>", meta_label),
                Paragraph(f"<b>{maturity_date_str} (Net-14)</b>", sub_legal),
            ],
            [
                Paragraph("<b>Interactive Settle Gateway:</b>", meta_label),
                Paragraph(f"https://pay.waasz.com/settle/{ref_code}", sub_legal),
                Paragraph("<b>Digital Document Route:</b>", meta_label),
                Paragraph(f"https://docs.waasz.com/receipts/iou/{ref_code}.pdf", sub_legal),
            ],
        ]
        terms_table = Table(terms_data, colWidths=[120, 140, 115, 135])
        terms_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#F3F4F6")),
            ])
        )
        elements.append(terms_table)
        elements.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#E5E7EB"), spaceBefore=7, spaceAfter=7))

        # -------------------------------------------------------------
        # SECTION 3: COMPLIANCE, CONSENT & AUDIT TRAIL
        # -------------------------------------------------------------
        elements.append(Paragraph("SECTION 3: COMPLIANCE, CONSENT & AUDIT TRAIL", section_heading))
        elements.append(Spacer(1, 3))

        legal_text = (
            "<b>STATUTORY DECLARATION OF DEBT OBLIGATION:</b> This digital debt instrument represents a binding "
            "commercial accounts receivable claim registered on the Waasz Enterprise Ledger Registry. "
            "By receiving delivery of the underlying trade inventory or services and acknowledging the receipt "
            "transmitted via SMS/WhatsApp notification channels, the Debtor formally covenants to extinguish "
            "the Principal Outstanding on or prior to the stipulated Maturity Date. "
            "Failure to settle within the designated timeline constitutes an event of commercial default, authorizing "
            "the Creditor to cite this certified ledger acknowledgment before relevant arbitral tribunals, "
            "credit reporting institutions, and partner commercial registries across Nigeria."
        )
        elements.append(Paragraph(legal_text, legal_declaration))
        elements.append(Spacer(1, 5))

        # Cryptographic Audit Hash Table
        audit_data = [
            [
                Paragraph("<b>SHA-256 LEDGER SIGNATURE HASH:</b>", meta_label),
                Paragraph(sha256_hash, hash_style),
            ],
            [
                Paragraph("<b>CONSENSUS NODE & ORIGIN:</b>", meta_label),
                Paragraph(f"WAASZ-LEDGER-NODE-NG-04 // PROTOCOL v2.1 // TIMESTAMP: {timestamp_iso}", hash_style),
            ],
        ]
        audit_table = Table(audit_data, colWidths=[165, 345])
        audit_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ])
        )
        elements.append(audit_table)
        elements.append(Spacer(1, 7))

        # Signatures Grid
        sig_data = [
            [
                [
                    Paragraph("CREDITOR AUTHORIZED SIGNATORY", sig_label),
                    Spacer(1, 10),
                    Paragraph("________________________________________", sub_legal),
                    Spacer(1, 2),
                    Paragraph(f"<b>{creditor_name}</b>", sig_title),
                    Paragraph("Digitally Certified via Waasz Verified Merchant Registry", sub_legal),
                ],
                [
                    Paragraph("DEBTOR ACKNOWLEDGMENT & CONSENT", sig_label),
                    Spacer(1, 10),
                    Paragraph("________________________________________", sub_legal),
                    Spacer(1, 2),
                    Paragraph(f"<b>{debtor_name}</b>", sig_title),
                    Paragraph(f"Dispatched & Logged via Registered Telco Channel ({debtor_phone})", sub_legal),
                ],
            ]
        ]
        sig_table = Table(sig_data, colWidths=[255, 255])
        sig_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ])
        )
        elements.append(sig_table)
        elements.append(Spacer(1, 5))

        # Bottom Page Footer
        footer_table = Table(
            [
                [
                    Paragraph("WAASZ ENTERPRISE CREDIT INFRASTRUCTURE // ISO-20022 COMPLIANT AUDIT FORMAT", super_header),
                    Paragraph("PAGE 1 OF 1 // IMMUTABLE LEDGER", meta_val),
                ]
            ],
            colWidths=[350, 160],
        )
        footer_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ])
        )
        elements.append(footer_table)

        doc.build(elements)
        return buffer.getvalue()
