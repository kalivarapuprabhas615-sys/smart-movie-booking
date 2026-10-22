# Software, installation and deployment guide

## 1. Technologies used and why
| Layer | Technology | Role |
|---|---|---|
| Database (lab deliverable) | **Oracle Database 11g+ (SQL, PL/SQL)** – SQL*Plus / SQL Developer / Live SQL | DDL, constraints, triggers, `pkg_dynamic_pricing`, `book_seats`, `cancel_booking`, scheduler job |
| Database (running website) | **SQLite 3** (Python stdlib `sqlite3`) | Same 20-table model; zero-install, ACID transactions, WAL mode |
| Backend | **Python 3.12**, **Flask 3.1** (REST/JSON), **Werkzeug** (salted password hashing, sessions) | Business logic, auth, booking, pricing, admin API |
| Frontend | **HTML5, CSS3, vanilla JavaScript (ES2020)** single-page app with hash routing | No build step, no third-party runtime dependencies |
| App server | **gunicorn** | Production WSGI server (Procfile / Docker) |
| Testing | Python `unittest`, Flask test client, **Playwright + Chromium** | 29 API/DB tests, 18 browser tests |
| Tooling | Git, Graphviz (ER diagram), python-docx (reports) | Version control, documentation |
| Hosting (optional) | Render.com (`render.yaml`) or any Docker host | Public deployment |

## 2. Run locally (Windows / macOS / Linux)
1. Install **Python 3.10+** (https://www.python.org) and **Git**.
2. `git clone <your-repo-url> && cd smart-movie-booking`
3. Create a virtual environment: `python -m venv .venv` then activate it (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` elsewhere).
4. `pip install -r requirements.txt`
5. `python backend/app.py` → open http://localhost:5000 . The SQLite file `data/smartmovie.db` is created and seeded automatically on first run.
6. Logins – customer `aarav.sharma@example.com` / `Password@123`; admin `admin@smartmovie.local` / `Admin@123` (use *Admin* in the top bar).
7. Tests: `python manage.py test`. Browser tests: `pip install playwright && playwright install chromium && python scripts/e2e_browser.py`.
8. Reset demo data: `python manage.py reseed`.

## 3. Run the Oracle scripts in the lab
1. Open SQL*Plus or SQL Developer connected to your schema (e.g. `sqlplus user/pass@//host:1521/XEPDB1`).
2. `@database/oracle/01_schema.sql` – creates the 20 tables, indexes, sequences, triggers (re-runnable; drops old objects first).
3. `@database/oracle/02_plsql.sql` – compiles the package, procedures and the scheduler job (needs `CREATE JOB`; otherwise skipped with a message). Check `SHOW ERRORS`.
4. `@database/oracle/03_seed.sql` – inserts sample data and runs the first pricing pass.
5. `@database/queries.sql` – the demonstration queries.
6. Try the procedures: 
   ```sql
   DECLARE b NUMBER; BEGIN book_seats(1, 30, SYS.ODCINUMBERLIST(1001,1002), 'UPI', b); DBMS_OUTPUT.PUT_LINE(b); END;
   /
   ```
   (choose a `show_id` that is upcoming and `seat_id`s of that show's screen). A second identical call must fail with ORA-20003.
Status: these scripts were **not executed** where they were written. Expect to fix at most small version-specific items.

## 4. Publish to GitHub
```bash
git remote add origin https://github.com/<your-user>/smart-movie-booking.git   # create the empty repo on github.com first
git branch -M main
git push -u origin main
```
(The repository already has a local commit. Authenticate with a Personal Access Token or `gh auth login`.)

## 5. Deploy online (Render, free tier)
1. Push the repo to GitHub (section 4).
2. On https://render.com → **New → Blueprint** → select the repo. Render reads `render.yaml` (Python web service, health check `/api/health`, generated `SECRET_KEY`).
3. Wait for the build; open the URL. Check `https://<service>.onrender.com/api/health` returns `{"status":"ok"}`.
4. Free-tier notes: the filesystem is ephemeral, so users, bookings and uploads reset on restart; the demo schedule is re-seeded automatically. For persistence attach a disk and set `DATABASE_PATH`/`UPLOAD_DIR` to it, or use the Dockerfile on a host with a volume.
Alternative: `docker build -t smartmovie . && docker run -p 8000:8000 -v smartdata:/data -e SECRET_KEY=... smartmovie`.

## 6. Configuration (environment variables)
`SECRET_KEY` (required in production) · `DATABASE_PATH` · `UPLOAD_DIR` · `COOKIE_SECURE=1` behind HTTPS · `ENABLE_SCHEDULER=1` · `DEMO_AUTORESEED=1` · `PORT`.

## 7. Known limitations
Payments are simulated · SQLite allows one writer at a time (fine for a lab; use Oracle/PostgreSQL for real load) · one gunicorn worker because the scheduler thread lives in-process · uploaded media is stored on local disk · no email/SMS delivery (notifications are in-app).
