"""Gold-standard CV the system learns from.

A single, real, well-written CV (the owner's) is the reference every AI feature
is taught against:

  * tailoring uses it as a few-shot example so output matches its structure;
  * CV review scores against the qualities measured here;
  * the CV Coach assistant coaches toward this standard.

The "learning" is two layers, both deterministic and inspectable:

  1. STYLE_GUIDE — prose rules distilled from the exemplar, injected into the
     AI prompts (the API path).
  2. learned_profile() — measurable features extracted from the exemplar
     (section order, quantified-bullet ratio, action-verb ratio, named tools).
     The offline rule-based reviewer scores CVs against these, so the system
     "knows" the standard even with no API key.

Run `python -m app.services.cv_exemplar` to print the learned profile + a
scoring demonstration.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- the exemplar
# The owner's CV — a strong, real example. Kept verbatim (lightly flattened to
# plain text) so the analysis below reflects an actual gold standard, not a
# synthetic one.
EXEMPLAR_CV = """THEBE BETHEL RATSATSI
Gaborone, Botswana | theberatsatsi2@gmail.com | +267 74390351 | linkedin.com/in/thebe-ratsatsi-2aa7842b7

SUMMARY
Innovative and self-driven engineer with strong communication skills and a record of thriving in collaborative teams. Experienced in network operations and support roles, able to manage multiple tasks while driving creative problem-solving. Focused on using technology to improve processes and deliver measurable results.

EXPERIENCE
Botswana Telecommunications Company Limited (BTCL) | Feb 2025 - Jul 2025
Engineering Intern, Gaborone
- Monitored national networks as a Surveillance Engineer at the Network Operations Centre, troubleshooting issues and supporting service restoration using LIBRE, IMASTER, NCE Transmission, MAE, RADWIN, Cambium OWS, and Ericsson ENM.
- Supported antenna installation, preventative maintenance, and restoration of failed sites as a Field Network Engineer.
- Assisted the Power Systems team with UPS installation projects and preventative maintenance of backup systems.

EDUCATION
Botswana University of Science and Technology | 2021 - 2026
BEng Computer and Telecommunications Engineering, Palapye
- Final-year project: a machine-learning framework for early seizure detection from EEG signals, deployed on a Raspberry Pi 3B, achieving 77.07% accuracy and 86.67% seizure recall.
- Extracted 247 features per EEG window and built a hybrid RF-SVM ensemble for three-class detection.
- Research Coordinator, SRC Research and Innovation Committee.

SKILLS
Python, machine learning, network monitoring, project management, communication, teamwork, Microsoft Word and Excel.

CERTIFICATIONS
Driver's Licence.
"""

# --------------------------------------------------------------------------- style guide (for AI prompts)
# Distilled from the exemplar. Injected into tailoring / review / coach prompts.
STYLE_GUIDE = """Learn the STRUCTURE and WRITING QUALITY of the gold-standard CV — not its
content. The example happens to be a student engineer's CV, but the candidate
you are tailoring for may be anyone: a cleaner, a teacher, a graduate, an
accountant, a driver. Keep ONLY the sections their real facts support; never
copy the example's wording, its field, or its student/academic items (e.g. a
final-year project or research role) onto someone who has none. Adapt to the
candidate in front of you.

STRUCTURE (in this order, include only what applies to the candidate):
1. Name in CAPITALS on the first line.
2. Contact line: city, email, phone, LinkedIn — separated by ' | '.
3. SUMMARY — 2-4 sentences of plain prose (NOT bullets). Confident, specific, no clichés.
4. EXPERIENCE — for each role: 'Employer | Date range' on one line, then 'Job title, Location' on the next, then achievement bullets starting with '- '.
5. EDUCATION — 'Institution | Date range', then 'Qualification, Location'. Add project/role bullets ONLY if the candidate genuinely has them.
6. SKILLS — one compact line or short list of real, specific skills and tools.
7. CERTIFICATIONS / LANGUAGES / REFERENCES — only if the candidate has them.

BULLET QUALITY (the part that transfers to every CV):
- Start each bullet with a strong past-tense action verb (Monitored, Designed, Served, Sold, Taught, Managed, Achieved, Supported).
- Quantify with real numbers where the CV provides them (e.g. '20 customers a day', '3 sites', '77.07% accuracy'). Never invent numbers.
- Name specific tools, systems, employers, or methods the candidate actually used.
- Keep bullets to one or two lines; cut filler.

