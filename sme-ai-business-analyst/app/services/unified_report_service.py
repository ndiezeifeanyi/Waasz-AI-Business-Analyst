from datetime import UTC, datetime, timedelta
import inspect
import logging
import re
from typing import Literal
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.activity import Activity
from app.models.business import Business
from app.models.goal import UserGoal
from app.models.member import Member
from app.models.user import User
from app.schemas.actor import ActorContext
from app.services.analytics_service import AI_ADVISORY_DISCLAIMER, AnalyticsService
from app.services.ledger_service import LedgerService
from app.services.magic_link_service import MagicLinkService
from app.services.visual_reports import VisualReportService
from app.services.whatsapp_client import WhatsAppClient

logger = logging.getLogger(__name__)

ReportCadence = Literal["daily", "weekly", "monthly", "quarterly", "yearly"]


def sanitize_whatsapp_clean_text(text: str) -> str:
    """
    Sanitize text for clean WhatsApp display:
    - Removes all asterisks (*) to eliminate unsightly bold markdown marks
    - Removes all double quotes (", “, ”, „, «, »)
    - Removes wrapping single quotes (', ‘, ’) around phrases or words
    - Preserves internal contractions like isn't, don't, I'm
    - Strips code backticks (`)
    """
    if not text:
        return text
    cleaned = text.replace("*", "")
    # Remove all unicode double quotes
    for q in ('"', '“', '”', '„', '«', '»'):
        cleaned = cleaned.replace(q, "")
    # Strip wrapping single quotes / curly quotes around words or expressions (run twice for adjacent pairs)
    for _ in range(2):
        cleaned = re.sub(r"(^|[\s\(\[\{•\-])['‘]([^\n'’]+)['’]([\s\)\]\}•\-\.,;:!?]|$)", r"\1\2\3", cleaned)
    cleaned = cleaned.replace("`", "")
    return cleaned.strip()


