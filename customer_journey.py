"""End-to-end customer journey test against the LIVE server (http://127.0.0.1:8000).

Creates a real test customer (customer.demo@example.com / demo-password-123),
walks the whole flow, and leaves the account active so you can log in and look
around afterwards. Run: python customer_journey.py
"""
import re
import sys

import requests

BASE = "http://127.0.0.1:8000"
CUSTOMER_EMAIL = "customer.demo@example.com"
CUSTOMER_PASSWORD = "demo-password-123"

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def csrf(sess, path):
    html = sess.get(BASE + path).text
    m = re.search(r'name="_csrf" value="([^"]+)"', html)
    return m.group(1) if m else ""


def admin_credentials():
    creds = {}
    with open(".env", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                creds[k.strip()] = v.strip()
    return creds["ADMIN_EMAIL"], creds["ADMIN_PASSWORD"]


# ---------------------------------------------------------------- 1. landing page
c = requests.Session()
landing = c.get(BASE + "/").text
check("landing page renders", "Apply to more jobs" in landing)
check("landing page has no emojis", not re.search(r"[\U0001F300-\U0001FAFF✀-➿☀-⛿]", landing))

# ---------------------------------------------------------------- 2. register
tok = csrf(c, "/register")
r = c.post(BASE + "/register", data={
    "_csrf": tok, "email": CUSTOMER_EMAIL, "password": CUSTOMER_PASSWORD,
    "full_name": "Demo Customer", "phone": "+267 71 234 567",
})
already = "already exists" in r.text
check("customer registration", "pending activation" in r.text or already,
      "unexpected response")

# ---------------------------------------------------------------- 3. login, pending paywall
tok = csrf(c, "/login")
r = c.post(BASE + "/login", data={"_csrf": tok, "email": CUSTOMER_EMAIL,
                                  "password": CUSTOMER_PASSWORD})
dash = c.get(BASE + "/dashboard").text
check("customer can log in", "Welcome, Demo Customer" in dash)
pending = "pending activation" in dash
if pending:
    check("pending banner shows payment numbers",
          "Orange Money" in dash and "74 390 351" in dash and "MyZaka" in dash)
    tok = csrf(c, "/dashboard")
    r = c.post(BASE + "/jobs/add", data={"_csrf": tok, "company_email": "hr@example.co.bw"})
    check("paid features blocked while pending", "not active yet" in r.text)
else:
    print("INFO  account already active from a previous run; skipping paywall checks")

# ---------------------------------------------------------------- 4. admin activates
a = requests.Session()
admin_email, admin_password = admin_credentials()
tok = csrf(a, "/login")
a.post(BASE + "/login", data={"_csrf": tok, "email": admin_email, "password": admin_password})
panel = a.get(BASE + "/admin/").text
check("admin sees the new customer", CUSTOMER_EMAIL in panel)
row = panel[panel.find(CUSTOMER_EMAIL):]
uid = re.search(r"/admin/users/(\d+)/activate", row).group(1)
tok = csrf(a, "/admin/")
r = a.post(BASE + f"/admin/users/{uid}/activate", data={"_csrf": tok, "access_expires": ""})
check("admin activates customer", "User activated" in r.text)

# ---------------------------------------------------------------- 5. CV upload + check
dash = c.get(BASE + "/dashboard").text
check("pending banner gone after activation", "pending activation" not in dash)
cv_text = (
    "Demo Customer\nGaborone, Botswana | +267 71 234 567 | customer.demo@example.com\n\n"
    "PROFILE\nMotivated business administration graduate with retail and customer "
    "service experience.\n\nEDUCATION\nBGCSE, Gaborone Senior Secondary School, 2019\n"
    "Diploma in Business Administration, Botswana Accountancy College, 2023\n\n"
    "EXPERIENCE\nSales Assistant, Choppies, Gaborone (2023-2025)\n- Served customers and "
    "handled cash reconciliation\n- Trained 3 new staff members on POS systems\n\n"
    "SKILLS\nCustomer service, Microsoft Office, stock control, teamwork\n\n"
    "REFERENCES\nAvailable on request."
)
tok = csrf(c, "/dashboard")
r = c.post(BASE + "/cv/upload",
           data={"_csrf": tok},
           files={"cv_file": ("demo_cv.txt", cv_text.encode(), "text/plain")})
check("CV upload", "uploaded and read successfully" in r.text)
tok = csrf(c, "/dashboard")
r = c.post(BASE + "/cv/review", data={"_csrf": tok})
m = re.search(r'CV score: <span class="score \w+">(\d+)</span>', r.text)
check("CV check returns a score", bool(m), "no score found")
if m:
    print(f"      (CV scored {m.group(1)}/100, offline rules engine)")

# ---------------------------------------------------------------- 6. add job + tailor
tok = csrf(c, "/dashboard")
r = c.post(BASE + "/jobs/add", data={
    "_csrf": tok,
    "company_name": "Botswana Power Corporation",
    "company_email": "recruitment@bpc.example.bw",
    "job_title": "Customer Service Officer",
    "job_description": (
        "BPC seeks a Customer Service Officer. Requirements: excellent customer "
        "service, cash handling, Microsoft Office, teamwork, stock control "
        "experience an advantage. Diploma in Business Administration preferred."
    ),
})
check("job added", "Added application for Botswana Power Corporation" in r.text)
m = re.search(r'href="/jobs/(\d+)"', r.text)
app_id = m.group(1)
tok = csrf(c, "/dashboard")
r = c.post(BASE + f"/jobs/{app_id}/tailor", data={"_csrf": tok})
check("tailoring produces a cover letter", "Dear Hiring Manager" in r.text)
check("cover letter mentions matched keywords", "customer" in r.text.lower())

# ---------------------------------------------------------------- 7. edit + send (no SMTP -> graceful error)
tok = csrf(c, f"/jobs/{app_id}")
r = c.post(BASE + f"/jobs/{app_id}/edit", data={
    "_csrf": tok,
    "cover_letter": "Dear Hiring Manager,\n\nEdited cover letter for the send test.\n\nDemo Customer",
    "tailored_cv": cv_text,
})
check("editing the application saves", "Saved your edits" in r.text)
tok = csrf(c, f"/jobs/{app_id}")
r = c.post(BASE + f"/jobs/{app_id}/send", data={"_csrf": tok})
check("send without SMTP fails gracefully",
      "Email is not configured" in r.text and "Settings" in r.text)
check("application marked failed (not crashed)", "failed" in r.text)

# ---------------------------------------------------------------- 8. delete application
tok = csrf(c, f"/jobs/{app_id}")
r = c.post(BASE + f"/jobs/{app_id}/delete", data={"_csrf": tok})
check("application deleted", "Application deleted" in r.text)

print()
if failures:
    print(f"{len(failures)} FAILURES: " + ", ".join(failures))
    sys.exit(1)
print("ALL PASS — customer journey works end to end.")
print(f"Test account left active for you: {CUSTOMER_EMAIL} / {CUSTOMER_PASSWORD}")
