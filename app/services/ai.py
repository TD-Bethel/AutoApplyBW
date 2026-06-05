"""CV review and per-job tailoring.

Uses the Claude API (via the owner's single API key) when ANTHROPIC_API_KEY is
set. Falls back to a free, offline rule-based approach otherwise, so the app
always works.

The Claude API is called with plain `requests` (no anthropic SDK) to keep the
dependency footprint pure-Python.
"""
import json
import re
from pathlib import Path
import requests
from flask import current_app

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT = 90


# ----------------------------------------------------------------------------- helpers
def _has_ai():
    return bool(current_app.config.get("ANTHROPIC_API_KEY"))


def _call_claude(system, user, max_tokens=2000):
    key = current_app.config["ANTHROPIC_API_KEY"]
    model = current_app.config["ANTHROPIC_MODEL"]
    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return "".join(block.get("text", "") for block in data.get("content", []))


def _extract_json(text):
    """Pull the first JSON object out of a model response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON found in model response.")
    return json.loads(match.group(0))


# ----------------------------------------------------------------------------- humanizer
# Deterministic cleanup of the most obvious AI / typographic tells. Runs always,
# even without an API key, so output is consistent and PDF-safe (Latin-1).
_HUMANIZE_REPLACEMENTS = {
    "—": ", ", "–": "-",
    "’": "'", "‘": "'", "“": '"', "”": '"', "…": "...",
}
# Filler phrases -> plain equivalents (case-insensitive).
_FILLER = [
    (r"\bin order to\b", "to"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bat this point in time\b", "now"),
    (r"\bit is important to note that\b", ""),
    (r"\bhas the ability to\b", "can"),
    (r"\ba testament to\b", "a sign of"),
]


def _light_clean(text):
    """Strip the most obvious AI typography and filler. Safe, deterministic.

    Used on the CV (where structure must be preserved) and on Claude's output.
    """
    if not text:
        return text
    for bad, good in _HUMANIZE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    for pattern, repl in _FILLER:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    # Drop emoji / pictographs.
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)
    # Collapse the double spaces those edits can leave behind.
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ----------------------------------------------------------------------------- offline humanizer "algorithm"
# A deterministic implementation of the humanizer skill's patterns, so the app
# can humanize prose with NO API key. Each rule maps an "AI tell" to a plainer
# form. Swaps preserve the original capitalisation; removals are followed by a
# re-capitalisation + whitespace cleanup pass. We deliberately only include
# high-confidence transforms that don't risk breaking grammar.

# Word / phrase swaps (case-insensitive, capitalisation preserved). Pattern 1, 4,
# 7, 8, 23, 24, 26, 27 from the skill.
_HUMANIZE_SWAPS = [
    # 7. Overused AI vocabulary / wordiness
    (r"\butili[sz]e\b", "use"), (r"\butili[sz]es\b", "uses"),
    (r"\butili[sz]ed\b", "used"), (r"\butili[sz]ing\b", "using"),
    (r"\bleveraging\b", "using"), (r"\bleveraged\b", "used"),
    (r"\bleverages\b", "uses"), (r"\bleverage\b", "use"),
    (r"\bdelve into\b", "explore"), (r"\bdelve\b", "explore"),
    (r"\badditionally\b", "also"), (r"\bmoreover\b", "also"),
    (r"\bfurthermore\b", "also"),
    (r"\ba plethora of\b", "many"), (r"\ba myriad of\b", "many"),
    (r"\ba wide range of\b", "many"), (r"\ba vast array of\b", "many"),
    (r"\ba variety of\b", "various"), (r"\bnumerous\b", "many"),
    # 23. Filler phrases
    (r"\bin order to\b", "to"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bat this point in time\b", "now"),
    (r"\bin the event that\b", "if"),
    (r"\bhas the ability to\b", "can"), (r"\bhave the ability to\b", "can"),
    (r"\bhad the ability to\b", "could"),
    (r"\bwhen it comes to\b", "for"),
    # 24. Excessive hedging
    (r"\bcould potentially possibly\b", "could"),
    (r"\bpotentially possibly\b", "possibly"),
    (r"\bmay potentially\b", "may"),
    # 8. Copula avoidance (use is/are/has)
    (r"\bserves as\b", "is"), (r"\bserve as\b", "are"), (r"\bserved as\b", "was"),
    (r"\bstands as\b", "is"), (r"\bstand as\b", "are"),
    (r"\bboasts\b", "has"), (r"\bboast\b", "have"),
    # 22. Sycophantic / over-eager
    (r"\bthrilled\b", "glad"), (r"\bdelighted\b", "glad"),
    # 1. Significance inflation
    (r"\ba testament to\b", "a sign of"),
    (r"\b(vital|crucial|pivotal|key|essential)\s+role\b", "role"),
    # 4. Promotional adjectives -> plainer words (kept in place to avoid a/an issues)
    (r"\bvibrant\b", "active"), (r"\bseamless\b", "smooth"),
    (r"\bseamlessly\b", "smoothly"), (r"\bgroundbreaking\b", "notable"),
    (r"\bbreathtaking\b", "impressive"), (r"\bstunning\b", "impressive"),
    (r"\bcutting-edge\b", "modern"), (r"\bstate-of-the-art\b", "modern"),
    (r"\bworld-class\b", "excellent"), (r"\btop-notch\b", "excellent"),
    (r"\bunparalleled\b", "strong"), (r"\bunwavering\b", "steady"),
    # 27. Persuasive authority tropes
    (r"\bthe real question is\b", "the question is"),
    # 26. De-hyphenate over-used word pairs
    (r"\bdata-driven\b", "data driven"), (r"\bdecision-making\b", "decision making"),
    (r"\bcross-functional\b", "cross functional"), (r"\breal-time\b", "real time"),
    (r"\blong-term\b", "long term"), (r"\bshort-term\b", "short term"),
    (r"\bhigh-quality\b", "high quality"), (r"\bwell-known\b", "well known"),
    (r"\bend-to-end\b", "end to end"), (r"\bclient-facing\b", "client facing"),
    (r"\bdetail-oriented\b", "detail oriented"), (r"\bresults-driven\b", "results driven"),
    (r"\bteam-oriented\b", "team oriented"), (r"\bgoal-oriented\b", "goal oriented"),
    (r"\bforward-thinking\b", "forward thinking"), (r"\bfast-paced\b", "fast paced"),
]

# Whole-fragment removals (case-insensitive). Patterns 20, 21, 22, 23, 27, 28.
_HUMANIZE_REMOVALS = [
    r"\bit is important to note that\b", r"\bit should be noted that\b",
    r"\bneedless to say,?\s*", r"\bat its core,?\s*", r"\bfundamentally,?\s*",
    r"\bin reality,?\s*", r"\bwhat really matters is\b",
    r"great question[.!]?\s*", r"(certainly|absolutely|of course)!\s*",
    r"i hope this helps[.!]?\s*",
    r"i hope this (email|message|letter)\s+finds you well[.,!]?\s*",
    r"let me know if[^.!?]*[.!?]\s*",
    r"feel free to reach out[^.!?]*[.!?]\s*",
    r"let'?s dive into\s*", r"let'?s (explore|break this down|take a look)[.:]?\s*",
    r"without further ado,?\s*",
    r"as of my last (training )?update[,.]?\s*",
    r"while specific details (are|remain) (limited|scarce)[^.]*\.\s*",
    r"could potentially be argued that\b", r"it could be argued that\b",
]

# Group-using substitutions. Patterns 9 (negative parallelism) and 15 (boldface).
_HUMANIZE_SUBS = [
    (r"\*\*([^*]+)\*\*", r"\1"), (r"__([^_]+)__", r"\1"),   # strip markdown bold
    (r"it'?s not just ([^,.;]+)[,;]\s*it'?s\s+", "It's "),  # negative parallelism
    (r"it is not just ([^,.;]+)[,;]\s*it is\s+", "It is "),
]


def _preserve_case(repl):
    """Return a re.sub callback that mirrors the matched text's capitalisation."""
    def _f(m):
        s = m.group(0)
        if s.isupper():
            return repl.upper()
        if s[:1].isupper():
            return repl[:1].upper() + repl[1:]
        return repl
    return _f


