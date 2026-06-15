"""Isolated AI assistants ("Career Team").

Each assistant is a specialist with its own conversation thread per user — an
isolated context window. The CV Coach never sees your interview chat; the
Interview Coach never sees your cover-letter drafts. Isolation is enforced by
construction: a request for one assistant only ever loads that assistant's rows
from assistant_messages.

Follows the app's offline-first rule: with ANTHROPIC_API_KEY set, replies come
from Claude (via the existing raw-requests client in ai.py — no SDK, per the
project's dependency-light rule); without a key, a rule-based fallback still
gives useful, context-aware guidance.
"""
from flask import current_app

from ..db import query, execute, now_iso
from . import ai
from . import cv_exemplar

# Per-thread context budget sent to the model. The API is stateless, so we
# replay history each turn; capping it keeps long threads fast and cheap.
MAX_TURNS = 20              # most recent messages sent to the model
MAX_CONTEXT_CHARS = 24_000  # hard cap across replayed history
MAX_MESSAGE_CHARS = 4_000   # per user message
REPLY_MAX_TOKENS = 2500  # generous: free reasoning models think before answering

ASSISTANTS = {
    "cv_coach": {
        "name": "CV Coach",
        "tagline": "Improve your CV section by section.",
        "system": (
            "You are a CV coach for job seekers in Botswana. You help the user "
            "improve their CV: structure, wording, achievements, and tailoring for "
            "the Botswana job market. Be specific and practical; suggest concrete "
            "rewrites of their actual CV lines, never invent facts about them. "
            "Keep replies short and focused — this is a chat, not an essay.\n\n"
            "Coach toward this gold standard (adapt to the user's own field — do "
            "not push student or academic items like a final-year project onto "
            "someone who has none):\n"
            + cv_exemplar.STYLE_GUIDE
        ),
    },
    "cover_letter": {
        "name": "Cover Letter Studio",
        "tagline": "Draft and refine cover letters together.",
        "system": (
            "You are a cover-letter writing partner for job seekers in Botswana. "
            "Help the user draft, critique, and refine cover letters. Mirror the "
            "language of the job advert where honest, keep letters to about three "
            "short paragraphs, and never invent qualifications. Plain, confident, "
            "human English — no clichés or AI-sounding filler."
        ),
    },
    "interview": {
        "name": "Interview Coach",
        "tagline": "Practise interview questions for your target job.",
        "system": (
            "You are an interview coach for job seekers in Botswana. Run realistic "
            "practice: ask one interview question at a time based on the user's "
            "target job, wait for their answer, then give brief constructive "
            "feedback (what was strong, what to improve, an example of a better "
            "answer) before the next question. Cover competency, technical, and "
            "'tell me about yourself' style questions."
        ),
    },
}


# --------------------------------------------------------------------------- threads
def get_thread(user_id, assistant):
    return query(
        "SELECT * FROM assistant_messages WHERE user_id = ? AND assistant = ? ORDER BY id",
        (user_id, assistant),
    )


def thread_counts(user_id):
    """Message count per assistant in one query (picker page)."""
    counts = {key: 0 for key in ASSISTANTS}
    rows = query(
        "SELECT assistant, COUNT(*) AS c FROM assistant_messages"
        " WHERE user_id = ? GROUP BY assistant",
        (user_id,),
    )
    counts.update({r["assistant"]: r["c"] for r in rows if r["assistant"] in counts})
    return counts


def clear_thread(user_id, assistant):
    execute(
        "DELETE FROM assistant_messages WHERE user_id = ? AND assistant = ?",
        (user_id, assistant),
    )


def _store(user_id, assistant, role, content):
    execute(
        "INSERT INTO assistant_messages (user_id, assistant, role, content, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (user_id, assistant, role, content, now_iso()),
    )


# --------------------------------------------------------------------------- context
def _user_context(user, assistant):
    """Per-assistant background context (kept in the system prompt so the
    conversation itself stays purely user/assistant turns)."""
    parts = []
    if user["full_name"]:
        parts.append(f"The user's name is {user['full_name']}.")
    cv = (user["cv_text"] or "").strip()
    if cv:
        parts.append("Their current CV:\n---\n" + cv[:8000] + "\n---")
    elif assistant == "cv_coach":
        parts.append("They have not uploaded a CV yet — suggest they do, and help from scratch.")
    if assistant in ("cover_letter", "interview"):
        app_row = query(
            "SELECT * FROM applications WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user["id"],), one=True,
        )
        if app_row and (app_row["job_title"] or app_row["job_description"]):
            parts.append(
                f"Their most recent target job: {app_row['job_title'] or 'untitled'} "
                f"at {app_row['company_name'] or 'unknown company'}.\n"
                f"Job description:\n---\n{app_row['job_description'][:4000]}\n---"
            )
    return "\n\n".join(parts)


