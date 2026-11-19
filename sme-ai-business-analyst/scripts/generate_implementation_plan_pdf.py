import os
import sys
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    HRFlowable,
)
from reportlab.pdfgen import canvas

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PDF = BASE_DIR / "docs" / "Waasz_Voice_Calling_and_Credit_Scoring_Implementation_Plan.pdf"

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
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setStrokeColor(colors.HexColor("#0F766E"))
        self.setLineWidth(2)
        self.line(40, 842 - 30, 555, 842 - 30)

        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#0F766E"))
        self.drawString(40, 842 - 25, "WAASZ AI // TECHNICAL IMPLEMENTATION PLAN")

        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.6)
        self.line(40, 35, 555, 35)

        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawString(40, 24, "Confidential & Proprietary — Waasz Engineering Leadership")
        self.drawRightString(555, 24, f"Page {self._pageNumber} of {page_count}")
        self.restoreState()

def build_pdf():
    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=45,
        bottomMargin=45,
    )

    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        "CoverTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=6,
    )
    
    subtitle_style = ParagraphStyle(
        "CoverSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=15,
        textColor=colors.HexColor("#0F766E"),
        spaceAfter=14,
    )
    
    h1_style = ParagraphStyle(
        "Head1",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=12,
        spaceAfter=6,
    )

    h2_style = ParagraphStyle(
        "Head2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#0F766E"),
        spaceBefore=8,
        spaceAfter=4,
    )

    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#334155"),
        spaceAfter=5,
    )

    bullet_style = ParagraphStyle(
        "Bullet",
        parent=body_style,
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=3,
    )

    table_header = ParagraphStyle(
        "TH",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
    )

    table_cell = ParagraphStyle(
        "TC",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
    )

    story = []

    # Title & Header
    story.append(Paragraph("Waasz AI: Autonomous Voice Recovery & Alternative Credit Scoring Engine", title_style))
    story.append(Paragraph("Technical Architecture, Model Benchmarking, Fraud-Prevention & Step-by-Step Implementation Plan", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=10))

    # Meta Info Card
    meta_data = [
        [
            Paragraph("<b>Author:</b> Ndibueze Ifeanyi", body_style),
            Paragraph("<b>Version:</b> 1.0.0 (Production Blueprint)", body_style),
            Paragraph("<b>Status:</b> Approved for Execution", body_style),
        ]
    ]
    t_meta = Table(meta_data, colWidths=[170, 170, 175])
    t_meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 10))

    # Section 1
    story.append(Paragraph("1. Executive Overview & Strategic Objective", h1_style))
    story.append(Paragraph(
        "Waasz is upgrading from a pure messaging financial OS into an active recovery and underwriting engine. "
        "This plan addresses two mission-critical capabilities: (1) An Autonomous GSM Voice Agent to call debtors and follow up on customer inquiries over local Nigerian telecom networks, and (2) The formalized 4-Factor Alternative Credit Scoring Engine (300 to 850 scale) with anti-fraud transaction quarantine for bank loan pre-qualification.",
        body_style
    ))

    # Section 2
    story.append(Paragraph("2. Part I: Autonomous GSM Voice Recovery Architecture", h1_style))
    story.append(Paragraph("<b>2.1 Latest Model Benchmarking & Platform Stack (Nov 2026):</b>", h2_style))

    bench_data = [
        [Paragraph("Pipeline Stage", table_header), Paragraph("Recommended Platform", table_header), Paragraph("Fallback", table_header), Paragraph("Latency", table_header), Paragraph("Role in Nigeria", table_header)],
        [Paragraph("Telephony Gateway", table_cell), Paragraph("Termii Voice API / Africa's Talking", table_cell), Paragraph("Twilio Voice", table_cell), Paragraph("< 800ms", table_cell), Paragraph("Direct telco interconnects; NGN billing; rotating 080/090 caller IDs.", table_cell)],
        [Paragraph("Voice Orchestration", table_cell), Paragraph("LiveKit Agents (Self-hosted)", table_cell), Paragraph("Vapi AI", table_cell), Paragraph("< 400ms", table_cell), Paragraph("WebRTC-to-SIP bridge; deterministic state management; VAD control.", table_cell)],
        [Paragraph("Speech-to-Text (STT)", table_cell), Paragraph("Deepgram Nova-2 (Multi)", table_cell), Paragraph("Whisper v3 Turbo", table_cell), Paragraph("~250ms", table_cell), Paragraph("Market background noise resistance; Nigerian accent accuracy.", table_cell)],
        [Paragraph("Reasoning Engine", table_cell), Paragraph("Groq Llama-3.3 70B", table_cell), Paragraph("GPT-4o-mini", table_cell), Paragraph("~180ms", table_cell), Paragraph("Sub-second turn latency; cost under $0.0005/call; zero hallucination.", table_cell)],
        [Paragraph("Text-to-Speech (TTS)", table_cell), Paragraph("Cartesia Sonic / ElevenLabs African", table_cell), Paragraph("Meta MMS (Native)", table_cell), Paragraph("~150ms", table_cell), Paragraph("Warm, natural African English/Pidgin intonation; native local greetings.", table_cell)],
    ]
    t_bench = Table(bench_data, colWidths=[90, 115, 80, 50, 180])
    t_bench.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("PADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]))
    story.append(t_bench)
    story.append(Spacer(1, 8))

    story.append(Paragraph("<b>2.2 Zero-Hallucination & Circuit Breaker Architecture:</b>", h2_style))
    story.append(Paragraph("• <b>Deterministic Slot-Filling:</b> The AI voice agent is never given free reign over figures. Names, balances, and dates are hard-locked from PostgreSQL.", bullet_style))
    story.append(Paragraph("• <b>Pre-Flight Verification:</b> 1 second before dialing, the server verifies `debt.status == 'pending'`. If the merchant marked it paid 5 seconds ago, the call is instantly aborted.", bullet_style))
    story.append(Paragraph("• <b>Merchant Opt-in:</b> Waasz prompts the merchant via WhatsApp: `[📞 Approve Call]` / `[❌ Do Not Call]`. No customer is called without permission.", bullet_style))
    story.append(Paragraph("• <b>Voice Activity Detection (VAD) & Fallback:</b> If 4s of silence occurs, the AI gracefully exits (`'I've just texted you the details'`) and dispatches a 1-tap WhatsApp and SMS payment link.", bullet_style))

    # Section 3
    story.append(Paragraph("3. Part II: Next-Gen Alternative Credit Scoring Engine (300 - 850)", h1_style))
    story.append(Paragraph(
        "<b>Mathematical Formula:</b> <code>Waasz Score = 300 + 550 × [0.35·V + 0.25·S + 0.25·R + 0.15·E]</code>",
        body_style
    ))

    score_factors = [
        [Paragraph("Factor", table_header), Paragraph("Weight", table_header), Paragraph("Core Metrics Measured", table_header), Paragraph("Underwriting Value", table_header)],
        [Paragraph("Transaction Velocity (V)", table_cell), Paragraph("35% (192.5 pts)", table_cell), Paragraph("Active trading days/month, month-over-month turnover stability, seasonal resilience.", table_cell), Paragraph("Proves daily customer demand and business viability.", table_cell)],
        [Paragraph("Cash Flow Stability (S)", table_cell), Paragraph("25% (137.5 pts)", table_cell), Paragraph("Cash-to-credit sales ratio, gross operating margins, liquidity volatility.", table_cell), Paragraph("Ensures business generates real profit rather than unprofitable volume.", table_cell)],
        [Paragraph("Debt Recovery Discipline (R)", table_cell), Paragraph("25% (137.5 pts)", table_cell), Paragraph("Percentage of credit recovered on time, turnaround velocity, default frequency.", table_cell), Paragraph("Demonstrates merchant's ability to enforce capital repayment.", table_cell)],
        [Paragraph("Inventory & Audit Health (E)", table_cell), Paragraph("15% (82.5 pts)", table_cell), Paragraph("Restock expense capture frequency, clean verification ratio, dead stock index.", table_cell), Paragraph("Measures record-keeping completeness and lack of bookkeeping fraud.", table_cell)],
    ]
    t_score = Table(score_factors, colWidths=[120, 75, 170, 150])
    t_score.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("PADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]))
    story.append(t_score)
    story.append(Spacer(1, 8))

    story.append(Paragraph("<b>3.2 Anti-Fraud Quarantine & Anomaly Detection:</b>", h2_style))
    story.append(Paragraph("• <b>Volume Spike Filter:</b> Transactions > 3.5x the 30-day moving average without receipt OCR are quarantined and excluded from credit scores.", bullet_style))
    story.append(Paragraph("• <b>Wash-Trading Shield:</b> Transactions between merchant staff and owner phone numbers cannot register as credit sales.", bullet_style))
    story.append(Paragraph("• <b>Cryptographic Ledger Hashes:</b> Daily transaction logs are sealed with SHA-256 hashes to prove authentic calendar streaks to lenders.", bullet_style))

    # Section 4
    story.append(Paragraph("4. Implementation Roadmap & Execution Milestones", h1_style))
    
    milestone_data = [
        [Paragraph("Phase", table_header), Paragraph("Timeline", table_header), Paragraph("Key Deliverables", table_header)],
        [Paragraph("Phase 1: Telephony & LiveKit MVP", table_cell), Paragraph("Weeks 1 - 2", table_cell), Paragraph("Termii SIP trunk integration, pre-flight circuit breakers, in-chat `/call` WhatsApp trigger, deterministic call script.", table_cell)],
        [Paragraph("Phase 2: Dialect Engine & VAD", table_cell), Paragraph("Weeks 3 - 4", table_cell), Paragraph("Nigerian Pidgin & Yoruba audio greetings, rotating 080/090 caller IDs, smart VAD silence fallback with SMS transfer link.", table_cell)],
        [Paragraph("Phase 3: Hardened Credit Engine", table_cell), Paragraph("Weeks 5 - 6", table_cell), Paragraph("4-Factor scoring algorithm deployment, anti-fraud quarantine pipeline, updated QR-verified 2-Page Health Profile PDF.", table_cell)],
        [Paragraph("Phase 4: Bank Underwriting Sandbox", table_cell), Paragraph("Weeks 7 - 8", table_cell), Paragraph("Lender pre-qualification API endpoints, SMEDAN/MFB sandbox testing, and automated credit decisioning.", table_cell)],
    ]
    t_mile = Table(milestone_data, colWidths=[120, 75, 320])
    t_mile.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("PADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
    ]))
    story.append(t_mile)

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Generated PDF: {OUTPUT_PDF}")

if __name__ == "__main__":
    build_pdf()