def _recapitalize(text):
    """Capitalise the first letter of the text and after sentence boundaries."""
    return re.sub(r"(^|[.!?]\s+|\n\s*)([a-z])",
                  lambda m: m.group(1) + m.group(2).upper(), text)


# Words that start with a vowel letter but a consonant sound -> keep "a".
_CONSONANT_SOUND = re.compile(r"^(uni|use|usu|util|usa|ubiq|euro|one|once)", re.I)
# Words that start with a consonant letter but a vowel sound -> use "an".
_VOWEL_SOUND = re.compile(r"^(hour|honest|honou?r|heir)", re.I)


def _fix_articles(text):
    """Correct a/an after swaps changed the following word (e.g. 'a active')."""
    def repl(m):
        article, gap, word = m.group(1), m.group(2), m.group(3)
        vowel = word[:1].lower() in "aeiou"
        if _CONSONANT_SOUND.match(word):
            vowel = False
        elif _VOWEL_SOUND.match(word):
            vowel = True
        fixed = "an" if vowel else "a"
        if article[:1].isupper():
            fixed = fixed.capitalize()
        return f"{fixed}{gap}{word}"
    return re.sub(r"\b([Aa]n?)(\s+)([A-Za-z]+)", repl, text)


def _rule_humanize(text):
    """Offline humanizer: apply the skill's patterns deterministically."""
    if not text:
        return text
    text = _light_clean(text)
    for pattern, repl in _HUMANIZE_SUBS:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    for pattern, repl in _HUMANIZE_SWAPS:
        text = re.sub(pattern, _preserve_case(repl), text, flags=re.IGNORECASE)
    for pattern in _HUMANIZE_REMOVALS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    # Tidy up: spaces before punctuation, doubled spaces, leading line spaces.
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?m)^[ \t]+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _fix_articles(text)
    text = _recapitalize(text)
    return text.strip()


