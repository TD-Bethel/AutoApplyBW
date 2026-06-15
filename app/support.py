"""Support chat: customers report payments and talk to the admin.

The widget is a guided flow of fixed auto-replies ('bot' rows) — payment
method, payment numbers, support line — plus free-text messages that land in
the admin's support inbox. The admin replies from /admin/support and the reply
appears in the customer's widget. No AI involved: answers here must be exact
(phone numbers, payment steps), so they are scripted.
"""
from flask import Blueprint, jsonify, request, g

from .auth import login_required
from .db import query, execute, now_iso

bp = Blueprint("support", __name__, url_prefix="/support")

MAX_MESSAGE = 1000

SUPPORT_LINE = "+267 74 390 351"
PAY_NUMBERS = (
    "Orange Money: +267 74 390 351\n"
    "Pay2cell / Ewallet: +267 74 390 351\n"
    "MyZaka: +267 71 847 749\n"
    "Smega: +267 76 834 418"
)

GREETING = (
    "Dumela! Welcome to AutoApply BW support. How can we help you today? "
    "Pick an option below, or just type a message for the admin."
)

ROOT_OPTIONS = [
    {"key": "paid", "label": "I have paid"},
    {"key": "methods", "label": "Payment options"},
    {"key": "admin", "label": "Chat with the admin"},
    {"key": "line", "label": "Customer support line"},
]

_PAY_METHODS = ["Orange Money", "MyZaka", "Pay2cell / Ewallet", "Smega", "Bank transfer"]
PAY_OPTIONS = [{"key": f"paid:{m}", "label": m} for m in _PAY_METHODS]

_PAID_REPLY = (
    "Thank you! Your payment notice has been sent to the admin — your account "
    "will be activated as soon as the payment is confirmed (usually the same "
    f"day). If it takes longer, call or WhatsApp {SUPPORT_LINE}."
)

# choice key -> (text recorded as the user's message, bot reply, next options)
CHOICES = {
    "paid": (
        "I have paid",
        "Great! Which method did you pay with?",
        PAY_OPTIONS,
    ),
    "methods": (
        "What are the payment options?",
        "You can pay your subscription with any of these:\n" + PAY_NUMBERS +
        "\nOnce you have paid, tap 'I have paid' so the admin can activate you.",
        [{"key": "paid", "label": "I have paid"}] + ROOT_OPTIONS[2:],
    ),
    "admin": (
        "I want to chat with the admin",
        "No problem — type your message below and the admin will reply right "
        "here in this chat. You will see the reply next time you open it.",
        [],
    ),
    "line": (
        "What is the customer support line?",
        f"You can call or WhatsApp us on {SUPPORT_LINE}. We usually respond "
        "fastest on WhatsApp.",
        ROOT_OPTIONS,
    ),
}
for _m in _PAY_METHODS:
    CHOICES[f"paid:{_m}"] = (f"I paid with {_m}", _PAID_REPLY, ROOT_OPTIONS)


def _store(user_id, role, content):
    execute(
        "INSERT INTO support_messages (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, role, content, now_iso()),
    )


def _thread(user_id):
    rows = query(
        "SELECT role, content, created_at FROM support_messages WHERE user_id = ? ORDER BY id",
        (user_id,),
    )
    return [{"role": r["role"], "content": r["content"], "at": r["created_at"]} for r in rows]


@bp.route("/messages")
@login_required
def messages():
    thread = _thread(g.user["id"])
    if not thread:
        _store(g.user["id"], "bot", GREETING)
        thread = _thread(g.user["id"])
    return jsonify({"messages": thread, "options": ROOT_OPTIONS})


@bp.route("/send", methods=["POST"])
@login_required
def send():
    choice = request.form.get("choice", "").strip()
    text = request.form.get("message", "").strip()[:MAX_MESSAGE]

    if choice in CHOICES:
        user_text, reply, options = CHOICES[choice]
        _store(g.user["id"], "user", user_text)
        _store(g.user["id"], "bot", reply)
    elif text:
        _store(g.user["id"], "user", text)
        reply = ("Thanks — the admin has received your message and will reply "
                 "here. Check back in a while, or WhatsApp "
                 f"{SUPPORT_LINE} if it's urgent.")
        _store(g.user["id"], "bot", reply)
        options = ROOT_OPTIONS
    else:
        return jsonify({"error": "empty"}), 400

    return jsonify({"messages": _thread(g.user["id"]), "options": options})
