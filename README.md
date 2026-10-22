# Smart Movie Experience & Booking System

Full-stack movie discovery, seat booking and **demand-based dynamic pricing** platform.
Course **BCSE302P – Database Management Systems Lab**, VIT Vellore · Faculty guide: **Navamani T M**
Team: **Kalivarapu Prabhas (24BCE2279)**, **Shanmukha Abhinav (24BAI0238)**

## What is in this repository
| Path | Purpose |
|---|---|
| `database/oracle/01_schema.sql` | Oracle DDL – 20 tables, constraints, sequences, triggers (3NF) |
| `database/oracle/02_plsql.sql` | Oracle PL/SQL – `pkg_dynamic_pricing`, `book_seats`, `cancel_booking`, 15-min scheduler job |
| `database/oracle/03_seed.sql` | Oracle DML – generated sample data (5.7k INSERTs) |
| `database/queries.sql` | 12 demonstration queries (ANSI SQL, run on Oracle and SQLite) |
| `database/sqlite/schema.sql` | Same relational model for the running web app |
| `database/seed_data.py` | Single source of the sample data; emits SQLite rows and the Oracle script |
| `backend/` | Flask REST API (`app.py`), pricing engine (`pricing.py`), DB bootstrap (`db.py`) |
| `frontend/` | Single-page web UI (HTML/CSS/vanilla JS) |
| `tests/test_all.py` | 29 automated tests · `scripts/e2e_browser.py` – 18 real-browser checks |
| `docs/` | ER diagram, screenshots, test evidence |

> **Oracle vs SQLite.** The Oracle scripts are the lab deliverable and were written for Oracle 11g+. The running website uses
> SQLite (identical tables/constraints) so it can run and deploy anywhere with zero setup. The Oracle scripts were **not executed**
> in the environment where this code was generated (no Oracle server available there) – run them in your lab and fix any
> version-specific issue; see `docs/SETUP.md`.

## Quick start (local)
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python backend/app.py                                  # http://localhost:5000  (DB is created + seeded on first run)
```
Demo logins – customer: `aarav.sharma@example.com` / `Password@123` · admin: `admin@smartmovie.local` / `Admin@123`

Run tests: `python manage.py test` (unit/API) · `pip install playwright && playwright install chromium && python scripts/e2e_browser.py` (browser)

## Features
Browse/search/filter movies · register/login (salted password hashes, separate admin login) · theatre & show selection ·
interactive seat map with live availability · smart seat recommendation · **double-booking prevention**
(`UNIQUE(show_id, seat_id)` + serialised transactions) · simulated payments · cancellation with refund · reviews (only after attending) ·
watchlist · genre preferences · notifications (booking, reminders, new releases, watchlist) · movie memories (photo/video per booking) ·
**dynamic pricing** (occupancy, booking speed, popularity, urgency → multiplier → clamped price, fully audited) ·
admin dashboard (revenue, occupancy, demand, price changes, movie/show/theatre/screen/user management, pricing configuration).

## Dynamic pricing (Review 0, section 5)
`demand = wO·O + wS·S + wP·P + wU·U` → `multiplier = round(1 + k·(demand − 0.5), 3)` → `price = clamp(round(base × multiplier), min, max)`.
Every run writes `Demand_Analysis`; every change writes `Price_History`. Implemented identically in Python (`backend/pricing.py`) and PL/SQL.

## Deployment
See `docs/SETUP.md` (Render blueprint `render.yaml`, `Procfile`, `Dockerfile` included). Environment variables: `.env.example`.
Sample data only; payments are simulated.
