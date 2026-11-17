"""
Shared resume-writing rules for every LLM prompt that writes or rewrites
resume bullets (tailored resume generation, AI bullet suggestions,
ephemeral/anonymous generation).

Keeping the wording in one place means the XYZ formula and the
no-fabrication rule cannot drift between prompts.
"""

# Laszlo Bock's XYZ formula: "Accomplished [X] as measured by [Y], by doing [Z]".
XYZ_BULLET_RULES = (
    "BULLET FORMULA — Laszlo Bock XYZ (apply to EVERY work-experience and project bullet):\n"
    "  \"Accomplished [X] as measured by [Y], by doing [Z]\"\n"
    "  • X = the accomplishment / outcome. Open with a strong action verb "
    "(past tense; present tense only for the current role).\n"
    "  • Y = how it was measured: a number, %, currency, time saved, volume, scale, "
    "users, ranking or other concrete measure.\n"
    "  • Z = how it was done: the specific action, method, tool or technology. "
    "Put truthful job-description keywords here.\n"
    "  • Write it as one natural sentence containing all three parts — do not literally "
    "print the words \"as measured by\" or the letters X/Y/Z.\n"
    "    Example: \"Cut monthly cloud spend 32% ($14K/yr) by rightsizing EC2 fleets and "
    "moving batch jobs to Spot instances.\"\n"
    "  • One accomplishment per bullet, at most ~30 words, no \"I\"/\"my\", no filler like "
    "\"responsible for\" or \"worked on\".\n"
    "  • 3–6 bullets per role, most relevant to the target job first.\n"
    "\n"
    "TRUTHFULNESS FOR [Y] (non-negotiable):\n"
    "  • NEVER invent, estimate or round up a number, percentage, currency amount, user count "
    "or timeframe. Every figure in Y must appear in the candidate data.\n"
    "  • If the data has no metric for an accomplishment, use a factual measure of scope that IS "
    "in the data (team size, number of clients/systems/markets, shipped to production, adopted "
    "company-wide, etc.).\n"
    "  • If there is no truthful measure at all, write the bullet as X + Z only. A bullet without "
    "a number is acceptable; a made-up number is not.\n"
)

# Plain-text ATS constraints shared by resume generators.
ATS_FORMAT_RULES = (
    "ATS RULES (follow strictly):\n"
    "• Plain text only — NO tables, NO columns, NO text boxes, NO graphics, NO icons, "
    "NO headers/footers\n"
    "• Standard section headings only, and only for sections with real data: CONTACT, "
    "PROFESSIONAL SUMMARY, SKILLS, WORK EXPERIENCE, PROJECTS, EDUCATION, CERTIFICATIONS\n"
    "• For each role: Job Title — Company, Location, then dates on the same line\n"
    "• Dates in the format: Mon YYYY – Mon YYYY (or Present)\n"
    "• Bullets start with '•'\n"
    "• Mirror keywords from the job description naturally (in Skills and in the Z part of bullets) "
    "— never keyword-stuff\n"
    "• Output clean text with '---' between sections\n"
)