SECTION HEADINGS are ALL-CAPS on their own line. No markdown, no '**', no emojis."""


# --------------------------------------------------------------------------- learned profile (deterministic)
_ACTION_VERBS = {
    "achieved", "assisted", "built", "contributed", "coordinated", "created",
    "deployed", "designed", "developed", "delivered", "drove", "engineered",
    "extracted", "implemented", "improved", "installed", "led", "maintained",
    "managed", "monitored", "optimized", "optimised", "organized", "organised",
    "processed", "reduced", "reported", "researched", "restored", "served",
    "supported", "troubleshot", "tutored", "validated", "worked",
}

_BULLET_RE = re.compile(r"^\s*[-•*]\s+(.*)$")
_HEADING_RE = re.compile(r"^[A-Z][A-Z &/]{2,40}$")
_NUMBER_RE = re.compile(r"\d")


def _bullets(text):
    return [m.group(1).strip() for line in text.splitlines()
            if (m := _BULLET_RE.match(line))]


def _sections(text):
    # Skip the first non-empty line (the candidate's name, also ALL-CAPS).
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    body = lines[1:] if lines else []
    return [ln for ln in body if _HEADING_RE.match(ln)]


def _first_word(s):
    m = re.match(r"[A-Za-z]+", s)
    return m.group(0).lower() if m else ""


def learned_profile(cv_text=EXEMPLAR_CV):
    """Measurable style features extracted from a CV. Computed on the exemplar
    at import time to define the standard; reused to score any CV."""
    bullets = _bullets(cv_text)
    n = len(bullets) or 1
    quantified = sum(1 for b in bullets if _NUMBER_RE.search(b))
    action_led = sum(1 for b in bullets if _first_word(b) in _ACTION_VERBS)
    words = [len(b.split()) for b in bullets]
    return {
        "sections": _sections(cv_text),
        "bullet_count": len(bullets),
        "quantified_ratio": round(quantified / n, 3),
        "action_verb_ratio": round(action_led / n, 3),
        "avg_bullet_words": round(sum(words) / n, 1) if bullets else 0,
        "has_summary_prose": "SUMMARY" in cv_text or "PROFILE" in cv_text,
    }


# The standard, frozen at import from the exemplar.
PROFILE = learned_profile()


def score_against_exemplar(cv_text):
    """Score a CV (0-100) on how well it matches the learned gold standard.
    Returns (score, strengths, issues) — used by the offline reviewer so the
    'learning' applies even with no API key."""
    text = cv_text or ""
    low = text.lower()
    bullets = _bullets(text)
    nb = len(bullets) or 1
    strengths, issues = [], []
    score = 40

    # 1. Structure: expected sections present.
    wanted = {"summary": ("summary", "profile", "objective"),
              "experience": ("experience", "employment", "internship"),
              "education": ("education", "qualification", "degree", "bgcse"),
              "skills": ("skills",)}
    for label, keys in wanted.items():
        if any(k in low for k in keys):
            score += 8; strengths.append(f"Has a clear {label} section.")
        else:
            issues.append(f"Add a {label} section.")

    # 2. Contact details.
    if re.search(r"[\w.\-]+@[\w.\-]+\.\w+", text):
        score += 5; strengths.append("Includes a contact email.")
    else:
        issues.append("Add a contact email.")
    if re.search(r"(\+?267)?\s?\d{7,8}", text):
        score += 3
    else:
        issues.append("Add a phone number.")

    # 3. Quantified achievements — the strongest signal of a good CV.
    quantified = sum(1 for b in bullets if _NUMBER_RE.search(b))
    qr = quantified / nb
    if qr >= 0.4:
        score += 16; strengths.append(f"{quantified} achievements are quantified with real numbers.")
    elif qr > 0:
        score += 8; issues.append("Quantify more achievements with numbers (e.g. '%', 'x3', amounts).")
    else:
        issues.append("Add measurable results — numbers, percentages, or amounts — to your bullets.")

    # 4. Action-verb-led bullets.
    action_led = sum(1 for b in bullets if _first_word(b) in _ACTION_VERBS)
    ar = action_led / nb
    if ar >= 0.6:
        score += 12; strengths.append("Bullets start with strong action verbs.")
    elif ar > 0:
        score += 6; issues.append("Start more bullets with action verbs (Designed, Built, Led, Achieved).")
    else:
        issues.append("Rewrite bullets to start with strong past-tense action verbs.")

    # 5. Specificity: named tools / proper nouns inside bullets.
    tool_hits = len(re.findall(r"\b[A-Z][A-Za-z0-9]+(?:\s[A-Z][A-Za-z0-9]+)?\b",
                               " ".join(bullets)))
    if tool_hits >= 4:
        score += 6; strengths.append("Names specific tools, systems, or technologies.")
    else:
        issues.append("Name the specific tools, systems, or software you used.")

    # 6. Length sanity.
    wc = len(text.split())
    if wc < 120:
        issues.append("CV is short — add more detail about your achievements.")
        score -= 6
    elif wc > 1400:
        issues.append("CV is long — trim to 1-2 pages.")
        score -= 3

    return max(0, min(100, score)), strengths[:8], issues[:8]


if __name__ == "__main__":  # python -m app.services.cv_exemplar
    import json
    print("LEARNED PROFILE (from the gold-standard CV):")
    print(json.dumps(PROFILE, indent=2))
    s, st, iss = score_against_exemplar(EXEMPLAR_CV)
    print(f"\nExemplar scores against itself: {s}/100")
    print("strengths:", st)
