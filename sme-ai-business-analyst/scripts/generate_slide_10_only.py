import os
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.pdfgen import canvas

PAGE_WIDTH, PAGE_HEIGHT = landscape(letter)
OUTPUT_PDF = os.path.abspath("Waasz_AI_Slide_10_Roadmap.pdf")

class Slide10Canvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def draw_decorations(self):
        self.saveState()
        # Top banner bar
        self.setFillColor(colors.HexColor("#0D9488"))
        self.rect(0, PAGE_HEIGHT - 6, PAGE_WIDTH, 6, fill=1, stroke=0)
        # Running header
        self.setFont("Helvetica-Bold", 7.5)
        self.setFillColor(colors.HexColor("#0D9488"))
        self.drawString(36, PAGE_HEIGHT - 22, "WAASZ AI")
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawString(82, PAGE_HEIGHT - 22, "|  The WhatsApp-Native Financial OS & Credit Engine for MSMEs")
        # Slide number
        self.setFont("Helvetica-Bold", 7.5)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawRightString(PAGE_WIDTH - 36, PAGE_HEIGHT - 22, "SLIDE 10 OF 10")
        # Top dividing line
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(36, PAGE_HEIGHT - 28, PAGE_WIDTH - 36, PAGE_HEIGHT - 28)
        # Bottom footer line
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(36, 26, PAGE_WIDTH - 36, 26)
        # Bottom metadata
        self.setFont("Helvetica", 7)
        self.setFillColor(colors.HexColor("#94A3B8"))
        self.drawString(36, 14, "Waasz AI Executive Pitch Deck  •  Confidential  •  AWS EC2 Production Live")
        self.drawRightString(PAGE_WIDTH - 36, 14, "founder@waasz.ai  •  Page 10")
        self.restoreState()

    def showPage(self):
        self.draw_decorations()
        super().showPage()

def build_slide_10():
    doc = SimpleDocTemplate(
        OUTPUT_PDF,
        pagesize=landscape(letter),
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=32,
    )
    styles = getSampleStyleSheet()

    cat_style = ParagraphStyle("Cat", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=colors.HexColor("#0D9488"))
    title_style = ParagraphStyle("Title", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=colors.HexColor("#0F172A"))
    sub_style = ParagraphStyle("Sub", parent=styles["Normal"], fontName="Helvetica", fontSize=9, leading=13, textColor=colors.HexColor("#64748B"))
    card_title = ParagraphStyle("CardTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=colors.HexColor("#0F172A"))
    card_body = ParagraphStyle("CardBody", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, leading=12.5, textColor=colors.HexColor("#334155"))

    story = []
    story.append(Paragraph("09 / THE ASK & ROADMAP", cat_style))
    story.append(Spacer(1, 3))
    story.append(Paragraph("Seeking $100,000 Pre-Seed Funding & Institutional Pilot Partners", title_style))
    story.append(Spacer(1, 3))
    story.append(Paragraph("Deploying capital into merchant cluster acquisition, lender API integrations, and product expansion.", sub_style))
    story.append(Spacer(1, 10))

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

    cta_line = Paragraph(
        "<b>Ready for Immediate Live Demonstration:</b> Test the WhatsApp bot live with voice notes right now &nbsp;•&nbsp; <b>Email:</b> founder@waasz.ai &nbsp;•&nbsp; <b>AWS Production Live</b>",
        ParagraphStyle("CTAFooter", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.HexColor("#0F766E"), alignment=1),
    )
    story.append(cta_line)

    doc.build(story, canvasmaker=Slide10Canvas)
    print("Standalone Slide 10 generated successfully at:", OUTPUT_PDF)

if __name__ == "__main__":
    build_slide_10()