# The full humanizer skill (Wikipedia "Signs of AI writing") is bundled alongside
# this module and loaded as the system prompt, so the app uses the real skill
# rather than a paraphrase. We strip the YAML frontmatter and append a directive
# so the model returns only the final rewrite (no drafts/audits/summaries).
_SKILL_PATH = Path(__file__).with_name("humanizer_skill.md")
_OUTPUT_DIRECTIVE = (
    "\n\n---\n\n## Output rules for this application\n"
    "You are humanizing a job-application cover letter. Apply every guideline above, "
    "but do NOT show drafts, the 'what makes this AI' audit, or a summary of changes. "
    "Preserve all facts exactly and never invent qualifications, employers, dates, or "
    "details. Keep it a professional cover letter. Respond with ONLY the final "
    "rewritten text and nothing else."
)


def _strip_frontmatter(md):
    """Remove a leading YAML '--- ... ---' block if present."""
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end != -1:
            return md[md.find("\n", end + 1) + 1:].lstrip()
    return md


_SKILL_CACHE = None


def _humanize_system():
    """Build the humanizer system prompt from the bundled skill file (cached)."""
    global _SKILL_CACHE
    if _SKILL_CACHE is not None:
        return _SKILL_CACHE
    try:
        skill = _strip_frontmatter(_SKILL_PATH.read_text(encoding="utf-8"))
        _SKILL_CACHE = skill + _OUTPUT_DIRECTIVE
        return _SKILL_CACHE
    except OSError as exc:  # bundled file missing -> minimal safe fallback
        current_app.logger.warning("Humanizer skill file unreadable: %s", exc)
        return (
            "Rewrite the text so it reads as natural, human-written English and does "
            "not look AI-generated. Remove significance inflation, promotional words, "
            "'-ing' tails, vague attributions, negative parallelisms, rule-of-three, "
            "copula avoidance, em dashes, curly quotes, emojis, filler and hedging. "
            "Keep every fact unchanged. Respond with ONLY the rewritten text."
        )


def humanize_text(text):
    """Rewrite text to sound human.

    With an API key, Claude runs the full humanizer skill, then we light-clean the
    result. Without a key (or if the call fails), we fall back to the offline
    rule-based humanizer, which implements the skill's patterns deterministically.
    """
    text = (text or "").strip()
    if not text:
        return text
    if _has_ai():
        try:
            rewritten = _call_claude(
                _humanize_system(),
                f"Rewrite this so it does not read as AI-generated:\n\n{text}",
                max_tokens=1500,
            ).strip()
            if rewritten:
                return _light_clean(rewritten)
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("Humanize pass failed, using offline rules: %s", exc)
    return _rule_humanize(text)


# ----------------------------------------------------------------------------- CV review
def review_cv(cv_text):
    """Return {'score': int, 'strengths': [...], 'issues': [...], 'powered_by': str}."""
    if not cv_text.strip():
        return {"score": 0, "strengths": [], "issues": ["No CV text found. Upload your CV first."],
                "powered_by": "none"}

    if _has_ai():
        try:
            return _review_cv_ai(cv_text)
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("AI CV review failed, using fallback: %s", exc)
    result = _review_cv_rules(cv_text)
    return result


def _review_cv_ai(cv_text):
    system = (
        "You are an expert career advisor for the Botswana job market. Review the "
        "candidate's CV and respond ONLY with a JSON object with keys: "
        "score (integer 0-100), strengths (array of short strings), "
        "issues (array of short, specific, actionable strings)."
    )
    raw = _call_claude(system, f"CV:\n\n{cv_text}", max_tokens=1200)
    data = _extract_json(raw)
    return {
        "score": int(data.get("score", 0)),
        "strengths": list(data.get("strengths", []))[:8],
        "issues": list(data.get("issues", []))[:8],
        "powered_by": "claude",
    }


