"""CV review and per-job tailoring.

Uses the Claude API (via the owner's single API key) when ANTHROPIC_API_KEY is
set. Falls back to a free, offline rule-based approach otherwise, so the app
always works.

The Claude API is called with plain `requests` (no anthropic SDK) to keep the
dependency footprint pure-Python.
"""
import json
import re
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
    return {
        "tailored_cv": data.get("tailored_cv", "").strip(),
        "cover_letter": data.get("cover_letter", "").strip(),
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
    return {"tailored_cv": cv_text, "cover_letter": cover_letter, "powered_by": "rules"}


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
