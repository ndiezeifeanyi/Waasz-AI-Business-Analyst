"""
Generates an executive, investor-grade PDF pitch deck for Waasz AI.
Built for Institutional Venture Capital Presentation (Pre-Seed Round).

Design Principles:
- Minimalist, clean executive light background (White & Crisp Slate).
- Restrained color palette: Deep Teal (#0F766E) accent, Charcoal Slate (#0F172A) headlines, Slate (#334155) body.
- Generous whitespace and vertical balance. No crowded card grids, no rainbow colors, no AI slop.
- Standard ISO currency codes (NGN) to prevent glyph corruption across PDF readers.
- Modern Segoe UI / TrueType typography with crisp visual hierarchy.
- Strict 16:9 widescreen layout with exactly 10 high-impact slides.
"""

import os
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    PageBreak,
)
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PDF = ROOT_DIR / "Waasz_AI_Executive_Pitch_Deck.pdf"
LOGO_PATH = ROOT_DIR / "admin" / "waasz_logo.jpg"

# 16:9 Presentation Dimensions in Points
PAGE_WIDTH = 842.0
PAGE_HEIGHT = 473.5
USABLE_WIDTH = 754.0  # 842 - 44*2

# Register clean TrueType fonts if available
FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITALIC = "Helvetica-Oblique"

try:
    if os.path.exists("C:/Windows/Fonts/segoeui.ttf"):
        pdfmetrics.registerFont(TTFont("SegoeUI", "C:/Windows/Fonts/segoeui.ttf"))
        pdfmetrics.registerFont(TTFont("SegoeUI-Bold", "C:/Windows/Fonts/segoeuib.ttf"))
        pdfmetrics.registerFont(TTFont("SegoeUI-Italic", "C:/Windows/Fonts/segoeuii.ttf"))
        FONT_REGULAR = "SegoeUI"
        FONT_BOLD = "SegoeUI-Bold"
        FONT_ITALIC = "SegoeUI-Italic"
except Exception as e:
    pass