class UnifiedReportService:
    def __init__(
        self,
        whatsapp: WhatsAppClient | None = None,
        ledger: LedgerService | None = None,
        visual_reports: VisualReportService | None = None,
        magic_links: MagicLinkService | None = None,
    ) -> None:
        self.whatsapp = whatsapp or WhatsAppClient()
        self.ledger = ledger or LedgerService()
        self.visual_reports = visual_reports or VisualReportService()
        self.magic_links = magic_links or MagicLinkService()

    async def generate_and_deliver_report(
        self,
        db: AsyncSession,
        user_id: UUID,
        cadence: ReportCadence = "weekly",
        actor: ActorContext | None = None,
    ) -> dict:
        """
        Generate a multi-niche period report and deliver via WhatsApp,
        respecting the 24-hour customer care window. Reconnects visual trend charts
        for weekly, monthly, quarterly, and yearly cadences.
        For staff: delivers isolated personal performance report with improvement tips (no charts/magic links).
        For owners: delivers comprehensive business report including team sales breakdown and trend charts.
        """
        user = await db.get(User, user_id)
        if not user:
            return {"status": "skipped", "reason": "user_not_found"}

        # Resolve actor context if not provided
        if actor is None and user.business_id:
            member_stmt = select(Member).where(
                Member.business_id == user.business_id,
                Member.wa_id == user.phone_number,
                Member.status == "active",
            )
            m = (await db.execute(member_stmt)).scalars().first()
            if m:
                actor = ActorContext(
                    business_id=user.business_id,
                    member_id=m.id,
                    role=m.role,
                    wa_id=m.wa_id,
                    display_name=m.display_name or user.display_name,
                )
            else:
                actor = ActorContext(
                    business_id=user.business_id,
                    member_id=user.id,
                    role=user.role or "owner",
                    wa_id=user.phone_number,
                    display_name=user.display_name,
                )

        now = datetime.now(UTC)

        # ---------------------------------------------------------
        # Staff Persona: Isolated personal report & improvement tips
        # ---------------------------------------------------------
        if actor and actor.role == "staff":
            analytics_svc = AnalyticsService()
            period_str = "this_month" if cadence in {"monthly", "quarterly", "yearly"} else "this_week"
            staff_summary = await analytics_svc.get_staff_personal_summary(
                db,
                business_id=user.business_id,
                member_id=actor.member_id,
                member_name=actor.display_name or user.display_name or "Staff Member",
                period=period_str,
            )
            report_text = sanitize_whatsapp_clean_text(staff_summary["summary_text"])

            last_inbound = user.last_inbound_at or (now - timedelta(hours=48))
            if last_inbound.tzinfo is None:
                last_inbound = last_inbound.replace(tzinfo=UTC)
            is_within_24h = (now - last_inbound) <= timedelta(hours=24)

            if is_within_24h:
                result = await self.whatsapp.send_text(user.phone_number, report_text)
            else:
                template_components = [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": actor.display_name or user.display_name or "there"},
                            {"type": "text", "text": f"{cadence.capitalize()} Personal Summary"},
                            {"type": "text", "text": f"{staff_summary['total_count']} sales logged"},
                            {"type": "text", "text": f"{settings.app_base_url}/reports"},
                        ],
                    }
                ]
                result = await self.whatsapp.send_template(
                    user.phone_number,
                    template_name="personal_productivity_summary_v1",
                    language_code="en",
                    components=template_components,
                )

            if user.business_id:
                await self.ledger.record_outbound_message(
                    db, user.business_id, user.phone_number, report_text, result
                )

            if hasattr(db, "commit"):
                res_c = db.commit()
                if inspect.isawaitable(res_c):
                    await res_c

            return {"status": "sent", "report": report_text, "chart": None, "within_24h": is_within_24h}

        # ---------------------------------------------------------
        # Business Owner Persona: Business-wide + Team Breakdown
        # ---------------------------------------------------------
        cadence_days = {
            "daily": 1,
            "weekly": 7,
            "monthly": 30,
            "quarterly": 90,
            "yearly": 365,
        }
        days = cadence_days.get(cadence, 7)
        current_period_start = now - timedelta(days=days)
        prior_period_start = current_period_start - timedelta(days=days)

        # 1. Fetch current period activities
        stmt_curr = (
            select(Activity)
            .where(Activity.user_id == user_id, Activity.occurred_at >= current_period_start)
            .order_by(Activity.occurred_at.desc())
        )
        curr_acts = (await db.execute(stmt_curr)).scalars().all()

        # 2. Fetch prior period activities for comparison
        stmt_prior = (
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.occurred_at >= prior_period_start,
                Activity.occurred_at < current_period_start,
            )
            .order_by(Activity.occurred_at.desc())
        )
        prior_acts = (await db.execute(stmt_prior)).scalars().all()

        # 3. Fetch active user goals
        stmt_goals = (
            select(UserGoal)
            .where(UserGoal.user_id == user_id, UserGoal.status == "in_progress")
        )
        goals = (await db.execute(stmt_goals)).scalars().all()

        # Fetch business intelligence evidence (margins, 80/20, dead stock, and team performance)
        bi_evidence = ""
        if user.business_id:
            try:
                analytics_svc = AnalyticsService()
                period_str = "this_month" if cadence in {"monthly", "quarterly", "yearly"} else "this_week"
                margin_res = await analytics_svc.get_margin_report(db, user.business_id, period=period_str)
                perf_res = await analytics_svc.get_product_performance_analysis(db, user.business_id, period=period_str)
                team_res = await analytics_svc.get_team_sales_breakdown(db, user.business_id, period=period_str)
                parts = []
                if margin_res.get("summary_text"):
                    parts.append(margin_res["summary_text"])
                if perf_res.get("summary_text"):
                    parts.append(perf_res["summary_text"])
                if team_res.get("summary_text"):
                    parts.append(team_res["summary_text"])
                bi_evidence = "\n\n".join(parts)
            except Exception:
                pass

        # Build prompt
        report_text = await self._synthesize_report(
            user, cadence, curr_acts, prior_acts, goals, bi_evidence=bi_evidence
        )

        # 4. Check 24-hour customer care window
        last_inbound = user.last_inbound_at or (now - timedelta(hours=48))
        if last_inbound.tzinfo is None:
            last_inbound = last_inbound.replace(tzinfo=UTC)
        is_within_24h = (now - last_inbound) <= timedelta(hours=24)

        if is_within_24h:
            # Free-form session message allowed
            result = await self.whatsapp.send_text(user.phone_number, report_text)
        else:
            # Must use pre-approved Meta utility template outside 24h window
            template_name = (
                "weekly_business_report_v1"
                if user.niche == "sme_owner"
                else "personal_productivity_summary_v1"
            )
            logger.info("User %s is outside 24h window; sending %s template", user_id, template_name)
            template_components = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": user.display_name or "there"},
                        {"type": "text", "text": f"{cadence.capitalize()} Progress"},
                        {"type": "text", "text": f"{len(curr_acts)} activities logged"},
                        {"type": "text", "text": f"{settings.app_base_url}/reports"},
                    ],
                }
            ]
            result = await self.whatsapp.send_template(
                user.phone_number,
                template_name=template_name,
                language_code="en",
                components=template_components,
            )

        if user.business_id:
            await self.ledger.record_outbound_message(
                db, user.business_id, user.phone_number, report_text, result
            )

        # 5. Visual Chart Generation & Delivery (for monthly, quarterly, yearly reports only)
        chart_info = None
        if cadence in {"monthly", "quarterly", "yearly"} and user.business_id:
            try:
                chart_bytes = await self.visual_reports.generate_trend_chart(db, user.business_id, days=days)
                if chart_bytes:
                    media_id = await self.whatsapp.upload_media(chart_bytes, "image/png")
                    if media_id:
                        dashboard_url = ""
                        try:
                            token = await self.magic_links.create_link(db, user.business_id, expires_in_days=3)
                            base_url = settings.app_base_url.rstrip("/")
                            dashboard_url = f"{base_url}/reports/dashboard/{token}"
                        except Exception as ml_err:
                            logger.warning("Could not generate dashboard magic link: %s", ml_err)

                        caption = f"📊 {cadence.capitalize()} Financial Trend Chart ({days} Days)"
                        if dashboard_url:
                            caption += f"\n\n🌐 Live Interactive Dashboard:\n{dashboard_url}"

                        img_send_result = await self.whatsapp.send_image(
                            user.phone_number, media_id=media_id, caption=caption
                        )
                        await self.ledger.record_outbound_message(
                            db,
                            user.business_id,
                            user.phone_number,
                            caption,
                            img_send_result,
                            message_type="image",
                            user_id=user.id,
                            media_id=media_id,
                        )
                        chart_info = {"media_id": media_id, "caption": caption, "delivered": True, "dashboard_url": dashboard_url}
                        logger.info("Delivered %s report chart image to %s", cadence, user.phone_number)
            except Exception as chart_exc:
                logger.exception("Failed to deliver trend chart for %s report: %s", cadence, chart_exc)

        if hasattr(db, "commit"):
            res_c = db.commit()
            if inspect.isawaitable(res_c):
                await res_c

        return {"status": "sent", "report": report_text, "chart": chart_info, "within_24h": is_within_24h}

    async def deliver_financial_chart(
        self,
        db: AsyncSession,
        user_id: UUID,
        period: str = "monthly",
        actor: ActorContext | None = None,
    ) -> dict:
        """
        Generate and deliver an on-demand visual financial trend chart and interactive dashboard link
        directly to the user's WhatsApp chat.
        Restricted strictly to the business owner.
        """
        user = await db.get(User, user_id)
        if not user or not user.business_id:
            return {"status": "error", "message": "User or business record not found."}

        # Resolve actor context if not provided
        if actor is None:
            member_stmt = select(Member).where(
                Member.business_id == user.business_id,
                Member.wa_id == user.phone_number,
                Member.status == "active",
            )
            m = (await db.execute(member_stmt)).scalars().first()
            if m:
                actor = ActorContext(
                    business_id=user.business_id,
                    member_id=m.id,
                    role=m.role,
                    wa_id=m.wa_id,
                    display_name=m.display_name or user.display_name,
                )
            else:
                actor = ActorContext(
                    business_id=user.business_id,
                    member_id=user.id,
                    role=user.role or "owner",
                    wa_id=user.phone_number,
                    display_name=user.display_name,
                )

        if actor and actor.role == "staff":
            return {
                "status": "error",
                "message": "Business financial charts and interactive dashboards are restricted to the business owner.",
            }

        period_days = {
            "daily": 1,
            "weekly": 7,
            "monthly": 30,
            "quarterly": 90,
            "yearly": 365,
        }
        days = period_days.get(period.lower(), 30)

        chart_bytes = await self.visual_reports.generate_trend_chart(db, user.business_id, days=days)
        if not chart_bytes:
            return {"status": "error", "message": "No transaction data available to plot a trend chart."}

        media_id = await self.whatsapp.upload_media(chart_bytes, "image/png")
        if not media_id:
            return {"status": "error", "message": "Failed to upload chart image to WhatsApp."}

        dashboard_url = ""
        try:
            token = await self.magic_links.create_link(db, user.business_id, expires_in_days=3)
            base_url = settings.app_base_url.rstrip("/")
            dashboard_url = f"{base_url}/reports/dashboard/{token}"
        except Exception as ml_err:
            logger.warning("Could not generate dashboard magic link: %s", ml_err)

        caption = f"📊 {period.capitalize()} Financial Trend Chart ({days} Days)"
        if dashboard_url:
            caption += f"\n\n🌐 Live Interactive Dashboard:\n{dashboard_url}"

        img_send_result = await self.whatsapp.send_image(
            user.phone_number, media_id=media_id, caption=caption
        )
        await self.ledger.record_outbound_message(
            db,
            user.business_id,
            user.phone_number,
            caption,
            img_send_result,
            message_type="image",
            user_id=user.id,
            media_id=media_id,
        )
        if hasattr(db, "commit"):
            res_c = db.commit()
            if inspect.isawaitable(res_c):
                await res_c

        return {
            "status": "sent",
            "media_id": media_id,
            "dashboard_url": dashboard_url,
            "caption": caption,
        }

    async def dispatch_scheduled_reports(
        self,
        db: AsyncSession,
        cadence: ReportCadence = "weekly",
    ) -> int:
        """
        Scheduled job: Iterates over all active users and delivers their personalized reports.
        """
        users_res = await db.execute(
            select(User).where(User.is_active == True, User.deleted_at.is_(None))
        )
        users = users_res.scalars().all()
        sent_count = 0
        for u in users:
            try:
                res = await self.generate_and_deliver_report(db, u.id, cadence=cadence)
                if res.get("status") == "sent":
                    sent_count += 1
            except Exception as exc:
                logger.error("Failed to generate %s report for user %s: %s", cadence, u.id, exc)
        return sent_count

    async def _synthesize_report(
        self,
        user: User,
        cadence: str,
        curr_acts: list[Activity],
        prior_acts: list[Activity],
        goals: list[UserGoal],
        bi_evidence: str = "",
    ) -> str:
        niche = user.niche or "personal"
        cadence_title = cadence.capitalize()

        curr_summary = "\n".join(f"- {a.title} ({a.activity_type})" for a in curr_acts[:10]) or "No logged activities."
        prior_count = len(prior_acts)
        curr_count = len(curr_acts)

        goal_summary = ""
        if goals:
            goal_summary = "\n".join(
                f"- {g.title}: {g.current_value} / {g.target_value or 'target'} (Target Date: {g.target_date or 'Ongoing'})"
                for g in goals
            )
        else:
            goal_summary = "No active goals set."

        bi_block = ""
        playbook_instruction = ""
        if bi_evidence:
            bi_block = f"\nREAL BUSINESS METRICS & INVENTORY DATA (GROUNDING SOURCE):\n{bi_evidence}\n"
            if cadence in {"monthly", "quarterly", "yearly"} or user.niche == "sme_owner":
                playbook_instruction = (
                    "5. 🚀 *Personalized Growth Playbook* (Actionable business growth suggestions)\n"
                )

        prompt = f"""
You are an executive AI assistant preparing a {cadence_title} Report for a user whose profile is '{niche}'.

CURRENT PERIOD LOGGED ACTIVITIES ({curr_count} total):
{curr_summary}

PRIOR PERIOD ACTIVITY COUNT:
{prior_count} activities logged.

ACTIVE GOALS & TARGETS:
{goal_summary}
{bi_block}
CRITICAL GROUNDING RULES:
- Only reference activities, numbers, milestones, and metrics that appear in the logged records above.
- If zero activities were logged for this period, state clearly that no activities were recorded, and provide helpful advice without inventing fabricated achievements or metrics.
- Never hallucinate transactions, dates, or numbers not present in the data above.
- GROWTH PLAYBOOK GROUNDING DISCIPLINE: Every single suggestion in the Personalized Growth Playbook section MUST explicitly cite the specific real metric from the data above that motivated it (e.g. 'Since Rice makes up 60% of your sales at a 28.9% margin, consider...', or 'Your ₦144,000 tied up in unsold Fertilizer could be freed up by bundling...').
- If a Personalized Growth Playbook is included, conclude that section with this exact note: '{AI_ADVISORY_DISCLAIMER}'.
- ABSOLUTELY FORBIDDEN: Generic startup-advice filler, MBA platitudes, or recommendations without citing the real underlying number.

Generate a structured, inspiring report formatted cleanly for WhatsApp:
1. 📊 {cadence_title} Highlights (What was achieved)
2. 📈 Comparison vs Prior Period (Growth, velocity, or changes)
3. 💡 Concrete Suggestions (Specific to their '{niche}' role)
4. 🗺️ Goal Roadmap & Next Steps (Action items for the upcoming period)
{playbook_instruction}
WHATSAPP FORMATTING & CLEANLINESS RULES:
- STRICT NO-ASTERISK DIRECTIVE: Do NOT use asterisks (*) anywhere in your text. Do NOT bold words, numbers, percentages, or section titles with asterisks. Never output asterisks like *Rice* or *20%*.
- STRICT NO-QUOTATION-MARK DIRECTIVE: Do NOT put quotation marks (' or ") around item names, numbers, suggestions, or terms.
- Use plain, clean text with emojis and bullet points (e.g. "• Rice - ₦570,000 (69.5% of sales)").
- NEVER use markdown headers (#, ##, ###) or nested formatting.
- Leave clean blank lines between sections.
- When listing items, use a simple line-per-item with a single leading bullet "• ".

Keep tone motivating, professional, and clear.
"""
        def _finalize_report_text(text: str) -> str:
            if "Growth Playbook" in text and AI_ADVISORY_DISCLAIMER not in text:
                text = f"{text}\n\n{AI_ADVISORY_DISCLAIMER}"
            return sanitize_whatsapp_clean_text(str(text).strip())

        # Query Provider Chain: Gemini -> Groq -> OpenAI with live model resolution and self-healing
        from app.core.model_resolver import is_model_not_found_error, model_resolver

        if settings.gemini_api_key and settings.gemini_api_key != "placeholder_gemini_key":
            gemini_model = model_resolver.get_model("gemini", "chat")
            try:
                llm = ChatGoogleGenerativeAI(
                    model=gemini_model,
                    google_api_key=settings.gemini_api_key,
                    temperature=0.3,
                    max_retries=1,
                )
                res = await llm.ainvoke([HumanMessage(content=prompt)])
                return _finalize_report_text(res.content)
            except Exception as exc:
                if is_model_not_found_error(exc):
                    did_heal, new_model = await model_resolver.handle_mid_run_failure("gemini", "chat", exc)
                    if did_heal:
                        try:
                            llm = ChatGoogleGenerativeAI(
                                model=new_model,
                                google_api_key=settings.gemini_api_key,
                                temperature=0.3,
                                max_retries=1,
                            )
                            res = await llm.ainvoke([HumanMessage(content=prompt)])
                            return _finalize_report_text(res.content)
                        except Exception as retry_exc:
                            exc = retry_exc
                logger.warning("Gemini report generation failed: %s. Trying Groq fallback.", exc)

        if settings.groq_api_key and settings.groq_api_key != "placeholder_groq_key":
            groq_model = model_resolver.get_model("groq", "chat")
            try:
                from langchain_groq import ChatGroq

                groq_llm = ChatGroq(
                    model=groq_model,
                    api_key=settings.groq_api_key,
                    temperature=0.3,
                    max_retries=1,
                )
                res = await groq_llm.ainvoke([HumanMessage(content=prompt)])
                return _finalize_report_text(res.content)
            except Exception as exc:
                if is_model_not_found_error(exc):
                    did_heal, new_model = await model_resolver.handle_mid_run_failure("groq", "chat", exc)
                    if did_heal:
                        try:
                            groq_llm = ChatGroq(
                                model=new_model,
                                api_key=settings.groq_api_key,
                                temperature=0.3,
                                max_retries=1,
                            )
                            res = await groq_llm.ainvoke([HumanMessage(content=prompt)])
                            return _finalize_report_text(res.content)
                        except Exception as retry_exc:
                            exc = retry_exc
                logger.warning("Groq report generation failed: %s. Trying OpenAI fallback.", exc)

        if settings.openai_api_key and settings.openai_api_key != "placeholder_openai_key":
            openai_model = model_resolver.get_model("openai", "chat")
            try:
                from langchain_openai import ChatOpenAI

                openai_llm = ChatOpenAI(
                    model=openai_model,
                    api_key=settings.openai_api_key,
                    temperature=0.3,
                    max_retries=1,
                )
                res = await openai_llm.ainvoke([HumanMessage(content=prompt)])
                return _finalize_report_text(res.content)
            except Exception as exc:
                if is_model_not_found_error(exc):
                    did_heal, new_model = await model_resolver.handle_mid_run_failure("openai", "chat", exc)
                    if did_heal:
                        try:
                            openai_llm = ChatOpenAI(
                                model=new_model,
                                api_key=settings.openai_api_key,
                                temperature=0.3,
                                max_retries=1,
                            )
                            res = await openai_llm.ainvoke([HumanMessage(content=prompt)])
                            return _finalize_report_text(res.content)
                        except Exception as retry_exc:
                            exc = retry_exc
                logger.warning("OpenAI report generation failed: %s.", exc)

        # Honest grounded fallback based strictly on facts in database
        if curr_count == 0:
            return _finalize_report_text(
                f"📊 {cadence_title} Progress Summary\n\n"
                f"• Activities Logged: 0\n"
                f"• Active Goals: {len(goals)} in progress\n\n"
                f"(No activities were recorded for this period. Start logging your milestones today!)"
            )

        return _finalize_report_text(
            f"📊 {cadence_title} Progress Summary\n\n"
            f"• Activities Logged: {curr_count} (vs {prior_count} in prior period)\n"
            f"• Active Goals: {len(goals)} in progress\n\n"
            f"💡 Suggestion: Keep logging your daily milestones so we can map your roadmap accurately!\n"
            f"(Note: Detailed AI narrative is temporarily degraded; raw metrics are grounded from your database records.)"
        )
