"""Routes for the isolated AI assistants ("Career Team")."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, g, abort

from .auth import active_required
from .services import assistants as svc

bp = Blueprint("assistants", __name__, url_prefix="/assistants")


@bp.route("/")
@active_required
def index():
    counts = svc.thread_counts(g.user["id"])
    return render_template("assistants.html", assistants=svc.ASSISTANTS, counts=counts)


def _valid(assistant):
    if assistant not in svc.ASSISTANTS:
        abort(404)


@bp.route("/<assistant>")
@active_required
def chat(assistant):
    _valid(assistant)
    thread = svc.get_thread(g.user["id"], assistant)
    return render_template(
        "assistant_chat.html",
        assistant_key=assistant,
        assistant=svc.ASSISTANTS[assistant],
        thread=thread,
    )


@bp.route("/<assistant>/send", methods=["POST"])
@active_required
def send(assistant):
    _valid(assistant)
    text = request.form.get("message", "")
    if not text.strip():
        flash("Type a message first.", "warning")
    else:
        svc.send_message(g.user, assistant, text)
    return redirect(url_for("assistants.chat", assistant=assistant))


@bp.route("/<assistant>/clear", methods=["POST"])
@active_required
def clear(assistant):
    _valid(assistant)
    svc.clear_thread(g.user["id"], assistant)
    flash("Conversation cleared — this assistant starts fresh.", "info")
    return redirect(url_for("assistants.chat", assistant=assistant))