class ExecutiveNumberedCanvas(canvas.Canvas):
    """Draws a clean, restrained executive footer and page counter."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_slide_chrome(num_pages)
            super().showPage()
        super().save()

    def draw_slide_chrome(self, page_count):
        self.saveState()

        # Suppress footers on cover slide
        if self._pageNumber > 1:
            # Subtle top accent mark
            self.setStrokeColor(colors.HexColor("#0F766E"))
            self.setLineWidth(2.5)
            self.line(44, PAGE_HEIGHT - 12, 108, PAGE_HEIGHT - 12)

            # Footer divider rule
            self.setStrokeColor(colors.HexColor("#E2E8F0"))
            self.setLineWidth(0.6)
            self.line(44, 28, PAGE_WIDTH - 44, 28)

            # Left running footer
            self.setFont(FONT_BOLD, 7.5)
            self.setFillColor(colors.HexColor("#0F766E"))
            self.drawString(44, 16, "WAASZ AI")

            self.setFont(FONT_REGULAR, 7.5)
            self.setFillColor(colors.HexColor("#64748B"))
            self.drawString(96, 16, "|   The WhatsApp-Native Financial OS & Credit Engine for MSMEs   |   Confidential")

            # Right page numbering
            page_str = f"SLIDE {self._pageNumber:02d} OF {page_count:02d}"
            self.setFont(FONT_BOLD, 7.5)
            self.setFillColor(colors.HexColor("#475569"))
            self.drawRightString(PAGE_WIDTH - 44, 16, page_str)

        self.restoreState()


def build_deck():
    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        leftMargin=44,
        rightMargin=44,
        topMargin=32,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    # Typography & Hierarchy
    kicker_style = ParagraphStyle(
        "Kicker",
        parent=styles["Normal"],
        fontName=FONT_BOLD,
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#0F766E"),
    )

    title_style = ParagraphStyle(
        "SlideTitle",
        parent=styles["Normal"],
        fontName=FONT_BOLD,
        fontSize=21,
        leading=25,
        textColor=colors.HexColor("#0F172A"),
    )

    subtitle_style = ParagraphStyle(
        "SlideSubtitle",
        parent=styles["Normal"],
        fontName=FONT_REGULAR,
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#475569"),
    )

    card_title = ParagraphStyle(
        "CardTitle",
        parent=styles["Normal"],
        fontName=FONT_BOLD,
        fontSize=11.5,
        leading=15,
        textColor=colors.HexColor("#0F172A"),
    )

    card_body = ParagraphStyle(
        "CardBody",
        parent=styles["Normal"],
        fontName=FONT_REGULAR,
        fontSize=9.5,
        leading=14.5,
        textColor=colors.HexColor("#334155"),
    )

    metric_val = ParagraphStyle(
        "MetricVal",
        parent=styles["Normal"],
        fontName=FONT_BOLD,
        fontSize=26,
        leading=30,
        textColor=colors.HexColor("#0F766E"),
    )

    metric_lbl = ParagraphStyle(
        "MetricLbl",
        parent=styles["Normal"],
        fontName=FONT_REGULAR,
        fontSize=9,
        leading=12.5,
        textColor=colors.HexColor("#475569"),
    )

    pill_text = ParagraphStyle(
        "PillText",
        parent=styles["Normal"],
        fontName=FONT_BOLD,
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#0F766E"),
    )

    story = []

    def make_header(kicker, title, subtitle):
        return [
            Paragraph(kicker, kicker_style),
            Spacer(1, 4),
            Paragraph(title, title_style),
            Spacer(1, 4),
            Paragraph(subtitle, subtitle_style),
            Spacer(1, 14),
        ]

    # =========================================================================
    # SLIDE 1: COVER SLIDE
    # =========================================================================
    story.append(Spacer(1, 24))

    logo_elem = ""
    if LOGO_PATH.exists():
        logo_elem = Image(str(LOGO_PATH), width=58, height=58)

    badge_table = Table(
        [[Paragraph("<b>CONFIDENTIAL INVESTOR MEMORANDUM</b> &nbsp;•&nbsp; PRE-SEED ROUND", pill_text)]],
        colWidths=[400],
    )
    badge_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDFA")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCFBF1")),
            ("PADDING", (0, 0), (-1, -1), 5),
            ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ])
    )

    cover_heading = ParagraphStyle(
        "CoverHeading",
        parent=styles["Normal"],
        fontName=FONT_BOLD,
        fontSize=30,
        leading=35,
        textColor=colors.HexColor("#0F172A"),
    )

    cover_sub = ParagraphStyle(
        "CoverSub",
        parent=styles["Normal"],
        fontName=FONT_REGULAR,
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#334155"),
    )

    cover_desc = ParagraphStyle(
        "CoverDesc",
        parent=styles["Normal"],
        fontName=FONT_REGULAR,
        fontSize=10,
        leading=15,
        textColor=colors.HexColor("#64748B"),
    )

    header_cols = [
        [
            logo_elem,
            [
                badge_table,
                Spacer(1, 10),
                Paragraph("<b>Waasz AI</b>", cover_heading),
                Spacer(1, 4),
                Paragraph("The WhatsApp-Native Financial OS & Alternative Credit Engine for African MSMEs", cover_sub),
                Spacer(1, 6),
                Paragraph(
                    "Turning informal voice notes and receipt photos into audited ledgers, daily business intelligence, and bankable creditworthiness — zero app downloads required.",
                    cover_desc,
                ),
            ],
        ]
    ]

    t_cover_header = Table(header_cols, colWidths=[70, 684])
    t_cover_header.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.append(t_cover_header)
    story.append(Spacer(1, 24))

    # 3 Executive summary pillars
    exec_cards = [
        [
            Paragraph("<b>The Wedge</b>", card_title),
            Paragraph("<b>The Technology</b>", card_title),
            Paragraph("<b>The Investment Goal</b>", card_title),
        ],
        [
            Paragraph(
                "40M+ Nigerian MSMEs run exclusively on WhatsApp. Rather than forcing them to download complex accounting apps, Waasz meets merchants directly in their everyday chat.",
                card_body,
            ),
            Paragraph(
                "Multi-modal AI transcribes Nigerian Pidgin/English voice notes, reads paper receipts via OCR, maintains double-entry books, and pushes daily profit summaries at 9 PM.",
                card_body,
            ),
            Paragraph(
                "Raising $100,000 Pre-Seed capital to expand across major trade hubs (Lagos, Onitsha, Aba, Kano) and partner with licensed MFBs to underwrite NGN 400M+ in micro-credit.",
                card_body,
            ),
        ],
    ]
    t_exec = Table(exec_cards, colWidths=[240, 240, 250])
    t_exec.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 14),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_exec)
    story.append(Spacer(1, 22))

    meta_line = Paragraph(
        "<b>Founding Team:</b> Ndieze Ifeanyi & Team &nbsp;•&nbsp; <b>Infrastructure:</b> Live Production on AWS & Verified Meta Cloud API &nbsp;•&nbsp; <b>Contact:</b> founder@waasz.ai",
        ParagraphStyle("CoverMeta", parent=styles["Normal"], fontName=FONT_REGULAR, fontSize=8.5, textColor=colors.HexColor("#64748B")),
    )
    story.append(meta_line)
    story.append(PageBreak())

    # =========================================================================
    # SLIDE 2: THE PROBLEM (The Invisible African Merchant)
    # =========================================================================
    story.extend(
        make_header(
            "01 / THE REAL PROBLEM",
            "40 Million Nigerian MSMEs Are Financially Invisible to Banks",
            "Informal merchants generate billions in daily economic activity, but operate in complete accounting darkness.",
        )
    )

    left_story = [
        [Paragraph("<b>The Reality in Balogun & Trade Fair Markets</b>", card_title)],
        [
            Paragraph(
                "Mama Chioma sells NGN 75,000 worth of fabrics every day. She tracks customer debt in a water-stained exercise notebook, calculates expenses from memory, and has no formal financial statements.<br/><br/>"
                "When she needs NGN 300,000 working capital to restock inventory before peak season, commercial banks demand audited books and titled real estate collateral.<br/><br/>"
                "<b>The Result:</b> She gets rejected immediately. Not because her business isn't profitable, but because her cash flow cannot be independently verified.",
                card_body,
            )
        ],
    ]
    t_left = Table(left_story, colWidths=[360])
    t_left.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 16),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )

    right_stats = [
        [
            Paragraph("41.5M", metric_val),
            Paragraph("<b>MSMEs across Nigeria</b><br/>Driving 48% of national GDP but over 90% lack formal accounting records.", metric_lbl),
        ],
        [
            Paragraph("&gt; 90%", metric_val),
            Paragraph("<b>Credit Rejection Rate</b><br/>Lenders cannot assess risk without verifiable transaction and turnover history.", metric_lbl),
        ],
        [
            Paragraph("NGN 13T+", metric_val),
            Paragraph("<b>Unmet MSME Credit Gap</b><br/>Massive liquidity sits in financial institutions unable to deploy safely.", metric_lbl),
        ],
    ]
    t_right = Table(right_stats, colWidths=[108, 252])
    t_right.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 14),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#E2E8F0")),
            ("LINEBELOW", (0, 1), (-1, 1), 0.5, colors.HexColor("#E2E8F0")),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )

    prob_cols = [[t_left, t_right]]
    t_prob_layout = Table(prob_cols, colWidths=[370, 384])
    t_prob_layout.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(t_prob_layout)
    story.append(PageBreak())

    # =========================================================================
    # SLIDE 3: THE WEDGE (Why Software Fails vs Why WhatsApp Wins)
    # =========================================================================
    story.extend(
        make_header(
            "02 / THE STRATEGIC WEDGE",
            "Why Accounting Apps Fail in Nigeria — And Why WhatsApp Wins",
            "Informal traders do not change their behavior for software. The software must adapt to where they already live.",
        )
    )

    wedge_data = [
        [
            Paragraph("<b>Traditional Accounting Apps (Why They Fail)</b>", card_title),
            Paragraph("<b>Waasz WhatsApp-Native Wedge (Why We Win)</b>", card_title),
        ],
        [
            Paragraph(
                "• <b>Heavy Download Barrier:</b> Requires downloading a 60MB+ native app on cheap, low-memory Android smartphones.<br/><br/>"
                "• <b>Steep Cognitive Friction:</b> Forces non-technical traders to navigate complex accounting menus, categories, and input forms.<br/><br/>"
                "• <b>85%+ Day-30 Churn:</b> Merchants forget to open the app after a busy week in the market; bookkeeping is quickly abandoned.<br/><br/>"
                "• <b>Passive & Isolated:</b> Does not connect with customers or suppliers where daily business communication happens.",
                card_body,
            ),
            Paragraph(
                "• <b>Zero Download Friction:</b> 100% hosted inside WhatsApp. Every merchant already opens WhatsApp 15+ times a day.<br/><br/>"
                "• <b>Conversational Simplicity:</b> Merchants simply send a voice note in Nigerian Pidgin or snap a photo of paper invoices.<br/><br/>"
                "• <b>Proactive Daily Retention:</b> Waasz pushes an automated financial summary at 9 PM and an activity nudge at 6 PM.<br/><br/>"
                "• <b>Connected Network:</b> Sends automated debt recovery notices and digital receipts directly to customers via WhatsApp.",
                card_body,
            ),
        ],
    ]
    t_wedge = Table(wedge_data, colWidths=[370, 384])
    t_wedge.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F8FAFC")),
            ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#F0FDFA")),
            ("BOX", (0, 0), (0, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("BOX", (1, 0), (1, -1), 1, colors.HexColor("#0F766E")),
            ("PADDING", (0, 0), (-1, -1), 16),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_wedge)
    story.append(PageBreak())

    # =========================================================================
    # SLIDE 4: THE SOLUTION (Core Capabilities)
    # =========================================================================
    story.extend(
        make_header(
            "03 / THE PRODUCT",
            "An Autonomous AI CFO in Every Merchant's Pocket",
            "Waasz turns everyday chat into bank-grade accounting and verifiable credit intelligence.",
        )
    )

    sol_cards = [
        [
            Paragraph("<b>1. Voice & Vision Capture</b>", card_title),
            Paragraph("<b>2. Autonomous Ledger & Debt</b>", card_title),
            Paragraph("<b>3. Executive 9 PM Insights</b>", card_title),
        ],
        [
            Paragraph(
                "• <b>Voice-First Input:</b> Merchants speak naturally in Pidgin, Hausa, Yoruba, or English: <i>'Sold 3 lace materials to Mama Ngozi for NGN 45k, she pay NGN 30k balance NGN 15k'</i>.<br/><br/>"
                "• <b>Receipt OCR:</b> Snaps photos of crumpled supplier invoices; OCR extracts line items, quantities, and unit costs instantly.",
                card_body,
            ),
            Paragraph(
                "• <b>Double-Entry Engine:</b> Automatically categorizes transactions into revenue, COGS, expenses, and receivables.<br/><br/>"
                "• <b>Customer Debt Recovery:</b> Tracks outstanding balances and generates polite, professional payment reminder notices directly on WhatsApp.",
                card_body,
            ),
            Paragraph(
                "• <b>Daily 9 PM Report:</b> Delivers an evening financial summary every night: Today's Sales, Expenses, Net Profit, and Cash Position.<br/><br/>"
                "• <b>Weekly PDF Statement:</b> Dispatches a structured, downloadable profit & loss statement every Sunday night for bank review.",
                card_body,
            ),
        ],
    ]
    t_sol = Table(sol_cards, colWidths=[244, 244, 252])
    t_sol.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 16),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_sol)
    story.append(PageBreak())

    # =========================================================================
    # SLIDE 5: USER JOURNEY (Step-by-Step Walkthrough)
    # =========================================================================
    story.extend(
        make_header(
            "04 / PRODUCT WALKTHROUGH",
            "The Merchant Experience: From Voice Note to Financial Clarity",
            "Zero operational friction. Designed for high-speed, chaotic market trading environments.",
        )
    )

    steps_data = [
        [
            Paragraph("<b>STEP 1</b>", kicker_style),
            Paragraph("<b>STEP 2</b>", kicker_style),
            Paragraph("<b>STEP 3</b>", kicker_style),
            Paragraph("<b>STEP 4</b>", kicker_style),
        ],
        [
            Paragraph("<b>Record in Seconds</b>", card_title),
            Paragraph("<b>AI Verification Gate</b>", card_title),
            Paragraph("<b>Daily Intelligence</b>", card_title),
            Paragraph("<b>Unlocked Credit</b>", card_title),
        ],
        [
            Paragraph(
                "Trader sends a quick voice note or invoice photo between customer sales. No typing required.",
                card_body,
            ),
            Paragraph(
                "Waasz parses items, prices, and balances, prompting the trader with quick confirmation buttons.",
                card_body,
            ),
            Paragraph(
                "At 9 PM, trader receives their daily net profit, cash balance, and who owes them money.",
                card_body,
            ),
            Paragraph(
                "After 60 days of consistent logging, merchant unlocks a verified cash-flow statement for lenders.",
                card_body,
            ),
        ],
    ]
    t_steps = Table(steps_data, colWidths=[184, 184, 184, 188])
    t_steps.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 14),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_steps)
    story.append(Spacer(1, 14))

    # Real example banner
    demo_box = [
        [
            Paragraph(
                "<b>Real Customer Interaction Example:</b><br/>"
                "<i>Merchant:</i> 'Waasz, I sold 2 pairs of shoes to Emeka for NGN 28,000. He paid NGN 20,000 transfer, balance next week.'<br/>"
                "<i>Waasz AI:</i> 'Recorded! Added NGN 28k to sales, NGN 20k cash collected, and NGN 8k logged under Emeka's debt tracker. Daily profit updated.'",
                card_body,
            )
        ]
    ]
    t_demo = Table(demo_box, colWidths=[754])
    t_demo.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDFA")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#CCFBF1")),
            ("PADDING", (0, 0), (-1, -1), 14),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_demo)
    story.append(PageBreak())

    # =========================================================================
    # SLIDE 6: TRACTION & MATURITY (Builders Festival Priority)
    # =========================================================================
    story.extend(
        make_header(
            "05 / MARKET TRACTION & READINESS",
            "Live in Production on AWS With Active Nigerian Merchants",
            "We are not an idea or mock-up. Our engine is deployed and processing real transactions daily.",
        )
    )

    traction_metrics = [
        [
            [Paragraph("14", metric_val), Paragraph("<b>Active MSME Merchants</b><br/>Logging daily transactions in our closed beta cohort.", metric_lbl)],
            [Paragraph("3", metric_val), Paragraph("<b>Major Trade Clusters</b><br/>Lagos Trade Fair, Balogun, and Ibadan retail traders.", metric_lbl)],
            [Paragraph("250+", metric_val), Paragraph("<b>Transactions Processed</b><br/>Sales, expense invoices, and credit sales recorded.", metric_lbl)],
            [Paragraph("100%", metric_val), Paragraph("<b>Cloud API Verified</b><br/>Official Meta WhatsApp Cloud API v21.0 infrastructure.", metric_lbl)],
        ]
    ]
    t_trac_metrics = Table(traction_metrics, colWidths=[184, 184, 184, 188])
    t_trac_metrics.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 14),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_trac_metrics)
    story.append(Spacer(1, 14))

    proof_data = [
        [
            Paragraph("<b>Battle-Tested Architecture</b>", card_title),
            Paragraph("<b>Direct Merchant Validation</b>", card_title),
        ],
        [
            Paragraph(
                "• <b>Live Cloud Server:</b> AWS EC2 Ubuntu production instance running 24/7 with automated Systemd recovery daemons.<br/>"
                "• <b>PostgreSQL Ledger:</b> ACID-compliant double-entry database with strict accounting integrity.<br/>"
                "• <b>Cost-Optimized AI Pipeline:</b> Multi-model routing (Gemini 1.5 Flash + Groq Llama-3) ensures sub-cent operating cost per message.<br/>"
                "• <b>Founder Telemetry:</b> Live analytics portal monitoring user activity, message throughput, and report delivery.",
                card_body,
            ),
            Paragraph(
                "<i>'Before Waasz, I had no idea if I was making profit or running at a loss because I was just using cash from sales to buy food and restock. Getting my daily summary every night at 9 PM changed how I run my shop.'</i><br/><br/>"
                "<b>— Beta Merchant, Provisions & FMCG Retailer, Lagos</b><br/><br/>"
                "<b>Key Validation Insight:</b> Merchants do not need training. Because it is in WhatsApp, 100% of onboarded users understood how to record sales within 2 minutes.",
                card_body,
            ),
        ],
    ]
    t_proof = Table(proof_data, colWidths=[370, 384])
    t_proof.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 16),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_proof)
    story.append(PageBreak())

    # =========================================================================
    # SLIDE 7: BUSINESS MODEL & UNIT ECONOMICS
    # =========================================================================
    story.extend(
        make_header(
            "06 / BUSINESS MODEL",
            "High-Margin Recurring SaaS Powered by Credit Monetization",
            "A dual revenue model designed for grassroots affordability and institutional scalability.",
        )
    )

    biz_data = [
        [
            Paragraph("<b>Stream 1: Merchant SaaS Subscription</b>", card_title),
            Paragraph("<b>Stream 2: Institutional Loan Origination</b>", card_title),
        ],
        [
            Paragraph(
                "• <b>14-Day Free Onboarding Trial:</b> Merchants experience immediate value with automated bookkeeping and daily reports.<br/><br/>"
                "• <b>Affordable Flat Fee:</b> <b>NGN 1,000 / month ($0.70/mo)</b> for unlimited voice/photo bookkeeping, customer debt reminders, and weekly PDF statements.<br/><br/>"
                "• <b>Sub-Cent Cost Structure:</b> Efficient AI model routing keeps our compute cost below NGN 85 per active merchant per month, delivering an <b>85%+ gross margin</b>.<br/><br/>"
                "• <b>High Retention Loop:</b> Automated daily 9 PM reports create an indispensable feedback loop that drives long-term customer stickiness.",
                card_body,
            ),
            Paragraph(
                "• <b>Bank & MFB Origination Commission:</b> <b>1.5% – 2.0% success fee</b> paid by lending partners on loans disbursed via Waasz credit underwriting.<br/><br/>"
                "• <b>Customer Acquisition Savings:</b> Banks spend upwards of NGN 15,000 to acquire and verify an SME customer. Waasz delivers pre-vetted, active businesses at near-zero CAC.<br/><br/>"
                "• <b>Embedded Repayment Sweeps:</b> Automated micro-repayment reminders ensure lower default rates, driving recurring partnership revenue.",
                card_body,
            ),
        ],
    ]
    t_biz = Table(biz_data, colWidths=[370, 384])
    t_biz.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 16),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_biz)
    story.append(PageBreak())

    # =========================================================================
    # SLIDE 8: MARKET OPPORTUNITY
    # =========================================================================
    story.extend(
        make_header(
            "07 / MARKET OPPORTUNITY",
            "A $2.6 Trillion Informal Economy Ready for Digital Formalization",
            "Nigeria is the proving ground; Sub-Saharan Africa is the scale destination.",
        )
    )

    mkt_cards = [
        [
            Paragraph("<b>Total Addressable Market (TAM)</b>", card_title),
            Paragraph("<b>Serviceable Addressable Market (SAM)</b>", card_title),
            Paragraph("<b>Serviceable Obtainable Market (SOM)</b>", card_title),
        ],
        [
            Paragraph(
                "<b>$2.6 Trillion</b><br/><br/>"
                "Total annual informal retail trade turnover across Sub-Saharan Africa, representing over 100M micro and small enterprises operating predominantly in cash.",
                card_body,
            ),
            Paragraph(
                "<b>41.5 Million MSMEs</b><br/><br/>"
                "Nigerian MSME landscape driving NGN 220B+ in daily commerce. Over 73% of Nigerian smartphone merchants use WhatsApp as their primary business communication tool.",
                card_body,
            ),
            Paragraph(
                "<b>150,000 Merchants</b><br/><br/>"
                "Phase 1 target across retail trade clusters, FMCG distributors, and women cooperatives in Lagos, Kano, Ibadan, and Abuja over 24 months.",
                card_body,
            ),
        ],
    ]
    t_mkt = Table(mkt_cards, colWidths=[244, 244, 252])
    t_mkt.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 16),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_mkt)
    story.append(Spacer(1, 14))

    # Competitive Moat Banner
    moat_box = [
        [
            Paragraph(
                "<b>Our Sustainable Competitive Moat:</b><br/>"
                "1. <b>Zero-Friction Distribution:</b> No app downloads means viral peer-to-peer referral in market associations.<br/>"
                "2. <b>Proprietary Alternative Ledger Data:</b> Daily granular transaction histories that traditional credit bureaus cannot access.<br/>"
                "3. <b>High-Frequency Daily Habit:</b> Evening 9 PM reports create an indispensable daily feedback loop for shop owners.",
                card_body,
            )
        ]
    ]
    t_moat = Table(moat_box, colWidths=[754])
    t_moat.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDFA")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#CCFBF1")),
            ("PADDING", (0, 0), (-1, -1), 14),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_moat)
    story.append(PageBreak())

    # =========================================================================
    # SLIDE 9: STRATEGIC & REGULATORY MOAT
    # =========================================================================
    story.extend(
        make_header(
            "08 / STRATEGIC & REGULATORY MOAT",
            "Why Banks, MFBs & Government Agencies Partner With Waasz",
            "Aligning directly with national digital formalization, CBN Open Banking, and credit inclusion.",
        )
    )

    inst_data = [
        [
            Paragraph("<b>1. Government & NITDA Mandate</b>", card_title),
            Paragraph("<b>2. Commercial & Microfinance Banks</b>", card_title),
            Paragraph("<b>3. Women & Grassroots Inclusion</b>", card_title),
        ],
        [
            Paragraph(
                "• <b>National Digital Formalization:</b> Digitizes invisible trade without requiring government hardware subsidies or training bootcamps.<br/><br/>"
                "• <b>Tax & Compliance Readiness:</b> Prepares micro-businesses for gradual formal registration and economic integration smoothly.",
                card_body,
            ),
            Paragraph(
                "• <b>De-Risked Micro-Credit:</b> Lenders replace guesswork with 90-day verified cash flow, turnover, and debt recovery metrics.<br/><br/>"
                "• <b>Low-Cost Deposit Pipeline:</b> Drives low-cost CASA deposits as merchants open Tier-1/Tier-2 accounts to receive credit disbursements.",
                card_body,
            ),
            Paragraph(
                "• <b>Empowering Women Traders:</b> Over 45% of informal traders are women who lack collateral deeds. Waasz democratizes credit based on real sales.<br/><br/>"
                "• <b>Localized Pidgin Intelligence:</b> Overcomes literacy barriers by processing natural voice notes in local market dialects.",
                card_body,
            ),
        ],
    ]
    t_inst = Table(inst_data, colWidths=[244, 244, 252])
    t_inst.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 16),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_inst)
    story.append(PageBreak())

    # =========================================================================
    # SLIDE 10: THE ASK & 12-MONTH EXECUTION ROADMAP
    # =========================================================================
    story.extend(
        make_header(
            "09 / THE ASK & ROADMAP",
            "Seeking $100,000 Pre-Seed Funding & Institutional Pilot Partners",
            "Deploying capital into merchant cluster acquisition, lender API integrations, and product expansion.",
        )
    )

    ask_data = [
        [
            Paragraph("<b>Investment & Capital Allocation</b>", card_title),
            Paragraph("<b>Institutional & Commercial Objectives</b>", card_title),
        ],
        [
            Paragraph(
                "<b>Seeking $100,000 Pre-Seed Investment (12-Month Runway)</b><br/><br/>"
                "• <b>45% ($45k) — Market Expansion:</b> Field activations across key commercial hubs (Lagos, Onitsha, Aba, Kano) to onboard 15,000 merchants and activate 5,000 daily traders.<br/>"
                "• <b>35% ($35k) — Core Team Build:</b> Hire Senior Backend/Fintech Eng, Head of Lending (ex-MFB credit lead), Field Ops Lead, and Support Lead.<br/>"
                "• <b>20% ($20k) — Enterprise Infra & Compliance:</b> Dedicated enterprise AI quotas (zero rate limits), Meta OBA verification (green badge), and NDPR compliance.",
                card_body,
            ),
            Paragraph(
                "<b>Seeking Bank & Lending Pilot Partnerships</b><br/><br/>"
                "• <b>Bank / MFB Lending Integration:</b> Partner with 2–3 licensed Microfinance Banks to underwrite <b>NGN 400M+ ($250k)</b> in collateral-free inventory loans based on verified cash-flow scores.<br/>"
                "• <b>Trade Union Activations:</b> Formal onboarding partnerships with market associations across ASPAMDA, Trade Fair, Alaba, and Ariaria Market.<br/>"
                "• <b>Financial Inclusion & Scale:</b> Democratizing micro-credit for credit-invisible traders while generating <b>$25k MRR</b> for Waasz.",
                card_body,
            ),
        ],
    ]
    t_ask = Table(ask_data, colWidths=[370, 384])
    t_ask.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 12),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_ask)
    story.append(Spacer(1, 12))

    # Milestones bar
    milestones_data = [
        [
            Paragraph("<b>Months 1–3: Foundation</b><br/>1,000 active merchants; enterprise AI quotas live; core team onboarded.", card_body),
            Paragraph("<b>Months 4–6: Credit Launch</b><br/>2,500 active merchants; Meta OBA verified; first MFB lending integration live.", card_body),
            Paragraph("<b>Months 7–12: Scale & Seed Ready</b><br/>5,000+ active traders; NGN 8B+ ($5M) GMV; NGN 400M+ credit originated; $25k MRR.", card_body),
        ]
    ]
    t_mile = Table(milestones_data, colWidths=[244, 244, 252])
    t_mile.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDFA")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#CCFBF1")),
            ("PADDING", (0, 0), (-1, -1), 11),
            ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ])
    )
    story.append(t_mile)
    story.append(Spacer(1, 10))

    # CTA Footer
    cta_line = Paragraph(
        "<b>Ready for Immediate Live Demonstration:</b> Test the WhatsApp bot live with voice notes right now &nbsp;•&nbsp; <b>Email:</b> founder@waasz.ai &nbsp;•&nbsp; <b>AWS Production Live</b>",
        ParagraphStyle("CTAFooter", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=8.5, textColor=colors.HexColor("#0F766E"), alignment=1),
    )
    story.append(cta_line)

    doc.build(story, canvasmaker=ExecutiveNumberedCanvas)
    print("Executive pitch deck generated successfully at:", OUTPUT_PDF)


if __name__ == "__main__":
    build_deck()
