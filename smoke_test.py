"""Smoke test for auth, CSRF, and admin flows. Run: python smoke_test.py

Uses a throwaway temp database; safe to run anytime.
"""
import os
import re
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["ADMIN_EMAIL"] = "admin@test.local"
os.environ["ADMIN_PASSWORD"] = "admin-password-123"
os.environ["SECRET_KEY"] = "x" * 64

from app import create_app  # noqa: E402

app = create_app()
app.config["TESTING"] = True
client = app.test_client()
failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def get_csrf(path):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([^"]+)"', html)
    return m.group(1) if m else None


# --- public pages render
check("landing page renders", client.get("/").status_code == 200)
check("login page renders", client.get("/login").status_code == 200)
check("register page renders", client.get("/register").status_code == 200)

# --- CSRF: POST without token is rejected
r = client.post("/login", data={"email": "a@b.com", "password": "x"})
check("POST without CSRF token rejected (400)", r.status_code == 400)

# --- register with token works
tok = get_csrf("/register")
check("CSRF token present in register form", bool(tok))
r = client.post("/register", data={
    "_csrf": tok, "email": "user@test.local", "password": "password123",
    "full_name": "Test User", "phone": "+267 71 000 000",
}, follow_redirects=True)
check("register succeeds", r.status_code == 200 and b"free trial" in r.data)

# --- bad email rejected
tok = get_csrf("/register")
r = client.post("/register", data={
    "_csrf": tok, "email": "not-an-email", "password": "password123", "full_name": "X",
}, follow_redirects=True)
check("invalid email rejected", b"valid email is required" in r.data)

# --- login with wrong password fails, with right one works
tok = get_csrf("/login")
r = client.post("/login", data={"_csrf": tok, "email": "user@test.local", "password": "wrong"},
                follow_redirects=True)
check("wrong password rejected", b"Incorrect email or password" in r.data)
tok = get_csrf("/login")
r = client.post("/login", data={"_csrf": tok, "email": "user@test.local", "password": "password123"},
                follow_redirects=True)
check("login works", b"Welcome, Test User" in r.data)
check("pending banner shows pay-by-card CTA", b"/billing" in r.data and b"Pay by card" in r.data)

# --- billing page renders; webhook rejects an unsigned call
r = client.get("/billing/", follow_redirects=True)
check("billing page renders", b"Subscription" in r.data)

# --- Jobs page renders and offers search
r = client.get("/jobs")
check("jobs page renders", r.status_code == 200 and b"Available jobs" in r.data and b"Currently available" in r.data)
check("jobs page lists apply-directly employers", b"Apply directly" in r.data and b"Access Bank Botswana" in r.data)
r = client.post("/billing/webhook", json={"data": {"tx_ref": "x", "id": 1}})
check("webhook rejects missing/forged signature", r.status_code == 401)

# --- open redirect blocked
client.post("/logout", data={"_csrf": get_csrf("/dashboard") or get_csrf("/login")})
tok = get_csrf("/login")
r = client.post("/login?next=https://evil.example.com",
                data={"_csrf": tok, "email": "user@test.local", "password": "password123"})
check("open redirect blocked", "evil.example.com" not in r.headers.get("Location", ""),
      r.headers.get("Location", ""))

# --- new user is on a free trial and CAN use key features (not blocked)
r = client.post("/jobs/add", data={"_csrf": get_csrf("/dashboard"), "company_email": "hr@x.co.bw"},
                follow_redirects=True)
check("trial user can use features", b"Added application" in r.data)

# --- settings: bad port doesn't crash; password change works
r = client.post("/settings", data={"_csrf": get_csrf("/settings"), "full_name": "Test User",
                                   "smtp_port": "not-a-number"}, follow_redirects=True)
check("bad SMTP port doesn't crash", r.status_code == 200)
html = client.get("/settings").get_data(as_text=True)
check("SMTP password not echoed in HTML", 'name="smtp_password" type="password" value=' not in html)
r = client.post("/password", data={"_csrf": get_csrf("/settings"),
                                   "current_password": "password123",
                                   "new_password": "newpassword123"}, follow_redirects=True)
check("password change works", b"Password changed" in r.data)

# --- admin login + actions
client.post("/logout", data={"_csrf": get_csrf("/dashboard")})
tok = get_csrf("/login")
client.post("/login", data={"_csrf": tok, "email": "admin@test.local", "password": "admin-password-123"})
r = client.get("/admin/")
check("admin panel renders", r.status_code == 200 and b"user@test.local" in r.data)
r = client.get("/admin/payments")
check("admin payments page renders", r.status_code == 200 and b"Payments" in r.data)
tok = get_csrf("/admin/")
r = client.post("/admin/users/2/activate", data={"_csrf": tok, "access_expires": "bad-date"},
                follow_redirects=True)
check("invalid expiry date rejected", b"Invalid expiry date" in r.data)
r = client.post("/admin/users/2/activate", data={"_csrf": tok, "access_expires": ""},
                follow_redirects=True)
check("activate works", b"User activated" in r.data)
r = client.post("/admin/users/2/suspend", data={"_csrf": tok}, follow_redirects=True)
check("suspend works", b"User suspended" in r.data)
r = client.post("/admin/users/2/delete", data={"_csrf": tok}, follow_redirects=True)
check("delete user works", b"Deleted user@test.local" in r.data)
r = client.post("/admin/users/1/delete", data={"_csrf": tok}, follow_redirects=True)
check("admin cannot be deleted", b"cannot be deleted" in r.data)

# --- non-admin gets 403 on admin routes
check("security headers present",
      "Content-Security-Policy" in client.get("/").headers
      and client.get("/").headers.get("X-Frame-Options") == "DENY")

# --- suspended user session killed immediately (register + log in via second client)
c2 = app.test_client()
html = c2.get("/register").get_data(as_text=True)
tok2 = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
c2.post("/register", data={"_csrf": tok2, "email": "u2@test.local",
                           "password": "password123", "full_name": "U2"})
html = c2.get("/login").get_data(as_text=True)
tok2 = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
c2.post("/login", data={"_csrf": tok2, "email": "u2@test.local", "password": "password123"})
check("u2 logged in", b"Welcome, U2" in c2.get("/dashboard").data)
tok = get_csrf("/admin/")
client.post("/admin/users/3/suspend", data={"_csrf": tok})
r = c2.get("/dashboard", follow_redirects=False)
check("suspended user session killed immediately", r.status_code == 302)

# --- login brute-force throttle
c3 = app.test_client()
html = c3.get("/login").get_data(as_text=True)
tok3 = re.search(r'name="_csrf" value="([^"]+)"', html).group(1)
for _ in range(8):
    c3.post("/login", data={"_csrf": tok3, "email": "brute@test.local", "password": "bad"})
r = c3.post("/login", data={"_csrf": tok3, "email": "brute@test.local", "password": "bad"},
            follow_redirects=True)
check("login throttle kicks in", b"Too many failed attempts" in r.data)

print()
print(f"{'ALL PASS' if not failures else str(len(failures)) + ' FAILURES: ' + ', '.join(failures)}")
raise SystemExit(1 if failures else 0)