def _review_cv_rules(cv_text):
    text = cv_text.lower()
    strengths, issues, score = [], [], 50
    checks = {
        "contact email": bool(re.search(r"[\w.\-]+@[\w.\-]+", cv_text)),
        "phone number": bool(re.search(r"(\+?267)?\s?\d{7,8}", cv_text)),
        "education section": any(k in text for k in ("education", "qualification", "degree", "diploma", "bgcse")),
        "work experience": any(k in text for k in ("experience", "employment", "worked", "intern")),
        "skills section": "skill" in text,
        "references": "reference" in text,
    }
    for label, present in checks.items():
        if present:
            strengths.append(f"Has a {label}.")
            score += 6
        else:
            issues.append(f"Add a clear {label}.")
            score -= 6
    word_count = len(cv_text.split())
    if word_count < 150:
        issues.append("CV looks short — add more detail about your achievements.")
        score -= 5
    elif word_count > 1200:
        issues.append("CV is long — trim to 1–2 pages and keep the most relevant points.")
        score -= 3
    score = max(0, min(100, score))
    return {"score": score, "strengths": strengths, "issues": issues, "powered_by": "rules"}


# ----------------------------------------------------------------------------- tailoring
def tailor_application(cv_text, full_name, job_title, company_name, job_description):
    """Return {'tailored_cv': str, 'cover_letter': str, 'powered_by': str}."""
    if _has_ai():
        try:
            return _tailor_ai(cv_text, full_name, job_title, company_name, job_description)
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("AI tailoring failed, using fallback: %s", exc)
    return _tailor_rules(cv_text, full_name, job_title, company_name, job_description)


def _tailor_ai(cv_text, full_name, job_title, company_name, job_description):
    system = (
        "You are an expert career advisor in Botswana. Given a candidate's CV and a "
        "job, produce a tailored CV and a tailored cover letter. Keep all facts truthful "
        "to the CV — never invent qualifications or experience. Reorder and emphasise the "
        "most relevant skills, and mirror keywords from the job description where the "
        "candidate genuinely has them. Respond ONLY with a JSON object with keys "
        "'tailored_cv' (string) and 'cover_letter' (string). The cover letter should be "
        "professional, addressed to the company, about 3 short paragraphs."
    )
    user = (
        f"Candidate name: {full_name}\n"
        f"Target job title: {job_title}\n"
        f"Company: {company_name}\n\n"
        f"Job description:\n{job_description}\n\n"
        f"Candidate CV:\n{cv_text}"
    )
    raw = _call_claude(system, user, max_tokens=3000)
    data = _extract_json(raw)
    # Humanize the cover letter (prose) through the humanizer pass; light-clean the
    # CV so its structure stays intact but typography/filler is still cleaned up.
    cover_letter = humanize_text(data.get("cover_letter", "").strip())
    tailored_cv = _light_clean(data.get("tailored_cv", "").strip())
    return {
        "tailored_cv": tailored_cv,
        "cover_letter": cover_letter,
        "powered_by": "claude",
    }


def _tailor_rules(cv_text, full_name, job_title, company_name, job_description):
    keywords = _top_keywords(job_description)
    matched = [k for k in keywords if k in cv_text.lower()]
    company = company_name or "your organisation"
    title = job_title or "the advertised position"
    cover_letter = (
        f"Dear Hiring Manager,\n\n"
        f"I am writing to apply for the {title} position at {company}. "
        f"Having reviewed the requirements, I believe my background aligns well with "
        f"what you are looking for"
        + (f", particularly in {', '.join(matched[:5])}." if matched else ".")
        + "\n\n"
        f"My CV, attached, sets out my qualifications and experience in more detail. "
        f"I am confident I can contribute meaningfully to {company} and would welcome "
        f"the opportunity to discuss my application.\n\n"
        f"Thank you for your time and consideration.\n\n"
        f"Yours sincerely,\n{full_name}"
    )
    return {"tailored_cv": _light_clean(cv_text), "cover_letter": humanize_text(cover_letter),
            "powered_by": "rules"}


_STOPWORDS = set(
    "the a an and or to of in for with on at by is are be as we you your our their "
    "will must have has should can this that they job role work position company".split()
)


def _top_keywords(text, n=15):
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", (text or "").lower())
    freq = {}
    for w in words:
        if w in _STOPWORDS:
            continue
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:n]]
