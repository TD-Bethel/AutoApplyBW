# AutoApply BW — Architecture

How the parts connect, from the browser down to the database and external services.
Open this file in VS Code and use the Markdown preview (Ctrl+Shift+V) to see the
diagram rendered.

```mermaid
flowchart TD
    Browser["🌐 Browser<br/>(user / admin)"]

    subgraph App["Flask app — run.py → waitress (4 threads)"]
        Factory["create_app()<br/>app/__init__.py<br/>config · admin · sessions"]

        subgraph Blueprints["Route blueprints"]
            Auth["auth.py<br/>register / login<br/>access control"]
            Main["main.py<br/>dashboard, CV,<br/>jobs, send, settings"]
            Admin["admin.py<br/>activate / suspend<br/>(owner only)"]
        end

        subgraph Services["Service layer"]
            CVParser["cv_parser.py<br/>PDF/DOCX/TXT → text"]
            AI["ai.py<br/>review + tailor<br/>+ HUMANIZER"]
            PDF["pdf.py<br/>CV → PDF (fpdf2)"]
            Emailer["emailer.py<br/>SMTP send"]
        end

        DB["db.py<br/>stdlib sqlite3<br/>query() / execute()"]
    end

    Skill["humanizer_skill.md<br/>(bundled prompt)"]
    Claude["☁️ Anthropic Claude API<br/>(if API key set)"]
    Rules["Offline rules<br/>_tailor_rules +<br/>_rule_humanize"]
    SQLite[("instance/autoapply.db<br/>users · applications")]
    SMTP["✉️ User's SMTP<br/>(Gmail, etc.)"]
    Company["🏢 Company inbox"]

    Browser -->|HTTPS| Factory
    Factory --> Auth & Main & Admin

    Main --> CVParser
    Main --> AI
    Main --> PDF
    Main --> Emailer

    AI -->|key set| Claude
    AI -->|no key| Rules
    Claude -. uses .-> Skill

    Auth --> DB
    Main --> DB
    Admin --> DB
    DB --> SQLite

    Emailer -->|login + send| SMTP
    SMTP -->|application email| Company
```

## Key design points

1. **One app, three blueprints.** `auth` = identity + access control, `main` = all
   user actions, `admin` = owner-only activation. Served by waitress.

2. **Thin service layer.** `cv_parser`, `ai`, `pdf`, `emailer` each do one job and
   are independently testable.

3. **The AI fork.** `ai.py` uses the Claude API with the bundled humanizer skill when
   `ANTHROPIC_API_KEY` is set; otherwise it runs the offline rule-based tailoring and
   `_rule_humanize`. Output shape is identical, so callers don't care which ran.

4. **Single source of truth.** Everything persists through `db.py` into one SQLite
   file (`users`, `applications`).

5. **Two external dependencies, both owner-controlled.** The Anthropic API (one key
   for all users) and each user's own SMTP inbox for sending (rate-limited per hour).

## Where the capacity limits live

- **waitress 4 threads** → ~4 truly-concurrent requests.
- **SQLite single-writer** → write contention is the main ceiling under load.
- **Blocking Claude calls** (AI mode) hold a thread for seconds.
- **SMTP per-user daily caps** (Gmail ~500/day) — but this scales *with* users.
