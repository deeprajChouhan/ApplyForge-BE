"""
System email templates (code defaults).

Every template is structured copy, not raw HTML: subject / preheader / heading /
body (markdown-lite + Jinja vars) / CTA / blocks. Admin edits are stored in
ops_email_templates under the same key and win over these defaults; admins can
also create new (non-system) templates for their own automations.

category:
  transactional — always sent (security, billing, invites). No unsubscribe.
  notification  — on by default; "Notifications" toggle in settings.
  digest        — on by default; "Digests" toggle in settings.
  lifecycle     — soft opt-in; "Tips & product updates" toggle; unsubscribe link.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CATEGORIES = ("transactional", "notification", "digest", "lifecycle")
PRODUCTS = ("consumer", "recruiter", "external", "ops")


@dataclass(frozen=True)
class TemplateDef:
    key: str
    product: str
    category: str
    name: str
    description: str
    subject: str
    body: str
    heading: str | None = None
    preheader: str | None = None
    cta_label: str | None = None
    cta_url: str | None = None
    blocks: tuple[str, ...] = ()
    variables: tuple[str, ...] = ()
    sample: dict[str, Any] = field(default_factory=dict)
    is_system: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["blocks"] = list(self.blocks)
        d["variables"] = list(self.variables)
        return d


_JOBS = [
    {"title": "Senior Backend Engineer", "company": "Monzo", "location": "London", "score": 86, "tier": "strong", "url": "/applications"},
    {"title": "Platform Engineer", "company": "Wise", "location": "Remote (UK)", "score": 81, "tier": "strong", "url": "/applications"},
]
_CANDS = [
    {"name": "Ada Lovelace", "headline": "Staff Engineer · 12 yrs", "match": 97, "reasons": ["Python + FastAPI, 6 yrs", "Led a team of 8", "Fintech domain"], "gaps": ["No Kubernetes in CV"], "url": "/pool"},
    {"name": "Alan Turing", "headline": "Senior Engineer · 9 yrs", "match": 91, "reasons": ["Distributed systems", "Python"], "gaps": ["Notice period 3 months"], "url": "/pool"},
]

T: list[TemplateDef] = [
    # ── Candidate (ApplyForgePro) ───────────────────────────────────────
    TemplateDef(
        key="c_welcome", product="consumer", category="transactional", name="Welcome",
        description="Sent once when an account is created (email or Google).",
        subject="Welcome to ApplyForge, {{ first_name or 'there' }}",
        preheader="Three steps to your first tailored application.",
        heading="You're in.",
        body=(
            "Hi {{ first_name or 'there' }},\n\n"
            "ApplyForge scores every job you look at and writes applications grounded in your own resume — nothing invented.\n\n"
            "- **Upload your resume** so we can ground every document in it.\n"
            "- **Install the Chrome extension** to see a Priority Score on LinkedIn, Indeed and Glassdoor.\n"
            "- **Save a role** and get a tailored resume, cover letter and cold email in one go."
        ),
        cta_label="Upload your resume", cta_url="{{ app_url }}/resume",
        variables=("first_name", "app_url"), sample={"first_name": "Deepraj"},
    ),
    TemplateDef(
        key="c_password_reset", product="consumer", category="transactional", name="Password reset",
        description="Forgot-password link. Expires in 60 minutes, single use.",
        subject="Reset your ApplyForge password",
        heading="Reset your password",
        body=(
            "Someone (hopefully you) asked to reset the password for {{ email }}.\n\n"
            "The link works once and expires in 60 minutes. If you didn't ask for this, you can ignore this email — your password won't change."
        ),
        cta_label="Choose a new password", cta_url="{{ reset_url }}",
        variables=("email", "reset_url"), sample={"email": "you@example.com", "reset_url": "https://applyforge.co.uk/reset-password?token=sample"},
    ),
    TemplateDef(
        key="c_password_changed", product="consumer", category="transactional", name="Password changed",
        description="Security notice after a password change.",
        subject="Your ApplyForge password was changed",
        heading="Password changed",
        body="The password for {{ email }} was changed on {{ when }} (UTC).\n\nIf this wasn't you, reply to this email straight away and we'll lock the account.",
        variables=("email", "when"), sample={"email": "you@example.com", "when": "18 Nov 2026 12:04"},
    ),
    TemplateDef(
        key="c_support_reply", product="consumer", category="transactional", name="Support reply",
        description="An admin replied to a help-desk ticket.",
        subject="Re: {{ ticket_subject }} [#{{ ticket_id }}]",
        heading="We replied to your ticket",
        body="Hi {{ first_name or 'there' }},\n\nThere's a new reply on **{{ ticket_subject }}**:",
        blocks=("quote",),
        cta_label="View the conversation", cta_url="{{ app_url }}/support",
        variables=("first_name", "ticket_subject", "ticket_id", "quote"),
        sample={"ticket_subject": "Extension not showing scores", "ticket_id": 42, "quote": "Thanks for flagging — fixed in 1.4.2. Can you update and try again?"},
    ),
    TemplateDef(
        key="c_resume_parsed", product="consumer", category="notification", name="Resume parsed",
        description="Resume parse finished; shows parse confidence and anything to check.",
        subject="Your resume is ready{% if parse_confidence is not none and parse_confidence < 60 %} — a few things to check{% endif %}",
        heading="Resume parsed",
        body="We've read **{{ resume_name }}** and built your knowledge base from it. Every document we generate will be grounded in it.",
        blocks=("gaps",),
        cta_label="Review your resume", cta_url="{{ app_url }}/resume",
        variables=("resume_name", "parse_confidence", "unsupported_gaps"),
        sample={"resume_name": "Deepraj_CV.pdf", "parse_confidence": 78, "unsupported_gaps": ["Dates for 2019 role unclear"]},
    ),
    TemplateDef(
        key="c_docs_ready", product="consumer", category="notification", name="Documents ready",
        description="Tailored docs generated for an application. Surfaces unsupported_gaps.",
        subject="Your {{ role_title }} application pack is ready",
        heading="Application pack ready",
        body="Tailored resume, cover letter and cold email for **{{ role_title }}** at {{ company }} are ready to review.",
        blocks=("gaps",),
        cta_label="Review and send", cta_url="{{ app_url }}/applications/{{ application_id }}",
        variables=("role_title", "company", "application_id", "unsupported_gaps"),
        sample={"role_title": "Senior Backend Engineer", "company": "Monzo", "application_id": 12, "unsupported_gaps": ["\"Led a team of 10\" — resume says 6"]},
    ),
    TemplateDef(
        key="c_new_strong_matches", product="consumer", category="notification", name="New strong matches",
        description="Live discovery found Strong-tier roles.",
        subject="{{ jobs|length }} new strong match{{ '' if jobs|length == 1 else 'es' }} for you",
        heading="New roles worth your time",
        body="These scored **Strong** against your resume and preferences:",
        blocks=("job_cards",),
        cta_label="See all matches", cta_url="{{ app_url }}/jobs",
        variables=("jobs",), sample={"jobs": _JOBS},
    ),
    TemplateDef(
        key="c_follow_up_reminder", product="consumer", category="notification", name="Follow-up reminder",
        description="Applied 7+ days ago with no status change.",
        subject="Time to follow up with {{ company }}?",
        heading="A nudge on {{ role_title }}",
        body="You applied to **{{ role_title }}** at {{ company }} {{ days }} days ago and haven't logged a reply. A short follow-up often gets a response — we've drafted one from your application.",
        cta_label="Open the draft", cta_url="{{ app_url }}/applications/{{ application_id }}",
        variables=("role_title", "company", "days", "application_id"),
        sample={"role_title": "Platform Engineer", "company": "Wise", "days": 8, "application_id": 7},
    ),
    TemplateDef(
        key="c_interview_prep", product="consumer", category="notification", name="Interview prep pack",
        description="Application moved to Interview.",
        subject="Interview prep for {{ company }}",
        heading="You've got an interview",
        body="Nice work. Your prep pack for **{{ role_title }}** at {{ company }} pulls the key requirements from the JD and matches them to evidence in your resume.\n\nOpen the application chat to rehearse answers.",
        cta_label="Start prepping", cta_url="{{ app_url }}/applications/{{ application_id }}",
        variables=("role_title", "company", "application_id"),
        sample={"role_title": "Senior Backend Engineer", "company": "Monzo", "application_id": 12},
    ),
    TemplateDef(
        key="c_weekly_digest", product="consumer", category="digest", name="Weekly digest",
        description="Monday summary: pipeline, matches, follow-ups due.",
        subject="Your week: {{ stats[0].value if stats else 0 }} applications in play",
        heading="Your week in ApplyForge",
        body="Hi {{ first_name or 'there' }}, here's where things stand.",
        blocks=("stats", "job_cards"),
        cta_label="Open your board", cta_url="{{ app_url }}/applications",
        variables=("first_name", "stats", "jobs"),
        sample={"stats": [{"label": "In play", "value": 9}, {"label": "Interviews", "value": 2}, {"label": "Follow-ups due", "value": 3}], "jobs": _JOBS},
    ),
    TemplateDef(
        key="c_nudge_upload_resume", product="consumer", category="lifecycle", name="Nudge: upload resume",
        description="No resume 24h after sign-up.",
        subject="One step left: upload your resume",
        heading="Your resume powers everything",
        body="Scores, tailored resumes and cover letters are all grounded in your own resume — without it we can't generate anything truthful.\n\nIt takes about a minute: PDF or DOCX.",
        cta_label="Upload resume", cta_url="{{ app_url }}/resume",
    ),
    TemplateDef(
        key="c_nudge_extension", product="consumer", category="lifecycle", name="Nudge: install extension",
        description="Extension not connected 3 days after sign-up.",
        subject="See a Priority Score on every job you open",
        heading="Score jobs where you find them",
        body="The ApplyForge Chrome extension shows a 0–100 Priority Score on LinkedIn, Indeed and Glassdoor, so you spend time on the roles you can actually win.",
        cta_label="Get the extension", cta_url="{{ app_url }}/extension",
    ),
    TemplateDef(
        key="c_usage_threshold", product="consumer", category="transactional", name="Usage threshold",
        description="Monthly AI token budget crossed 80% / 100%.",
        subject="You've used {{ percent }}% of this month's AI budget",
        heading="{{ percent }}% of your monthly budget used",
        body="You've used {{ used }} of {{ budget }} tokens on the **{{ plan }}** plan this month. It resets on {{ reset_date }}.",
        cta_label="See plans", cta_url="{{ app_url }}/plans",
        variables=("percent", "used", "budget", "plan", "reset_date"),
        sample={"percent": 80, "used": "40,000", "budget": "50,000", "plan": "Free", "reset_date": "1 Dec"},
    ),
    TemplateDef(
        key="c_winback", product="consumer", category="lifecycle", name="Win-back",
        description="Inactive for 14+ days.",
        subject="New roles since you were last here",
        heading="Still looking?",
        body="It's been a while. We've kept scoring roles against your resume — open ApplyForge to see what's new.",
        cta_label="See what's new", cta_url="{{ app_url }}/jobs",
    ),
    # ── Recruiter (agency users) ─────────────────────────────────────────
    TemplateDef(
        key="r_welcome", product="recruiter", category="transactional", name="Agency welcome",
        description="Self-serve signup. Says whether the account is pending approval.",
        subject="Welcome to ApplyForge Recruiter",
        heading="Welcome, {{ first_name or 'there' }}",
        body=(
            "{% if pending %}Thanks for signing up **{{ agency_name }}**. We review new agencies before they go live — you'll get an email as soon as you're approved (usually within one working day).{% else %}"
            "**{{ agency_name }}** is set up. Three steps to your first ranked shortlist:\n\n"
            "- **Build your pool** — bulk-upload CVs or capture LinkedIn profiles with the extension.\n"
            "- **Define a role** — paste the spec.\n"
            "- **Run matching** — get a ranked shortlist with reasons and gaps for every candidate.{% endif %}"
        ),
        cta_label="{{ 'Open ApplyForge Recruiter' if not pending else '' }}", cta_url="{{ app_url }}/dashboard",
        variables=("first_name", "agency_name", "pending"), sample={"agency_name": "Northbridge Talent", "pending": False},
    ),
    TemplateDef(
        key="r_agency_approved", product="recruiter", category="transactional", name="Agency approved",
        description="Operator approved a pending agency.",
        subject="{{ agency_name }} is live on ApplyForge Recruiter",
        heading="You're approved",
        body="Good news — **{{ agency_name }}** has been approved. Sign in to build your pool and run your first match.",
        cta_label="Sign in", cta_url="{{ app_url }}/login",
        variables=("agency_name",), sample={"agency_name": "Northbridge Talent"},
    ),
    TemplateDef(
        key="r_agency_status", product="recruiter", category="transactional", name="Agency status changed",
        description="Operator suspended or reactivated an agency.",
        subject="Your ApplyForge Recruiter account has been {{ status_label }}",
        heading="Account {{ status_label }}",
        body="**{{ agency_name }}** has been {{ status_label }}.{% if reason %}\n\nReason: {{ reason }}{% endif %}\n\nQuestions? Just reply to this email.",
        variables=("agency_name", "status_label", "reason"), sample={"agency_name": "Northbridge Talent", "status_label": "reactivated"},
    ),
    TemplateDef(
        key="r_invite", product="recruiter", category="transactional", name="Seat invite",
        description="Owner invited someone to the agency.",
        subject="{{ inviter_name }} invited you to {{ agency_name }} on ApplyForge Recruiter",
        heading="Join {{ agency_name }}",
        body="{{ inviter_name }} has invited you to join **{{ agency_name }}** as {{ role_label }}. Accept to set your password and get started.\n\nThis invite expires on {{ expires }}.",
        cta_label="Accept invite", cta_url="{{ invite_url }}",
        variables=("inviter_name", "agency_name", "role_label", "invite_url", "expires"),
        sample={"inviter_name": "Sam Carter", "agency_name": "Northbridge Talent", "role_label": "a recruiter", "invite_url": "https://recruiter.applyforge.co.uk/invite/sample", "expires": "25 Nov 2026"},
    ),
    TemplateDef(
        key="r_invite_accepted", product="recruiter", category="notification", name="Invite accepted",
        description="Tells the owner a seat was claimed.",
        subject="{{ member_name }} joined {{ agency_name }}",
        heading="New teammate",
        body="**{{ member_name }}** ({{ member_email }}) accepted your invite. You're now using {{ seats_used }}{% if seat_limit %} of {{ seat_limit }}{% endif %} seats.",
        cta_label="Manage team", cta_url="{{ app_url }}/team",
        variables=("member_name", "member_email", "agency_name", "seats_used", "seat_limit"),
        sample={"member_name": "Jo Patel", "member_email": "jo@northbridge.co.uk", "agency_name": "Northbridge Talent", "seats_used": 4, "seat_limit": 5},
    ),
    TemplateDef(
        key="r_password_reset", product="recruiter", category="transactional", name="Password reset",
        description="Recruiter forgot-password link (60 min, single use).",
        subject="Reset your ApplyForge Recruiter password",
        heading="Reset your password",
        body="Someone asked to reset the password for {{ email }}. The link works once and expires in 60 minutes. If it wasn't you, ignore this email.",
        cta_label="Choose a new password", cta_url="{{ reset_url }}",
        variables=("email", "reset_url"), sample={"email": "sam@northbridge.co.uk", "reset_url": "https://recruiter.applyforge.co.uk/reset-password?token=sample"},
    ),
    TemplateDef(
        key="r_password_changed", product="recruiter", category="transactional", name="Password changed",
        description="Security notice after a recruiter password change.",
        subject="Your ApplyForge Recruiter password was changed",
        heading="Password changed",
        body="The password for {{ email }} was changed on {{ when }} (UTC). If this wasn't you, reply to this email straight away.",
        variables=("email", "when"), sample={"email": "sam@northbridge.co.uk", "when": "18 Nov 2026 12:04"},
    ),
    TemplateDef(
        key="r_notification", product="recruiter", category="notification", name="Pipeline notification",
        description="Email copy of an in-app notification (interviews, offers, feedback, new applicants…).",
        subject="{{ title }}",
        heading="{{ title }}",
        body="{{ body or '' }}",
        cta_label="Open in ApplyForge", cta_url="{{ link or app_url ~ '/dashboard' }}",
        variables=("title", "body", "link", "kind"),
        sample={"title": "Interview tomorrow: Ada Lovelace × Monzo", "body": "10:00–11:00, video. Feedback form opens after the slot.", "kind": "interview_upcoming"},
    ),
    TemplateDef(
        key="r_shortlist_ready", product="recruiter", category="notification", name="Shortlist ready",
        description="Matching finished for a role.",
        subject="Shortlist ready: {{ role_title }} ({{ candidates|length }} top matches)",
        heading="Your shortlist for {{ role_title }}",
        body="Ranked against the spec, with the evidence behind each score:",
        blocks=("candidate_cards",),
        cta_label="Open shortlist", cta_url="{{ app_url }}/roles/{{ role_id }}",
        variables=("role_title", "role_id", "candidates"), sample={"role_title": "Senior Backend Engineer", "role_id": "r_abc", "candidates": _CANDS},
    ),
    TemplateDef(
        key="r_daily_brief", product="recruiter", category="digest", name="Daily brief",
        description="07:30 UK — today's interviews, overdue feedback, tasks.",
        subject="Your day: {{ stats[0].value if stats else 0 }} interviews, {{ stats[1].value if stats|length > 1 else 0 }} overdue",
        heading="Good morning, {{ first_name or 'there' }}",
        body="Here's what needs you today.",
        blocks=("stats", "table"),
        cta_label="Open daily brief", cta_url="{{ app_url }}/daily-brief",
        variables=("first_name", "stats", "table"),
        sample={"stats": [{"label": "Interviews", "value": 3}, {"label": "Overdue feedback", "value": 2}, {"label": "Tasks due", "value": 5}],
                "table": {"columns": ["Time", "Candidate", "Client"], "rows": [["10:00", "Ada Lovelace", "Monzo"], ["14:30", "Alan Turing", "Wise"]]}},
    ),
    TemplateDef(
        key="r_owner_weekly", product="recruiter", category="digest", name="Owner weekly report",
        description="Monday report for agency owners.",
        subject="{{ agency_name }} — week in review",
        heading="Week in review",
        body="How **{{ agency_name }}** did last week:",
        blocks=("stats", "table"),
        cta_label="Open dashboard", cta_url="{{ app_url }}/dashboard",
        variables=("agency_name", "stats", "table"),
        sample={"agency_name": "Northbridge Talent", "stats": [{"label": "Placements", "value": 2}, {"label": "Submissions", "value": 14}, {"label": "Interviews", "value": 9}],
                "table": {"columns": ["Recruiter", "Submissions", "Interviews"], "rows": [["Sam", 6, 4], ["Jo", 8, 5]]}},
    ),
    TemplateDef(
        key="r_onboarding_nudge", product="recruiter", category="lifecycle", name="Onboarding nudge",
        description="Agency hasn't completed pool → role → match.",
        subject="{{ next_step_title }}",
        heading="{{ next_step_title }}",
        body="{{ next_step_body }}",
        cta_label="{{ next_step_cta }}", cta_url="{{ app_url }}{{ next_step_path }}",
        variables=("next_step_title", "next_step_body", "next_step_cta", "next_step_path"),
        sample={"next_step_title": "Add your first candidates", "next_step_body": "Bulk-upload CVs (PDF/DOCX) or capture LinkedIn profiles with the extension. Matching needs a pool to rank.", "next_step_cta": "Upload CVs", "next_step_path": "/pool"},
    ),
    TemplateDef(
        key="r_trial_ending", product="recruiter", category="transactional", name="Trial ending",
        description="Trial ends in N days.",
        subject="Your trial ends in {{ days_left }} day{{ '' if days_left == 1 else 's' }}",
        heading="Trial ends {{ trial_end }}",
        body="Your **{{ agency_name }}** trial ends on {{ trial_end }}. Pick a plan to keep your pool, roles and pipeline running without interruption.",
        cta_label="Choose a plan", cta_url="{{ app_url }}/team",
        variables=("agency_name", "days_left", "trial_end"), sample={"agency_name": "Northbridge Talent", "days_left": 3, "trial_end": "21 Nov 2026"},
    ),
    TemplateDef(
        key="r_payment_failed", product="recruiter", category="transactional", name="Payment failed",
        description="Stripe subscription went past_due.",
        subject="Payment failed for {{ agency_name }}",
        heading="We couldn't take your payment",
        body="Your latest payment for **{{ agency_name }}** didn't go through. Update your card to avoid any interruption.",
        cta_label="Update billing", cta_url="{{ app_url }}/team",
        variables=("agency_name",), sample={"agency_name": "Northbridge Talent"},
    ),
    TemplateDef(
        key="r_seat_limit", product="recruiter", category="transactional", name="Seat limit reached",
        description="Agency hit its seat cap.",
        subject="{{ agency_name }} has used all {{ seat_limit }} seats",
        heading="All seats in use",
        body="You're using {{ seat_limit }} of {{ seat_limit }} seats. Upgrade your plan to invite more of the team.",
        cta_label="Manage plan", cta_url="{{ app_url }}/team",
        variables=("agency_name", "seat_limit"), sample={"agency_name": "Northbridge Talent", "seat_limit": 5},
    ),
    # ── External (sent on an agency's behalf; agency-branded) ───────────
    TemplateDef(
        key="x_consent_request", product="external", category="transactional", name="Candidate consent request",
        description="GDPR consent to hold/submit a candidate's details.",
        subject="{{ agency_name }} would like your permission to represent you",
        heading="Can we put you forward?",
        body="Hi {{ candidate_first_name or 'there' }},\n\n{{ recruiter_name }} at **{{ agency_name }}** would like your consent to keep your details on file and put you forward for suitable roles{% if role_title %}, starting with **{{ role_title }}**{% endif %}.\n\nYou can withdraw consent at any time by replying to this email.",
        cta_label="Review and consent", cta_url="{{ consent_url }}",
        variables=("candidate_first_name", "recruiter_name", "agency_name", "role_title", "consent_url"),
        sample={"candidate_first_name": "Ada", "recruiter_name": "Sam Carter", "agency_name": "Northbridge Talent", "role_title": "Senior Backend Engineer", "consent_url": "https://recruiter.applyforge.co.uk/public/consent/sample"},
    ),
    TemplateDef(
        key="x_client_share", product="external", category="transactional", name="Client shortlist share",
        description="Recruiter shares a role/shortlist link with a client.",
        subject="{{ agency_name }}: candidates for {{ role_title }}",
        heading="Candidates for {{ role_title }}",
        body="Hi {{ client_contact or 'there' }},\n\n{{ recruiter_name }} has shared {{ count }} candidate{{ '' if count == 1 else 's' }} for **{{ role_title }}**. Each profile shows why they fit and where the gaps are.",
        cta_label="View candidates", cta_url="{{ share_url }}",
        variables=("client_contact", "recruiter_name", "agency_name", "role_title", "count", "share_url"),
        sample={"client_contact": "Priya", "recruiter_name": "Sam Carter", "agency_name": "Northbridge Talent", "role_title": "Senior Backend Engineer", "count": 3, "share_url": "https://recruiter.applyforge.co.uk/share/sample"},
    ),
    TemplateDef(
        key="x_interview_confirm", product="external", category="transactional", name="Interview confirmation",
        description="Confirms an interview slot to the candidate.",
        subject="Interview confirmed: {{ company }}, {{ when }}",
        heading="You're booked in",
        body="Hi {{ candidate_first_name or 'there' }},\n\nYour interview for **{{ role_title }}** at {{ company }} is confirmed for **{{ when }}**{% if location %} ({{ location }}){% endif %}.\n\nAny questions, reply to this email and {{ recruiter_name }} will get back to you.",
        variables=("candidate_first_name", "role_title", "company", "when", "location", "recruiter_name"),
        sample={"candidate_first_name": "Ada", "role_title": "Senior Backend Engineer", "company": "Monzo", "when": "Thu 20 Nov, 10:00", "location": "Video call", "recruiter_name": "Sam"},
    ),
    TemplateDef(
        key="x_application_ack", product="external", category="transactional", name="Application received",
        description="Auto-acknowledgement for applicants via job listing / Partner API.",
        subject="We've received your application for {{ role_title }}",
        heading="Thanks for applying",
        body="Hi {{ candidate_first_name or 'there' }},\n\nThanks for applying for **{{ role_title }}** with {{ agency_name }}. A recruiter reviews every application; if it's a fit, we'll be in touch within {{ sla_days or 5 }} working days.",
        variables=("candidate_first_name", "role_title", "agency_name", "sla_days"),
        sample={"candidate_first_name": "Ada", "role_title": "Senior Backend Engineer", "agency_name": "Northbridge Talent"},
    ),
    # ── Operator / internal ──────────────────────────────────────────────
    TemplateDef(
        key="o_internal_alert", product="ops", category="transactional", name="Internal alert",
        description="Internal notice to info@/support@ (new enquiry, agency awaiting approval, …).",
        subject="[ApplyForge] {{ title }}",
        heading="{{ title }}",
        body="{{ body }}",
        cta_label="Open admin", cta_url="{{ admin_url }}",
        variables=("title", "body", "admin_url"), sample={"title": "New agency awaiting approval", "body": "Northbridge Talent (sam@northbridge.co.uk) signed up.", "admin_url": "https://applyforge.co.uk/admin/agencies"},
    ),
]

SYSTEM_TEMPLATES: dict[str, TemplateDef] = {t.key: t for t in T}
