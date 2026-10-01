from datetime import UTC, datetime
from decimal import Decimal
import io
from uuid import UUID

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
import reportlab.rl_config as rl_config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

rl_config.pageCompression = 0

from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.transaction import Transaction


class ReceiptService:
    """
    Generates clean, legible, and professional PDF receipts and invoices using ReportLab.
    Produces:
    - Standard Receipt for paid sales (cash/transfer).
    - Invoice variant for credit sales (unpaid / receivables) with amount due and due date.
    """

    async def generate_receipt_pdf(
        self,
        db: AsyncSession,
        business_id: UUID,
        transaction_id: UUID | None = None,
    ) -> tuple[bytes, str, Transaction]:
        """
        Builds the PDF document for a confirmed sale transaction.
        Returns:
            (pdf_bytes, filename, transaction)
        """
        # 1. Fetch transaction strictly scoped to business_id
        if transaction_id:
            tx_stmt = select(Transaction).where(
                Transaction.id == transaction_id,
                Transaction.business_id == business_id,
            )
        else:
            # Pick latest confirmed sale
            tx_stmt = (
                select(Transaction)
                .where(
                    Transaction.business_id == business_id,
                    Transaction.transaction_type == "sale",
                    Transaction.status == "confirmed",
                )
                .order_by(Transaction.occurred_at.desc())
                .limit(1)
            )

        tx_res = await db.execute(tx_stmt)
        transaction = tx_res.scalar_one_or_none()
        if not transaction:
            raise ValueError(f"No confirmed sale found for business {business_id}")

        # 2. Fetch business details and persistent receipt template (if configured)
        from app.models.receipt_template import ReceiptTemplate
        tmpl_stmt = select(ReceiptTemplate).where(ReceiptTemplate.business_id == business_id)
        tmpl_res = await db.execute(tmpl_stmt)
        template = tmpl_res.scalar_one_or_none()

        biz = await db.get(Business, business_id)
        biz_name = (template.business_display_name if template and template.business_display_name else None) or (biz.name if biz else "Business")
        biz_phone = (template.contact_phone if template and template.contact_phone else None) or (biz.phone_number if biz else "")
        biz_address = template.address if template else None
        biz_email = template.contact_email if template else None
        payment_terms = template.payment_terms_note if template else None
        footer_note = template.footer_note if template else None

        # 3. Fetch customer details if associated
        customer = None
        if transaction.customer_id:
            cust_stmt = select(Customer).where(
                Customer.id == transaction.customer_id,
                Customer.business_id == business_id,
            )
            cust_res = await db.execute(cust_stmt)
            customer = cust_res.scalar_one_or_none()

        # 4. Fetch debt details if this is a credit sale
        debt = None
        if transaction.is_credit:
            debt_stmt = select(Debt).where(
                Debt.transaction_id == transaction.id,
                Debt.business_id == business_id,
            )
            debt_res = await db.execute(debt_stmt)
            debt = debt_res.scalar_one_or_none()

        # 5. Build PDF in memory
        is_credit = bool(transaction.is_credit)
        doc_type = "INVOICE" if is_credit else "RECEIPT"
        short_id = str(transaction.id).replace("-", "")[:8].upper()
        doc_number = f"INV-{short_id}" if is_credit else f"REC-{short_id}"
        filename = f"{doc_type.lower()}_{short_id}.pdf"

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )

        styles = getSampleStyleSheet()

        # Custom typography styles
        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=colors.HexColor("#1e3a8a") if not is_credit else colors.HexColor("#b45309"),
            alignment=2,  # Right aligned
        )
        biz_name_style = ParagraphStyle(
            "BizName",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            textColor=colors.HexColor("#111827"),
        )
        biz_sub_style = ParagraphStyle(
            "BizSub",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#4b5563"),
        )
        meta_label_style = ParagraphStyle(
            "MetaLabel",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#374151"),
            alignment=2,
        )
        meta_val_style = ParagraphStyle(
            "MetaVal",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#1f2937"),
            alignment=2,
        )
        section_heading = ParagraphStyle(
            "SectionHead",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=15,
            textColor=colors.HexColor("#1e3a8a") if not is_credit else colors.HexColor("#92400e"),
        )
        body_text = ParagraphStyle(
            "Body",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#1f2937"),
        )
        table_head_style = ParagraphStyle(
            "TableHead",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#1f2937"),
        )
        table_cell_style = ParagraphStyle(
            "TableCell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#111827"),
        )
        table_num_cell = ParagraphStyle(
            "TableNumCell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#111827"),
            alignment=2,
        )
        footer_style = ParagraphStyle(
            "Footer",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#6b7280"),
            alignment=1,  # Center aligned
        )

        elements = []

        # --- Header Section (Two Column: Business Info vs Document Info) ---
        date_str = transaction.occurred_at.strftime("%d %b %Y, %H:%M UTC")

        due_date_str = None
        if is_credit:
            if debt and debt.due_date:
                due_date_str = debt.due_date.strftime("%d %b %Y")
            else:
                due_date_str = "Due upon receipt"

        biz_info_col = [Paragraph(biz_name, biz_name_style)]
        if biz_address:
            biz_info_col.append(Paragraph(biz_address, biz_sub_style))
        contact_line = []
        if biz_phone:
            contact_line.append(f"Phone: {biz_phone}")
        if biz_email:
            contact_line.append(f"Email: {biz_email}")
        if contact_line:
            biz_info_col.append(Paragraph(" | ".join(contact_line), biz_sub_style))

        doc_info_col = [
            Paragraph(doc_type, title_style),
            Paragraph(f"<b>{doc_type} #:</b> {doc_number}", meta_val_style),
            Paragraph(f"<b>Date:</b> {date_str}", meta_val_style),
        ]
        if is_credit and due_date_str:
            doc_info_col.append(Paragraph(f"<b>Payment Due:</b> {due_date_str}", meta_val_style))

        max_rows = max(len(biz_info_col), len(doc_info_col))
        header_data = []
        for i in range(max_rows):
            left = biz_info_col[i] if i < len(biz_info_col) else Paragraph("", biz_sub_style)
            right = doc_info_col[i] if i < len(doc_info_col) else Paragraph("", meta_val_style)
            header_data.append([left, right])

        header_table = Table(header_data, colWidths=[270, 270])
        header_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ])
        )
        elements.append(header_table)
        elements.append(Spacer(1, 14))

        # Thin divider
        elements.append(
            HRFlowable(
                width="100%",
                thickness=1,
                color=colors.HexColor("#e5e7eb"),
                spaceAfter=14,
                spaceBefore=0,
            )
        )

        # --- Customer / Bill To Block ---
        cust_name = customer.name if customer else (transaction.description if is_credit else None)
        cust_phone = customer.phone_number if customer else None

        if cust_name:
            bill_to_data = [
                [Paragraph("BILLED TO / CUSTOMER", section_heading)],
                [Paragraph(f"<b>Name:</b> {cust_name}", body_text)],
            ]
            if cust_phone:
                bill_to_data.append([Paragraph(f"<b>Phone:</b> {cust_phone}", body_text)])
            bill_to_table = Table(bill_to_data, colWidths=[540])
            bill_to_table.setStyle(
                TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ])
            )
            elements.append(bill_to_table)
            elements.append(Spacer(1, 14))

        # --- Status Badge Box ---
        if is_credit:
            status_text = f"<b>PAYMENT STATUS:</b> OUTSTANDING / CREDIT SALE (Due: {due_date_str})"
            status_bg = colors.HexColor("#fef3c7")  # Warm amber light
            status_border = colors.HexColor("#f59e0b")
            status_font_color = colors.HexColor("#92400e")
        else:
            status_text = "<b>PAYMENT STATUS:</b> PAID IN FULL"
            status_bg = colors.HexColor("#ecfdf5")  # Emerald light
            status_border = colors.HexColor("#10b981")
            status_font_color = colors.HexColor("#065f46")

        status_style = ParagraphStyle(
            "Status",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=14,
            textColor=status_font_color,
            alignment=1,  # Centered
        )

        status_box = Table([[Paragraph(status_text, status_style)]], colWidths=[540])
        status_box.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), status_bg),
                ("BOX", (0, 0), (-1, -1), 1, status_border),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ])
        )
        elements.append(status_box)
        elements.append(Spacer(1, 16))

        # --- Items & Calculations Table ---
        item_title = transaction.item_name or transaction.description or "General Sale"
        qty_str = (
            f"{transaction.quantity:g} {transaction.unit or ''}".strip()
            if transaction.quantity is not None
            else "1"
        )
        unit_price_val = transaction.unit_price
        if unit_price_val is None and transaction.quantity and transaction.amount:
            try:
                unit_price_val = transaction.amount / transaction.quantity
            except Exception:
                unit_price_val = None

        unit_price_str = f"NGN {unit_price_val:,.2f}" if unit_price_val else "-"
        total_amount = transaction.amount or Decimal("0.00")
        total_str = f"NGN {total_amount:,.2f}"

        items_table_data = [
            [
                Paragraph("<b>Item / Description</b>", table_head_style),
                Paragraph("<b>Qty</b>", table_head_style),
                Paragraph("<b>Unit Price</b>", table_num_cell),
                Paragraph("<b>Total Amount</b>", table_num_cell),
            ],
            [
                Paragraph(item_title, table_cell_style),
                Paragraph(qty_str, table_cell_style),
                Paragraph(unit_price_str, table_num_cell),
                Paragraph(total_str, table_num_cell),
            ],
            # Summary rows
            [
                "",
                "",
                Paragraph("<b>Subtotal:</b>", table_num_cell),
                Paragraph(total_str, table_num_cell),
            ],
            [
                "",
                "",
                Paragraph("<b>Grand Total:</b>", table_num_cell),
                Paragraph(f"<b>{total_str}</b>", table_num_cell),
            ],
        ]

        if is_credit:
            items_table_data.append([
                "",
                "",
                Paragraph("<b>Amount Paid:</b>", table_num_cell),
                Paragraph("NGN 0.00", table_num_cell),
            ])
            items_table_data.append([
                "",
                "",
                Paragraph("<b>Amount Due:</b>", table_num_cell),
                Paragraph(f"<b>{total_str}</b>", table_num_cell),
            ])
        else:
            items_table_data.append([
                "",
                "",
                Paragraph("<b>Amount Paid:</b>", table_num_cell),
                Paragraph(total_str, table_num_cell),
            ])
            items_table_data.append([
                "",
                "",
                Paragraph("<b>Balance Due:</b>", table_num_cell),
                Paragraph("NGN 0.00", table_num_cell),
            ])

        items_table = Table(items_table_data, colWidths=[250, 60, 115, 115])
        items_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.HexColor("#9ca3af")),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
                ("TOPPADDING", (0, 1), (-1, 1), 8),
                ("LINEBELOW", (0, 1), (-1, 1), 0.5, colors.HexColor("#e5e7eb")),
                ("TOPPADDING", (0, 2), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 2), (-1, -1), 4),
                ("LINEABOVE", (2, 3), (3, 3), 1, colors.HexColor("#9ca3af")),
            ])
        )
        elements.append(items_table)
        elements.append(Spacer(1, 16))

        # --- Payment Terms & Bank Details (if configured on template) ---
        if payment_terms:
            terms_data = [
                [Paragraph("<b>Payment Terms & Bank Details:</b>", section_heading)],
                [Paragraph(payment_terms, body_text)],
            ]
            terms_table = Table(terms_data, colWidths=[540])
            terms_table.setStyle(
                TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ])
            )
            elements.append(terms_table)
            elements.append(Spacer(1, 12))

        # --- Footer ---
        elements.append(
            HRFlowable(
                width="100%",
                thickness=0.5,
                color=colors.HexColor("#e5e7eb"),
                spaceAfter=12,
                spaceBefore=0,
            )
        )
        if footer_note:
            elements.append(Paragraph(footer_note, footer_style))
            elements.append(Spacer(1, 4))
        elements.append(Paragraph("Thank you for your business!", footer_style))
        elements.append(Spacer(1, 4))
        elements.append(
            Paragraph("Generated electronically by Waasz Business Assistant", footer_style)
        )

        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()

        return pdf_bytes, filename, transaction

    async def get_template(self, db: AsyncSession, business_id: UUID):
        from app.models.receipt_template import ReceiptTemplate
        stmt = select(ReceiptTemplate).where(ReceiptTemplate.business_id == business_id)
        res = await db.execute(stmt)
        return res.scalar_one_or_none()

    async def set_template(
        self,
        db: AsyncSession,
        business_id: UUID,
        business_display_name: str,
        contact_phone: str,
        address: str | None = None,
        business_logo: str | None = None,
        contact_email: str | None = None,
        payment_terms_note: str | None = None,
        footer_note: str | None = None,
    ):
        from app.models.receipt_template import ReceiptTemplate
        tmpl = await self.get_template(db, business_id)
        if not tmpl:
            tmpl = ReceiptTemplate(
                business_id=business_id,
                business_display_name=business_display_name,
                contact_phone=contact_phone,
                address=address,
                business_logo=business_logo,
                contact_email=contact_email,
                payment_terms_note=payment_terms_note,
                footer_note=footer_note,
            )
            db.add(tmpl)
        else:
            tmpl.business_display_name = business_display_name
            tmpl.contact_phone = contact_phone
            if address is not None:
                tmpl.address = address
            if business_logo is not None:
                tmpl.business_logo = business_logo
            if contact_email is not None:
                tmpl.contact_email = contact_email
            if payment_terms_note is not None:
                tmpl.payment_terms_note = payment_terms_note
            if footer_note is not None:
                tmpl.footer_note = footer_note
        await db.commit()
        await db.refresh(tmpl)
        return tmpl

    async def update_template(
        self,
        db: AsyncSession,
        business_id: UUID,
        **kwargs,
    ):
        tmpl = await self.get_template(db, business_id)
        if not tmpl:
            return None
        for key, val in kwargs.items():
            if val is not None and hasattr(tmpl, key):
                setattr(tmpl, key, val)
        await db.commit()
        await db.refresh(tmpl)
        return tmpl
