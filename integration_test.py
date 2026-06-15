"""Integration test for the two paths that need external services:

1. Real SMTP sending  -> verified against a local SMTP sink server (stdlib),
   including the PDF attachment that gets generated and mailed.
2. Claude AI tailoring -> verified against a local mock of the Anthropic API
   (ANTHROPIC_API_URL override), covering review, tailoring, and humanizing.

No real credentials or network access needed. Run: python integration_test.py
"""
import email
import json
import os
import re
import socketserver
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SMTP_PORT = 8825
CLAUDE_PORT = 8826

# Environment must be set before the app package is imported.
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["ADMIN_EMAIL"] = "admin@test.local"
os.environ["ADMIN_PASSWORD"] = "admin-password-123"
os.environ["SECRET_KEY"] = "x" * 64
os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
os.environ["ANTHROPIC_API_URL"] = f"http://127.0.0.1:{CLAUDE_PORT}/v1/messages"

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- local SMTP sink
class _SmtpHandler(socketserver.StreamRequestHandler):
    def _reply(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self._reply("220 sink ESMTP")
        envelope, buf, in_data = {}, [], False
        while True:
            line = self.rfile.readline()
            if not line:
                break
            if in_data:
                if line.rstrip(b"\r\n") == b".":
                    envelope["data"] = b"".join(buf)
                    self.server.messages.append(dict(envelope))
                    envelope, buf, in_data = {}, [], False
                    self._reply("250 OK")
                else:
                    buf.append(line[1:] if line.startswith(b"..") else line)
                continue
            cmd = line.decode(errors="replace").strip()
            up = cmd.upper()
            if up.startswith("EHLO"):
                self.wfile.write(b"250-sink\r\n250-AUTH PLAIN LOGIN\r\n250 OK\r\n")
            elif up.startswith("HELO"):
                self._reply("250 sink")
            elif up.startswith("AUTH"):
                self.server.auth_seen = True
                self._reply("235 Authentication successful")
            elif up.startswith("MAIL FROM"):
                envelope["from"] = cmd.split(":", 1)[1].strip()
                self._reply("250 OK")
            elif up.startswith("RCPT TO"):
                envelope.setdefault("to", []).append(cmd.split(":", 1)[1].strip())
                self._reply("250 OK")
            elif up == "DATA":
                in_data = True
                self._reply("354 End data with <CR><LF>.<CR><LF>")
            elif up == "QUIT":
                self._reply("221 Bye")
                break
            else:
                self._reply("250 OK")


class _SmtpSink(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


smtp_sink = _SmtpSink(("127.0.0.1", SMTP_PORT), _SmtpHandler)
smtp_sink.messages = []
smtp_sink.auth_seen = False
threading.Thread(target=smtp_sink.serve_forever, daemon=True).start()


# ---------------------------------------------------------------- mock Claude API
class _ClaudeMock(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        system = body.get("system", "")
        if "humanizing a job-application cover letter" in system or "Rewrite the text" in system:
            text = "Dear Hiring Manager, I am writing to apply. MOCK-HUMANIZED."
        elif "Judge the candidate's CV" in system.replace("\n", " "):
            text = json.dumps({
                "score": 82,
                "strengths": ["Clear contact details", "Relevant retail experience"],
                "issues": ["Add measurable achievements"],
            })
        else:  # tailoring prompt
            text = json.dumps({
                "tailored_cv": "MOCK TAILORED CV - customer service emphasised",
                "cover_letter": "Dear Hiring Manager, draft letter. MOCK-DRAFT.",
            })
        payload = json.dumps({"content": [{"type": "text", "text": text}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


claude_mock = ThreadingHTTPServer(("127.0.0.1", CLAUDE_PORT), _ClaudeMock)
claude_mock.requests = []
threading.Thread(target=claude_mock.serve_forever, daemon=True).start()


# ---------------------------------------------------------------- app under test
from app import create_app  # noqa: E402
from app import db  # noqa: E402
from app.services import ai  # noqa: E402

app = create_app()
app.config["TESTING"] = True
client = app.test_client()


def get_csrf(path):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([^"]+)"', html)
    return m.group(1) if m else None


CV_TEXT = (
    "Demo Customer\nGaborone | +267 71 234 567 | demo@example.com\n\n"
    "EXPERIENCE\nSales Assistant, Choppies (2023-2025)\n"
    "SKILLS\nCustomer service, Microsoft Office\nREFERENCES\nOn request."
)

# Register + activate a customer, give them SMTP settings pointing at the sink.
tok = get_csrf("/register")
client.post("/register", data={"_csrf": tok, "email": "demo@test.local",
                               "password": "password123", "full_name": "Demo Customer"})
with app.app_context():
    db.execute(
        "UPDATE users SET status='active', cv_text=?, smtp_host='127.0.0.1', smtp_port=?, "
        "smtp_user='demo.sender@test.local', smtp_password='sink-pass' WHERE email='demo@test.local'",
        (CV_TEXT, SMTP_PORT),
    )
tok = get_csrf("/login")
client.post("/login", data={"_csrf": tok, "email": "demo@test.local", "password": "password123"})

# ---------------------------------------------------------------- AI path (mock Claude)
with app.app_context():
    review = ai.review_cv(CV_TEXT)
check("AI CV review goes through Claude API", review["powered_by"] == "claude", str(review))
check("AI review score parsed from response", review["score"] == 82)
check("AI review issues parsed", review["issues"] == ["Add measurable achievements"])

tok = get_csrf("/dashboard")
client.post("/jobs/add", data={
    "_csrf": tok, "company_name": "BPC", "company_email": "recruitment@bpc.test",
    "job_title": "Customer Service Officer", "job_description": "Customer service role.",
})
tok = get_csrf("/dashboard")
r = client.post("/jobs/1/tailor", data={"_csrf": tok}, follow_redirects=True)
check("AI tailoring used (powered by claude)", b"(claude)" in r.data)
check("tailored CV from Claude saved", b"MOCK TAILORED CV" in r.data)
check("cover letter went through humanizer pass", b"MOCK-HUMANIZED" in r.data)
calls = [req.get("system", "")[:40] for req in claude_mock.requests]
check("Claude called for tailor + humanize", len(claude_mock.requests) >= 2, str(calls))
auth_headers = all(req.get("model") for req in claude_mock.requests)
check("requests carry the configured model", auth_headers)

# ---------------------------------------------------------------- SMTP path (local sink)
tok = get_csrf("/jobs/1")
r = client.post("/jobs/1/send", data={"_csrf": tok}, follow_redirects=True)
check("send reports success", b"Application sent to recruitment@bpc.test" in r.data)
check("application marked sent", b'badge sent' in r.data or b">sent<" in r.data)
check("SMTP sink received exactly one message", len(smtp_sink.messages) == 1)
check("SMTP login was performed", smtp_sink.auth_seen)

if smtp_sink.messages:
    raw = smtp_sink.messages[0]
    check("envelope recipient correct", "<recruitment@bpc.test>" in raw["to"][0], str(raw["to"]))
    msg = email.message_from_bytes(raw["data"])
    check("subject is the job title", msg["Subject"] == "Application: Customer Service Officer",
          str(msg["Subject"]))
    check("from shows sender address", "demo.sender@test.local" in msg["From"])
    body_part = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
    check("body is the humanized cover letter",
          "MOCK-HUMANIZED" in body_part.get_payload(decode=True).decode())
    pdf_parts = [p for p in msg.walk() if p.get_content_type() == "application/pdf"]
    check("PDF attachment present", len(pdf_parts) == 1)
    if pdf_parts:
        pdf_bytes = pdf_parts[0].get_payload(decode=True)
        check("attachment is a real PDF", pdf_bytes[:5] == b"%PDF-")
        check("attachment filename uses the user's name",
              pdf_parts[0].get_filename() == "CV - Demo Customer.pdf",
              str(pdf_parts[0].get_filename()))

# ---------------------------------------------------------------- rate limit still applies
with app.app_context():
    sent_cap = app.config["SEND_RATE_PER_HOUR"]
    db.execute("UPDATE applications SET status='tailored' WHERE id=1")
    # Pretend the user already hit the cap this hour.
    for _ in range(sent_cap):
        db.execute(
            "INSERT INTO applications (user_id, company_email, status, created_at, sent_at) "
            "SELECT id, 'x@y.test', 'sent', ?, ? FROM users WHERE email='demo@test.local'",
            (db.now_iso(), db.now_iso()),
        )
tok = get_csrf("/jobs/1")
r = client.post("/jobs/1/send", data={"_csrf": tok}, follow_redirects=True)
check("hourly rate limit blocks further sends", b"Hourly send limit reached" in r.data)
check("no extra message hit the sink", len(smtp_sink.messages) == 1)

print()
if failures:
    print(f"{len(failures)} FAILURES: " + ", ".join(failures))
    sys.exit(1)
print("ALL PASS - email sending and AI tailoring both verified.")
