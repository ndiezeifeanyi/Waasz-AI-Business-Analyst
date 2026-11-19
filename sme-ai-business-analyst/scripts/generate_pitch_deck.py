"""
Generates an executive, professional PDF pitch deck for Waasz AI for the Zenith Bank Zecathon.
Track: Smart SME Financing and Growth Platform.
"""

import os
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
from reportlab.pdfgen import canvas

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PDF = ROOT_DIR / "Waasz_AI_Zenith_Hackathon_Pitch_Deck.pdf"
LOGO_PATH = ROOT_DIR / "admin" / "waasz_logo.jpg"


class NumberedCanvas(canvas.Canvas):
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
            self.draw_footer(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_footer(self, page_count):
        self.saveState()
        # Top accent bar
        self.setFillColor(colors.HexColor("#E11D48")) # Zenith / Waasz Red accent
        self.rect(0, 608, 792, 4, fill=1, stroke=0)
        
        # Bottom footer
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawString(36, 18, "Waasz AI — Smart SME Financing & Growth Platform | Zenith Bank Tech Fair (Zecathon)")
        page_text = f"Slide {self._pageNumber} of {page_count}"
        self.drawRightString(756, 18, page_text)
        self.restoreState()


def build_deck():
    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=landscape(letter),
        leftMargin=36,
        rightMargin=36,
        topMargin=32,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "CoverTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=32,
        leading=38,
        textColor=colors.HexColor("#FFFFFF"),
    )

    subtitle_style = ParagraphStyle(
        "CoverSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=15,
        leading=20,
        textColor=colors.HexColor("#CBD5E1"),
    )

    track_style = ParagraphStyle(
        "CoverTrack",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#F43F5E"),
    )

    slide_heading = ParagraphStyle(
        "SlideHeading",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#FFFFFF"),
    )

    slide_subheading = ParagraphStyle(
        "SlideSubheading",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#94A3B8"),
    )

    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#E2E8F0"),
    )

    card_header = ParagraphStyle(
        "CardHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#F8FAFC"),
    )

    card_body = ParagraphStyle(
        "CardBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#CBD5E1"),
    )

    stat_num = ParagraphStyle(
        "StatNum",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=28,
        textColor=colors.HexColor("#F43F5E"),
        alignment=1,
    )

    stat_label = ParagraphStyle(
        "StatLabel",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#94A3B8"),
        alignment=1,
    )

    story = []

    def add_header(title, subtitle):
        story.append(Paragraph(title, slide_heading))
        story.append(Paragraph(subtitle, slide_subheading))
        story.append(Spacer(1, 14))

    # ==========================================
    # SLIDE 1: COVER SLIDE
    # ==========================================
    story.append(Spacer(1, 40))
    logo_elem = ""
    if LOGO_PATH.exists():
        logo_elem = Image(str(LOGO_PATH), width=65, height=65)

    cover_data = [
        [
            logo_elem,
            [
                Paragraph("<b>Waasz AI</b>", title_style),
                Spacer(1, 4),
                Paragraph("Conversational Financial OS & Alternative Credit Engine for SMEs", subtitle_style),
                Spacer(1, 8),
                Paragraph("<b>TRACK: SMART SME FINANCING AND GROWTH PLATFORM</b> • ZENITH BANK ZECATHON", track_style),
            ],
        ]
    ]

    cover_table = Table(cover_data, colWidths=[80, 640])
    cover_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.append(cover_table)
    story.append(Spacer(1, 35))

    overview_box = [
        [
            Paragraph("<b>The Core Mission:</b>", card_header),
            Paragraph("<b>Target Audience:</b>", card_header),
            Paragraph("<b>Zenith Bank Strategic Fit:</b>", card_header),
        ],
        [
            Paragraph("Transforming informal, invisible merchants into bankable businesses using WhatsApp-native Generative AI and alternative data.", card_body),
            Paragraph("40M+ Nigerian MSMEs (especially women-led market businesses) lacking formal accounting tools and credit records.", card_body),
            Paragraph("Direct SME customer acquisition, verifiable alternative credit scoring, and embedded loan disbursement via Zenith API rails.", card_body),
        ],
    ]
    t_over = Table(overview_box, colWidths=[235, 235, 250])
    t_over.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
        ("PADDING", (0, 0), (-1, -1), 12),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t_over)
    story.append(Spacer(1, 30))

    meta_text = Paragraph(
        "<b>Presenter:</b> Ndieze Ifeanyi & Team &nbsp;•&nbsp; <b>Status:</b> Live Production Deployment on AWS &nbsp;•&nbsp; <b>Meta API:</b> Verified v21.0 Cloud API",
        ParagraphStyle("CoverMeta", parent=styles["Normal"], fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#64748B"))
    )
    story.append(meta_text)
    story.append(PageBreak())

    # ==========================================
    # SLIDE 2: THE PROBLEM (SME Credit Blindspot)
    # ==========================================
    add_header("1. Problem: The SME Credit Blindspot in Nigeria", "Over 95% of informal merchants cannot access bank financing despite generating steady cash flow.")

    stat_cards = [
        [
            [Paragraph("41.5M", stat_num), Paragraph("MSMEs in Nigeria driving 48% of GDP", stat_label)],
            [Paragraph("90%+", stat_num), Paragraph("Operate strictly via cash and informal ledgers", stat_label)],
            [Paragraph("&gt; 95%", stat_num), Paragraph("Rejected for bank credit due to zero formal data", stat_label)],
            [Paragraph("₦13T+", stat_num), Paragraph("Estimated unmet financing gap for Nigerian SMEs", stat_label)],
        ]
    ]
    t_stats = Table(stat_cards, colWidths=[175, 175, 175, 195])
    t_stats.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
        ("PADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t_stats)
    story.append(Spacer(1, 16))

    problem_breakdown = [
        [
            Paragraph("<b>Paper Ledgers & Lost Memory</b>", card_header),
            Paragraph("<b>Severe Disadvantage for Women-Led SMEs</b>", card_header),
            Paragraph("<b>The Bank's Dilemma (Zenith's Risk)</b>", card_header),
        ],
        [
            Paragraph("Market vendors keep records in physical notebooks that get water-damaged, misplaced, or forgotten. They do not know their monthly net profit, inventory valuation, or true cash flow.", card_body),
            Paragraph("Over 40% of micro-enterprises are women-led (traders, caterers, fashion). They face an even higher hurdle accessing loans due to lack of land collateral and formal documentation.", card_body),
            Paragraph("Banks have working capital ready to deploy, but zero visibility into informal trade. Traditional credit bureaus (CRC, CreditRegistry) have zero data on informal traders. Result: High default fear.", card_body),
        ],
    ]
    t_prob = Table(problem_breakdown, colWidths=[235, 235, 250])
    t_prob.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
        ("PADDING", (0, 0), (-1, -1), 12),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t_prob)
    story.append(PageBreak())

    # ==========================================
    # SLIDE 3: SOLUTION OVERVIEW (Waasz AI)
    # ==========================================
    add_header("2. Solution: WhatsApp-Native AI Financial Operating System", "Zero app downloads. Zero software friction. Just conversational voice, photos, and chat on WhatsApp.")

    sol_cards = [
        [
            Paragraph("<b>1. Multi-Modal Input (Voice & OCR)</b>", card_header),
            Paragraph("<b>2. Autonomous Double-Entry Ledger</b>", card_header),
            Paragraph("<b>3. Alternative Credit Engine</b>", card_header),
        ],
        [
            Paragraph("Merchants speak in English/Pidgin (<i>'Sold 4 cartons Indomie for ₦18k'</i>) or snap photos of crumpled supplier receipts. Multi-modal vision and audio extract line items, prices, and suppliers instantly.", card_body),
            Paragraph("Every record is verified via an explicit confirmation gate. Waasz categorizes sales vs. expenses, tracks customer debts, sends automated reminders, and computes true net profit in real time.", card_body),
            Paragraph("Waasz aggregates daily trading discipline into a structured, bank-grade Alternative Credit Score and verifiable cash flow statement ready for credit underwriting.", card_body),
        ],
    ]
    t_sol = Table(sol_cards, colWidths=[235, 235, 250])
    t_sol.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
        ("PADDING", (0, 0), (-1, -1), 14),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t_sol)
    story.append(Spacer(1, 16))

    how_it_works_banner = [
        [
            Paragraph(
                "<b>The Merchant Journey:</b> 1. Snap supplier receipt ➔ 2. Speak/Type sales on WhatsApp ➔ 3. AI confirms & updates ledger ➔ 4. Weekly financial report delivered ➔ 5. Unlock pre-approved Zenith Bank credit.",
                ParagraphStyle("Journey", parent=styles["Normal"], fontName="Helvetica", fontSize=9, leading=13, textColor=colors.HexColor("#F8FAFC"))
            )
        ]
    ]
    t_journey = Table(how_it_works_banner, colWidths=[720])
    t_journey.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0F766E")), # Teal
        ("PADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    story.append(t_journey)
    story.append(PageBreak())

    # ==========================================
    # SLIDE 4: INNOVATION & FRAUD PREVENTION
    # ==========================================
    add_header("3. Innovation: Eliminating 'Ghost Bookkeeping' with 3-Way Triangulation", "How Waasz prevents loan fraud and falsified ledgers through multi-dimensional verification.")

    fraud_cards = [
        [
            Paragraph("<b>Pillar 1: Cost-of-Goods Reconciliation</b>", card_header),
            Paragraph("<b>Pillar 2: Counterparty Delivery</b>", card_header),
            Paragraph("<b>Pillar 3: Anomaly & Inflow Cross-Check</b>", card_header),
        ],
        [
            Paragraph("<b>Sales without Expenses is impossible.</b> Waasz requires merchants to scan supplier restock receipts via OCR. Retail sales deplete tracked inventory. A merchant cannot claim ₦5M sales without verifiable stock invoices.", card_body),
            Paragraph("Transactions record real customer counterparties. When a sale on credit is recorded, Waasz generates a digital receipt and sends automated debt recovery notices directly to the customer on WhatsApp.", card_body),
            Paragraph("AI statistical forensics (Benford's Law + time entropy) detect fake round-number entries. Triangulated against Zenith Bank account inflow / POS receipts for 100% verified turnover.", card_body),
        ],
    ]
    t_fraud = Table(fraud_cards, colWidths=[235, 235, 250])
    t_fraud.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
        ("PADDING", (0, 0), (-1, -1), 14),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t_fraud)
    story.append(Spacer(1, 16))

    credit_ladder_box = [
        [
            Paragraph("<b>The Dynamic Micro-Credit Ladder:</b>", card_header),
            Paragraph("<b>Tier 1 (Day 1-30):</b> Consistent bookkeeping unlocks ₦25,000–₦50,000 inventory restock (7-day turnaround).", card_body),
            Paragraph("<b>Tier 2 (Day 60):</b> 100% on-time repayment unlocks ₦150,000 working capital.", card_body),
            Paragraph("<b>Tier 3 (Day 90+):</b> Verified cash flow qualifies merchant for ₦500,000+ formal Zenith SME credit lines.", card_body),
        ]
    ]
    t_ladder = Table(credit_ladder_box, colWidths=[180, 180, 180, 180])
    t_ladder.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E1B4B")), # Deep Indigo
        ("PADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t_ladder)
    story.append(PageBreak())

    # ==========================================
    # SLIDE 5: ZENITH BANK INTEGRATION
    # ==========================================
    add_header("4. Zenith Bank Value Proposition & Integration Points", "Creating a high-margin lending channel and low-cost deposit pipeline for Zenith Bank.")

    zenith_benefits = [
        [
            Paragraph("<b>1. Embedded Lending (Z-Credit)</b>", card_header),
            Paragraph("<b>2. New SME Deposit Pipeline</b>", card_header),
            Paragraph("<b>3. De-Risked Repayment Deductions</b>", card_header),
        ],
        [
            Paragraph("Pre-approved loan offers triggered inside WhatsApp based on Waasz alternative scores. Disbursed instantly into the merchant's Zenith Bank account via Zenith Core Banking APIs.", card_body),
            Paragraph("Unbanked merchants open Tier-1/Tier-2 Zenith SME accounts directly from WhatsApp in under 2 minutes to receive credit and financial reports, growing low-cost CASA deposits.", card_body),
            Paragraph("Automated daily micro-splits: As confirmed sales arrive, small daily deductions automatically service the loan, eliminating lump-sum end-of-month default risk.", card_body),
        ],
    ]
    t_zen = Table(zenith_benefits, colWidths=[235, 235, 250])
    t_zen.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
        ("PADDING", (0, 0), (-1, -1), 14),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t_zen)
    story.append(Spacer(1, 16))

    integration_matrix = [
        [
            Paragraph("<b>Integration Component</b>", card_header),
            Paragraph("<b>Zenith Bank Rail</b>", card_header),
            Paragraph("<b>Waasz AI Implementation</b>", card_header),
        ],
        [
            Paragraph("Alternative Scoring", card_body),
            Paragraph("Credit Underwriting / Risk Engine", card_body),
            Paragraph("Exports 90-day verified cash flow, inventory turnover, and debt recovery ratios.", card_body),
        ],
        [
            Paragraph("Loan Disbursement", card_body),
            Paragraph("Zenith Transfer / Core Banking API", card_body),
            Paragraph("One-click WhatsApp confirmation triggers instant credit to merchant account.", card_body),
        ],
        [
            Paragraph("Loan Servicing", card_body),
            Paragraph("Direct Debit / Inflow Sweep", card_body),
            Paragraph("Daily micro-repayment reminders and auto-sweep on inward sales transfers.", card_body),
        ],
    ]
    t_matrix = Table(integration_matrix, colWidths=[180, 220, 320])
    t_matrix.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#1E293B")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#475569")),
        ("PADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t_matrix)
    story.append(PageBreak())

    # ==========================================
    # SLIDE 6: TARGET MARKET & IMPACT
    # ==========================================
    add_header("5. Market Size, Target Users & Financial Inclusion Impact", "A $2.6 Trillion market opportunity with an acute focus on empowering women-led informal trade.")

    market_data = [
        [
            [Paragraph("<b>TAM (Africa)</b>", card_header), Paragraph("$2.6 Trillion informal economy turnover across Sub-Saharan Africa.", card_body)],
            [Paragraph("<b>SAM (Nigeria)</b>", card_header), Paragraph("41.5 Million MSMEs; 73% rely on WhatsApp as primary communication tool.", card_body)],
            [Paragraph("<b>SOM (Phase 1)</b>", card_header), Paragraph("150,000 Retailers, FMCG traders, and women cooperatives in Lagos & Kano.", card_body)],
        ]
    ]
    t_mkt = Table(market_data, colWidths=[240, 240, 240])
    t_mkt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
        ("PADDING", (0, 0), (-1, -1), 12),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t_mkt)
    story.append(Spacer(1, 16))

    women_empowerment = [
        [
            Paragraph("<b>Target Persona: Mama Chioma (Provisions Trader, Tejuosho Market)</b>", card_header),
        ],
        [
            Paragraph(
                "• <b>Current Reality:</b> Sells ₦60,000/day. Keeps records in a soggy paper ledger. Owed ₦180,000 by customers. Bank rejected her ₦200,000 loan application for lacking audited statements.<br/>"
                "• <b>With Waasz AI:</b> She speaks voice notes in Pidgin. Waasz tracks customer debt and sends recovery notices. Within 60 days, Waasz generates a verified cash-flow score.<br/>"
                "• <b>The Transformation:</b> Zenith Bank automatically approves her for ₦150,000 inventory working capital directly inside WhatsApp. Her business turnover expands by 40%.",
                card_body
            )
        ]
    ]
    t_women = Table(women_empowerment, colWidths=[720])
    t_women.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#831843")), # Deep Pink/Burgundy
        ("PADDING", (0, 0), (-1, -1), 12),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    story.append(t_women)
    story.append(PageBreak())

    # ==========================================
    # SLIDE 7: TECHNICAL ARCHITECTURE
    # ==========================================
    add_header("6. Technical Architecture: Live, Battle-Tested Production Engine", "Fully deployed on AWS cloud with sub-cent marginal cost and high-throughput resilience.")

    arch_cards = [
        [
            Paragraph("<b>Channel Layer</b>", card_header),
            Paragraph("<b>AI Intelligence & Vision</b>", card_header),
            Paragraph("<b>Data & Infrastructure</b>", card_header),
        ],
        [
            Paragraph("• <b>Meta WhatsApp Cloud API v21.0</b><br/>• Webhook verification & idempotent queue<br/>• Real-time bilateral audio/photo streaming<br/>• Zero customer download requirement", card_body),
            Paragraph("• <b>Multi-Model Cost Router:</b> Gemini 1.5 Flash + Groq Llama-3 (sub-cent cost) with GPT-4o fallback<br/>• <b>Tesseract Optical Vision:</b> Automated receipt extraction<br/>• <b>Whisper AI:</b> Audio/Pidgin transcription", card_body),
            Paragraph("• <b>AWS EC2 Ubuntu (Systemd 24/7 persistence)</b><br/>• <b>PostgreSQL Database:</b> Double-entry ledgers with ACID compliance<br/>• <b>Streamlit Portal:</b> Argon2-secured founder telemetry dashboard", card_body),
        ],
    ]
    t_arch = Table(arch_cards, colWidths=[235, 235, 250])
    t_arch.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
        ("PADDING", (0, 0), (-1, -1), 12),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t_arch)
    story.append(Spacer(1, 16))

    prod_status_bar = [
        [
            Paragraph(
                "<b>Current Deployment Status:</b> Live Production on AWS (IP: 13.61.19.86) • Meta WhatsApp Cloud API Active • 100% End-to-End Automated Recovery with Systemd Daemons.",
                ParagraphStyle("ProdStatus", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=colors.HexColor("#10B981"))
            )
        ]
    ]
    t_prod = Table(prod_status_bar, colWidths=[720])
    t_prod.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#064E3B")),
        ("PADDING", (0, 0), (-1, -1), 9),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    story.append(t_prod)
    story.append(PageBreak())

    # ==========================================
    # SLIDE 8: IMPLEMENTATION ROADMAP
    # ==========================================
    add_header("7. Implementation Roadmap & Execution Plan", "A phased rollout designed to safely test, underwrite, and scale SME credit portfolios.")

    roadmap_data = [
        [
            Paragraph("<b>Phase 1: Working MVP (COMPLETED)</b>", card_header),
            Paragraph("<b>Phase 2: Hackathon Incubation (Month 1-3)</b>", card_header),
            Paragraph("<b>Phase 3: Zenith Pilot (Month 4-6)</b>", card_header),
            Paragraph("<b>Phase 4: Scale (Month 7-12)</b>", card_header),
        ],
        [
            Paragraph("• WhatsApp bot live<br/>• OCR receipt ingestion<br/>• Voice note processing<br/>• Double-entry ledger<br/>• Founder portal & audit<br/>• AWS 24/7 cloud deployment", card_body),
            Paragraph("• Onboard 250 market traders in Lagos<br/>• Build Trust / Credibility scoring model<br/>• Zenith Open Banking sandbox integration<br/>• Weekly PDF report dispatch", card_body),
            Paragraph("• Launch ₦50M Zenith Micro-Lending pilot<br/>• Disburse Tier-1 working capital (₦25k-₦50k)<br/>• Monitor 7-day repayment rates<br/>• Target 50%+ women-led merchants", card_body),
            Paragraph("• Expand to 50,000 active SME merchants<br/>• Integrate automated POS inflow sweeps<br/>• Nationwide rollout across Zenith branches<br/>• Expand into inventory financing", card_body),
        ],
    ]
    t_road = Table(roadmap_data, colWidths=[180, 180, 180, 180])
    t_road.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
        ("PADDING", (0, 0), (-1, -1), 12),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t_road)
    story.append(Spacer(1, 16))

    revenue_model = [
        [
            Paragraph(
                "<b>Commercial Revenue Model:</b> 1. <b>SaaS Subscription</b> (₦2,500/mo for advanced inventory & multi-staff) • 2. <b>Origination Fee</b> (1.5%–2.0% on loans originated for Zenith) • 3. <b>Interchange / Payment fees</b> on bill payment & settlements.",
                ParagraphStyle("RevModel", parent=styles["Normal"], fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#E2E8F0"))
            )
        ]
    ]
    t_rev = Table(revenue_model, colWidths=[720])
    t_rev.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
        ("PADDING", (0, 0), (-1, -1), 10),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    story.append(t_rev)
    story.append(PageBreak())

    # ==========================================
    # SLIDE 9: CONCLUSION & WHY WAASZ WINS
    # ==========================================
    add_header("8. Conclusion: Why Waasz AI & Zenith Bank Win Together", "Unlocking Africa's informal economy through friction-free AI and institutional banking rails.")

    conclusion_cards = [
        [
            Paragraph("<b>Unrivaled Adoption</b>", card_header),
            Paragraph("<b>De-Risked Banking</b>", card_header),
            Paragraph("<b>Scalable Impact</b>", card_header),
        ],
        [
            Paragraph("Zero training, zero app downloads. Merchants are already on WhatsApp 8 hours a day. Waasz meets them where they work.", card_body),
            Paragraph("Three-way triangulation of supplier expenses, real customer receipts, and statistical anomaly filters gives Zenith Bank audited confidence before lending.", card_body),
            Paragraph("Empowers millions of invisible female and micro-entrepreneurs to graduate into the formal economy while expanding Zenith Bank's asset and deposit base.", card_body),
        ],
    ]
    t_conc = Table(conclusion_cards, colWidths=[235, 235, 250])
    t_conc.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1E293B")),
        ("PADDING", (0, 0), (-1, -1), 14),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t_conc)
    story.append(Spacer(1, 24))

    call_to_action = [
        [
            Paragraph(
                "<b>Ready for Immediate Live Demonstration:</b><br/>"
                "Scan our QR code, send a voice note on WhatsApp right now, and watch Waasz construct a bank-verified financial record in real time.<br/><br/>"
                "<b>Contact:</b> founder@waasz.ai &nbsp;•&nbsp; <b>Live API:</b> https://waasz-sme-api.duckdns.org &nbsp;•&nbsp; <b>Zecathon 2026</b>",
                ParagraphStyle("CTA", parent=styles["Normal"], fontName="Helvetica", fontSize=10, leading=15, textColor=colors.HexColor("#FFFFFF"), alignment=1)
            )
        ]
    ]
    t_cta = Table(call_to_action, colWidths=[720])
    t_cta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E11D48")), # Zenith Red
        ("PADDING", (0, 0), (-1, -1), 14),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    story.append(t_cta)

    doc.build(story, canvasmaker=NumberedCanvas)
    print("Pitch deck successfully built at:", OUTPUT_PDF)


if __name__ == "__main__":
    build_deck()