def _history_for_model(rows):
    """Most recent MAX_TURNS messages, trimmed to the context budget, in order.
    Always starts on a 'user' turn (API requirement)."""
    recent = list(rows)[-MAX_TURNS:]
    total = 0
    kept = []
    for row in reversed(recent):           # newest first, keep until budget hit
        total += len(row["content"])
        if total > MAX_CONTEXT_CHARS:
            break
        kept.append(row)
    kept.reverse()
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return [{"role": r["role"], "content": r["content"]} for r in kept]


# --------------------------------------------------------------------------- chat
def send_message(user, assistant, text):
    """Store the user's message, get the assistant's reply, store and return it."""
    cfg = ASSISTANTS[assistant]
    text = (text or "").strip()[:MAX_MESSAGE_CHARS]
    if not text:
        return None
    _store(user["id"], assistant, "user", text)

    system = cfg["system"]
    context = _user_context(user, assistant)
    if context:
        system = f"{system}\n\nBackground about this user:\n{context}"
    messages = _history_for_model(get_thread(user["id"], assistant))

    reply = ""
    if ai._has_ai():
        try:
            reply = ai._call_claude_chat(system, messages, max_tokens=REPLY_MAX_TOKENS).strip()
        except Exception as exc:  # noqa: BLE001 — fall back, never break the chat
            current_app.logger.warning("Assistant '%s' AI call failed: %s", assistant, exc)
    if not reply:
        reply = _offline_reply(user, assistant, text)

    _store(user["id"], assistant, "assistant", reply)
    return reply


# --------------------------------------------------------------------------- offline fallback
def _offline_reply(user, assistant, text):
    """No API key (or the call failed): give rule-based, still-useful guidance."""
    if assistant == "cv_coach":
        review = ai.review_cv(user["cv_text"] or "")
        if not (user["cv_text"] or "").strip():
            return ("I can't see a CV yet — upload one on the Dashboard and I'll "
                    "review it. Meanwhile: a strong Botswana CV has contact details, "
                    "a 2-3 line profile, education (BGCSE/diploma/degree), work "
                    "experience with achievements, skills, and two references.")
        lines = [f"Your CV scores about {review['score']}/100. Here's what to work on:"]
        lines += [f"• {issue}" for issue in review["issues"][:5]]
        lines += [f"✓ {s}" for s in review["strengths"][:3]]
        lines.append("(Offline mode — set an API key for conversational coaching.)")
        return "\n".join(lines)

    if assistant == "cover_letter":
        return ("Here's a structure that works:\n"
                "1. Opening — name the exact role and where you saw it.\n"
                "2. Middle — two or three of your achievements that match the "
                "advert's keywords (use their words where honest).\n"
                "3. Close — confident ask for an interview, thank them.\n"
                "Keep it under one page. Paste a draft here and I'll point out "
                "filler phrases to cut. For the strongest result, tailor each "
                "application from the Dashboard. "
                "(Offline mode — set an API key for full drafting help.)")

    # interview coach
    app_row = query(
        "SELECT * FROM applications WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
        (user["id"],), one=True,
    )
    title = (app_row["job_title"] if app_row else "") or "the role"
    keywords = ai._top_keywords(app_row["job_description"] if app_row else "", n=3)
    questions = [
        "Tell me about yourself and why you applied for this role.",
        f"What makes you a strong candidate for {title}?",
        "Describe a time you solved a difficult problem. What did you do and what was the result?",
        "What is your biggest weakness, and how are you working on it?",
        "Where do you see yourself in five years?",
    ]
    if keywords:
        questions.insert(2, f"What experience do you have with {', '.join(keywords)}?")
    return ("Practise these out loud — structure answers as Situation, Task, "
            "Action, Result:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
            + "\n(Offline mode — set an API key for interactive mock interviews.)")
