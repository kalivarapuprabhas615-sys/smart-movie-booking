"""Builds Review_1/2/3 .docx reports from the real project files and the recorded test evidence.
Run: python scripts/build_reports.py   (needs python-docx)"""
import json, os, re, sys, sqlite3, tempfile
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "reports"); os.makedirs(OUT, exist_ok=True)
SHOT = os.path.join(ROOT, "docs", "screenshots")
sys.path.insert(0, os.path.join(ROOT, "backend")); sys.path.insert(0, os.path.join(ROOT, "database"))
read = lambda *p: open(os.path.join(ROOT, *p), encoding="utf-8").read()

NAVY = RGBColor(0x1F, 0x2A, 0x5C); GREY = "F2F2F2"
TEAM = [("Kalivarapu Prabhas", "24BCE2279", "Team Member"), ("Shanmukha Abhinav", "24BAI0238", "Team Member")]


# ------------------------------------------------------------------ helpers
def shade(cell, color):
    tcPr = cell._tc.get_or_add_tcPr(); shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), color); tcPr.append(shd)


def new_doc(title_footer):
    d = Document()
    s = d.sections[0]; s.page_width, s.page_height = Cm(21), Cm(29.7)
    s.left_margin = s.right_margin = Cm(2.2); s.top_margin = Cm(2.2); s.bottom_margin = Cm(2)
    st = d.styles["Normal"]; st.font.name = "Calibri"; st.font.size = Pt(11); st.paragraph_format.space_after = Pt(6)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    for n, sz in (("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 11.5)):
        h = d.styles[n]; h.font.name = "Calibri"; h.font.size = Pt(sz); h.font.bold = True; h.font.color.rgb = NAVY
        h.paragraph_format.space_before = Pt(14 if n == "Heading 1" else 10); h.paragraph_format.keep_with_next = True
    # footer: title + page number
    p = s.footer.paragraphs[0]; p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(title_footer + "   |   Page "); r.font.size = Pt(9); r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    for t in ("begin", None, "end"):
        run = p.add_run(); run.font.size = Pt(9)
        if t: e = OxmlElement("w:fldChar"); e.set(qn("w:fldCharType"), t)
        else: e = OxmlElement("w:instrText"); e.set(qn("xml:space"), "preserve"); e.text = "PAGE"
        run._r.append(e)
    return d


def para(d, text="", bold=False, italic=False, size=None, align=None, color=None, after=None):
    p = d.add_paragraph(); r = p.add_run(text); r.bold = bold; r.italic = italic
    if size: r.font.size = Pt(size)
    if color: r.font.color.rgb = color
    if align == "c": p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if after is not None: p.paragraph_format.space_after = Pt(after)
    return p


def rich(d, parts, style=None):
    """parts: list of (text, bold) tuples"""
    p = d.add_paragraph(style=style)
    for t, b in parts: r = p.add_run(t); r.bold = b
    return p


def bullets(d, items):
    for it in items:
        p = d.add_paragraph(style="List Bullet"); p.paragraph_format.space_after = Pt(2)
        if isinstance(it, tuple): r = p.add_run(it[0]); r.bold = True; p.add_run(it[1])
        else: p.add_run(it)


def table(d, header, rows, widths=None, size=9.5, mono_cols=()):
    t = d.add_table(rows=1, cols=len(header)); t.style = "Table Grid"; t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]; c.text = ""; r = c.paragraphs[0].add_run(h); r.bold = True; r.font.size = Pt(size); r.font.color.rgb = RGBColor(255, 255, 255); shade(c, "1F2A5C")
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""; r = cells[i].paragraphs[0].add_run(str(v)); r.font.size = Pt(size)
            if i in mono_cols: r.font.name = "Consolas"
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths): row.cells[i].width = Cm(w)
    # repeat header row
    trPr = t.rows[0]._tr.get_or_add_trPr(); th = OxmlElement("w:tblHeader"); th.set(qn("w:val"), "true"); trPr.append(th)
    d.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def code(d, text, size=7.5, max_lines=None):
    lines = text.rstrip("\n").split("\n")
    if max_lines and len(lines) > max_lines: lines = lines[:max_lines] + [f"-- ... ({len(lines) - max_lines} more lines in the repository file)"]
    for ln in lines:
        p = d.add_paragraph(); p.paragraph_format.space_after = Pt(0); p.paragraph_format.left_indent = Cm(0.2)
        pPr = p._p.get_or_add_pPr(); shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:fill"), GREY); pPr.append(shd)
        r = p.add_run(ln if ln else " "); r.font.name = "Consolas"; r.font.size = Pt(size); r._r.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
    d.add_paragraph().paragraph_format.space_after = Pt(2)


def figure(d, path, caption, width=15.5):
    if not os.path.exists(path): para(d, f"[missing figure: {os.path.basename(path)}]", italic=True); return
    p = d.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER; p.paragraph_format.keep_with_next = True
    p.add_run().add_picture(path, width=Cm(width))
    c = para(d, caption, italic=True, size=9.5, align="c", color=RGBColor(0x55, 0x55, 0x55))


def note(d, text, fill="FFF4CE"):
    t = d.add_table(rows=1, cols=1); t.style = "Table Grid"; c = t.rows[0].cells[0]; shade(c, fill); c.text = ""
    r = c.paragraphs[0].add_run(text); r.font.size = Pt(10); d.add_paragraph().paragraph_format.space_after = Pt(2)


def page_break(d): d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def title_page(d, review_label, subtitle):
    para(d, "VELLORE INSTITUTE OF TECHNOLOGY, VELLORE", bold=True, size=15, align="c", color=NAVY, after=0)
    para(d, "School of Computer Science and Engineering", italic=True, align="c")
    para(d, "")
    para(d, "DATABASE MANAGEMENT SYSTEMS", bold=True, size=18, align="c", after=0)
    para(d, review_label, size=14, align="c")
    para(d, "")
    para(d, "Smart Movie Experience & Booking System", bold=True, size=22, align="c", color=NAVY, after=2)
    para(d, "A full-stack movie discovery, booking, and demand-based dynamic pricing platform", italic=True, align="c")
    para(d, subtitle, bold=True, size=13, align="c")
    para(d, ""); para(d, "Submitted by", bold=True, align="c")
    table(d, ["Name", "Registration No.", "Role"], TEAM, widths=[6, 4, 4], size=11)
    para(d, "Faculty Guide: Navamani T M", align="c", bold=True, after=0)
    para(d, "Slot: L33 + L34     |     Course Code: BCSE302P", align="c")
    para(d, "Report prepared: 22 October 2026", align="c", size=10)
    page_break(d)


def contents(d, items):
    d.add_heading("Contents", 1)
    for i in items: p = d.add_paragraph(i); p.paragraph_format.space_after = Pt(1)
    page_break(d)


# ------------------------------------------------------------------ evidence
def load_evidence():
    ev = {}
    txt = read("docs", "evidence", "unit_test_output.txt")
    ev["unit"] = re.findall(r"^(test_\w+) \((\S+)\) \.\.\. .*?(ok|FAIL|ERROR)$", txt, re.M)
    if len(ev["unit"]) < 29:   # outputs where a warning was interleaved on the same line
        names = re.findall(r"(test_\w+) \((test_all\.\w+)\.\w+\)", txt); ev["unit"] = [(n, c, "ok") for n, c in names]
    ev["unit_summary"] = re.search(r"Ran (\d+) tests in ([\d.]+)s\s+(OK|FAILED.*)", txt).groups()
    ev["e2e"] = json.load(open(os.path.join(ROOT, "docs", "evidence", "e2e_results.json")))
    ev["queries"] = read("docs", "evidence", "query_outputs.txt")
    return ev


DESC = {  # one-line purpose per unit test (documentation of what each proves)
    "test_report_worked_example": "Review-0 worked example: demand 0.696, multiplier 1.118, price Rs.280",
    "test_neutral_demand_keeps_base": "demand 0.5 leaves price at base",
    "test_extremes_and_clamping": "multiplier bounds 0.70/1.30; raw price clamped to admin min/max",
    "test_twenty_tables": "schema has exactly 20 tables",
    "test_double_booking_blocked_by_unique_constraint": "UNIQUE(show_id, seat_id) rejects a duplicate seat for a show",
    "test_price_band_check": "CHECK(min<=current<=max) rejects out-of-band price",
    "test_weights_must_sum_to_one": "CHECK rejects pricing weights not summing to 1",
    "test_one_payment_per_booking": "UNIQUE(booking_id) on Payment (1:1)",
    "test_seat_must_belong_to_show_screen": "trigger rejects a seat from another screen",
    "test_rating_trigger": "avg_rating maintained by trigger on review insert/delete",
    "test_all_prices_inside_bounds_and_logged": "after recompute_all every price is in band; demand + price history logged",
    "test_booking_raises_occupancy_signal": "booking 6 seats raises occupancy in the next demand snapshot",
    "test_register_validation_and_login": "email/password validation, duplicate email 409, wrong password 401",
    "test_passwords_are_salted_hashes": "same password stored with different salted hashes, no plain text",
    "test_auth_required_and_rbac": "anonymous 401; customer cannot reach admin API (403)",
    "test_search_and_filters": "title search, language, rating and genre filters",
    "test_booking_flow_double_booking_and_cancel": "overlapping booking rejected atomically; cancel frees seats; no double cancel",
    "test_invalid_booking_inputs": "empty seats, bad payment method, foreign-screen seat, unknown show",
    "test_cannot_book_started_show": "booking a show that has started is refused",
    "test_concurrent_booking_same_seat_only_one_wins": "8 simultaneous requests for one seat: exactly 1 succeeds, 7 get 409",
    "test_recommendation_returns_free_adjacent_seats": "recommended seats are free and adjacent",
    "test_review_rules_and_rating_update": "review only after attending; upsert; rating range",
    "test_watchlist_and_preferences_and_notifications": "watchlist add/remove, genre preferences, mark-read",
    "test_memory_upload": "image upload linked to booking; .exe rejected; upload before show refused",
    "test_xss_payload_stored_verbatim_not_executable_server_side": "HTML in reviews served only as JSON (UI escapes on render)",
    "test_sql_injection_attempt_is_inert": "SQL injection strings in login/search have no effect",
    "test_admin_crud_pricing_config_and_notifications": "admin movie/show CRUD, new-release + watchlist alerts, config history",
    "test_admin_screen_generates_seats": "adding a 4x6 screen creates 24 seats",
    "test_dashboard_numbers_consistent_with_database": "dashboard totals equal direct SQL aggregates",
}


def dataset_counts():
    import seed_data
    ds = seed_data.build_dataset(); counts = {t: len(r) for t, r in ds.items()}
    import db, pricing
    c = db.init_db(os.path.join(tempfile.mkdtemp(), "r.db"))
    for t in ("demand_analysis", "price_history"): counts[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return counts


ORDER_TABLES = ["theatre", "screen", "seat", "movie", "genre", "movie_genre", "users", "admins", "pricing_config", "user_genre_preference",
                "shows", "booking", "booking_seat", "payment", "review", "movie_memory", "notification", "watchlist", "demand_analysis", "price_history"]

ORACLE_NOTE = ("Verification status: the Oracle scripts (01_schema.sql, 02_plsql.sql, 03_seed.sql) were written for Oracle 11g+ but could not be "
               "executed in the environment where they were produced because no Oracle server was available. The identical relational model, constraints, "
               "triggers and sample data WERE executed and tested on SQLite (see Review 3). Run the Oracle scripts in the lab and record any "
               "version-specific corrections before submission.")


# ================================================================== REVIEW 1
def review1(counts):
    d = new_doc("Review 1 - Normalization, DDL and DML | Smart Movie Booking System")
    title_page(d, "Review 1", "Normalization, DDL and DML")
    contents(d, ["1. Scope of Review 1", "2. Requirements recap and ER/EER design (from Review 0)", "3. Normalization (UNF -> 1NF -> 2NF -> 3NF)",
                 "4. Final relational schema and design decisions", "5. DDL - Oracle SQL/PL-SQL", "6. DML - sample data and operations",
                 "7. Integrity constraints and business rules in the database", "8. Demonstration queries with results", "9. PL/SQL units",
                 "10. Verification status and how to run", "Appendix A - Complete Oracle DDL listing"])

    d.add_heading("1. Scope of Review 1", 1)
    para(d, "This review converts the approved Review-0 design (16 entities, 22 relationships, 20 relational tables, dynamic-pricing algorithm) into a "
            "normalised physical database. It contains the normalization derivation, the Oracle DDL, the DML (sample data and operations), "
            "the integrity rules enforced by the database itself, and demonstration queries.")
    note(d, ORACLE_NOTE)

    d.add_heading("2. Requirements recap and ER/EER design", 1)
    para(d, "The Smart Movie Experience & Booking System lets customers discover movies, receive smart seat recommendations, book tickets with guaranteed protection "
            "against double-booking, keep memories (photos/videos) linked to a booking, rate and review movies, follow genres, keep a watchlist and receive notifications. "
            "Administrators maintain the catalogue and run a demand-analysis pipeline that drives a bounded dynamic-pricing engine, with a dashboard summarising movies, shows, "
            "bookings, occupancy, revenue and demand.")
    figure(d, os.path.join(ROOT, "docs", "diagrams", "er_diagram.png"),
           "Figure 1 - ER/EER diagram (double border = weak entity / identifying relationship). Entities: Theatre, Screen, Seat*, Movie, Genre, Show, User, Admin, Pricing_Config, Booking, Payment, Review, Movie_Memory, Notification, Demand_Analysis*, Price_History*.", width=15)
    d.add_heading("2.1 Cardinalities", 2)
    table(d, ["Relationship", "Card.", "Relational implementation"], [
        ("Theatre-Has-Screen", "1:N", "Screen.theatre_id FK"), ("Screen-Contains-Seat (identifying)", "1:N", "Seat.screen_id FK + UNIQUE(screen_id, seat_number)"),
        ("Screen-Hosts-Show / Movie-Scheduled As-Show", "1:N", "Shows.screen_id, Shows.movie_id FKs"), ("Movie-Belongs To-Genre", "M:N", "bridge MOVIE_GENRE"),
        ("User-Prefers-Genre", "M:N", "bridge USER_GENRE_PREFERENCE"), ("User-Makes-Booking, Show-Booked For-Booking", "1:N", "Booking.user_id, Booking.show_id"),
        ("Booking-Includes-Seat", "M:N", "bridge BOOKING_SEAT with UNIQUE(show_id, seat_id)"), ("Booking-Paid Via-Payment", "1:1", "Payment.booking_id UNIQUE"),
        ("User-Writes-Review / Movie-Receives-Review", "1:N", "Review.user_id, Review.movie_id"), ("User/Booking/Movie - Movie_Memory", "1:N", "Movie_Memory.user_id, booking_id, movie_id"),
        ("User-Gets-Notification", "1:N", "Notification.user_id"), ("User-Saves-Movie", "M:N", "WATCHLIST (+ added_date, notified_status) UNIQUE(user_id, movie_id)"),
        ("Show-Analyzed As-Demand_Analysis, Show-Price Log-Price_History", "1:N identifying", "show_id FK; UNIQUE(show_id, analysis_timestamp)"),
        ("Admin-Manages-Movie / Creates-Show / Sets-Pricing_Config", "1:N", "Shows.admin_id, Pricing_Config.admin_id (see section 4)")], widths=[6.2, 2.2, 8.2])

    d.add_heading("3. Normalization", 1)
    para(d, "Normalization is demonstrated on the document a ticket counter would print for one booking, because it mixes every kind of anomaly the final schema must remove.")
    d.add_heading("3.1 Unnormalised form (UNF)", 2)
    code(d, "BOOKING_SLIP( booking_id, booking_time, booking_status, total_amount,\n"
            "   user_id, user_name, user_email, user_phone,\n"
            "   show_id, show_date, show_time, base_price, current_price, min_price, max_price,\n"
            "   movie_id, title, language, certification, duration, release_date, avg_rating,\n"
            "   { genre_id, genre_name },                                  -- repeating group 1\n"
            "   screen_id, screen_number, total_seats, theatre_id, theatre_name, location, city,\n"
            "   { seat_id, seat_number, seat_type, seat_location },        -- repeating group 2\n"
            "   payment_id, payment_method, payment_status, payment_date )", size=8.5)
    para(d, "Problems: two repeating groups (genres, seats); movie, theatre and user facts are repeated on every booking (update anomaly: renaming a theatre touches thousands of rows; "
            "insertion anomaly: a movie cannot be stored until someone books it; deletion anomaly: cancelling the last booking of a show deletes the only copy of the show's price).")
    d.add_heading("3.2 Functional dependencies identified", 2)
    code(d, "booking_id  -> booking_time, booking_status, total_amount, user_id, show_id\n"
            "booking_id  -> payment_id, payment_method, payment_status, payment_date           (1:1 payment)\n"
            "user_id     -> user_name, user_email, user_phone, join_date ;  user_email -> user_id\n"
            "show_id     -> movie_id, screen_id, admin_id, show_date, show_time, base_price, current_price, min_price, max_price\n"
            "(screen_id, show_date, show_time) -> show_id                                      (a screen runs one show per slot)\n"
            "movie_id    -> title, language, certification, duration, release_date, synopsis, avg_rating\n"
            "movie_id ->> genre_id                                                             (multi-valued dependency)\n"
            "screen_id   -> theatre_id, screen_number, total_seats ;  (theatre_id, screen_number) -> screen_id\n"
            "theatre_id  -> theatre_name, location, city\n"
            "seat_id     -> screen_id, seat_number, seat_type, seat_location ;  (screen_id, seat_number) -> seat_id\n"
            "(show_id, seat_id) -> booking_id                                                  (a seat is sold once per show)", size=8.5)
    d.add_heading("3.3 First normal form (1NF)", 2)
    para(d, "Rule: every attribute atomic, no repeating groups, a primary key exists. The two repeating groups are flattened so each row is one (booking, seat, genre) combination; "
            "the key becomes (booking_id, seat_id, genre_id).")
    code(d, "BOOKING_FLAT( booking_id, seat_id, genre_id, booking_time, ..., user_id, user_name, ..., show_id, ..., movie_id, title, ...,\n"
            "              genre_name, screen_id, ..., theatre_name, ..., seat_number, seat_type, seat_location, payment_method, ... )\n"
            "PK (booking_id, seat_id, genre_id)", size=8.5)
    para(d, "1NF is satisfied but the relation is badly redundant: a booking of 4 seats for a 3-genre movie is stored 12 times.")
    d.add_heading("3.4 Second normal form (2NF)", 2)
    para(d, "Rule: 1NF and no non-key attribute depends on part of a composite key. In BOOKING_FLAT the key has three parts and almost every attribute depends on only a subset of it:")
    table(d, ["Depends only on", "Attributes", "New relation"], [
        ("booking_id", "booking_time, status, total_amount, user_id, show_id, payment_*", "BOOKING (+ PAYMENT)"),
        ("seat_id", "seat_number, seat_type, seat_location, screen_id", "SEAT"),
        ("genre_id", "genre_name", "GENRE"),
        ("(booking_id, seat_id)", "(only the fact 'this seat is in this booking')", "BOOKING_SEAT"),
        ("movie (via show)", "title, language, ... genre membership", "MOVIE, MOVIE_GENRE")], widths=[4, 8, 4.6])
    para(d, "After decomposition no relation has a partial dependency, except the controlled, documented case of BOOKING_SEAT.show_id explained in section 4.2.")
    d.add_heading("3.5 Third normal form (3NF)", 2)
    para(d, "Rule: 2NF and no transitive dependency (non-key -> non-key). Transitive chains found and removed:")
    bullets(d, [("booking_id -> user_id -> user_name/email/phone ", "=> USERS"),
                ("booking_id -> show_id -> movie_id -> title/language/... ", "=> SHOWS, MOVIE"),
                ("show_id -> screen_id -> theatre_id -> theatre_name/city ", "=> SCREEN, THEATRE"),
                ("show_id -> admin_id -> admin attributes ", "=> ADMINS (admin accounts kept apart from customers: separate authentication)"),
                ("movie reviews / memories / notifications ", "each become their own relation keyed by their own surrogate key")])
    para(d, "Weak entities (Seat, Demand_Analysis, Price_History) receive a surrogate primary key plus a UNIQUE constraint on (owner FK, partial key) so that the partial-key semantics of the ER diagram are preserved.")
    d.add_heading("3.6 BCNF check and deliberate redundancy", 2)
    table(d, ["Relation", "Determinants", "BCNF?"], [
        ("SEAT", "seat_id; (screen_id, seat_number)", "Yes - both are candidate keys"), ("SHOWS", "show_id; (screen_id, show_date, show_time)", "Yes"),
        ("SCREEN", "screen_id; (theatre_id, screen_number)", "Yes"), ("BOOKING_SEAT", "(booking_id, seat_id); (show_id, seat_id)", "Yes - both candidate keys (but see 4.2)"),
        ("USERS / ADMINS", "id; email", "Yes"), ("WATCHLIST", "watchlist_id; (user_id, movie_id)", "Yes")], widths=[4, 7, 5.6])
    d.add_heading("3.7 Result", 2)
    para(d, "The decomposition yields the 20 relations of the Review-0 schema: Theatre, Screen, Seat, Movie, Genre, Movie_Genre, Users, Admins, Pricing_Config, User_Genre_Preference, Shows, "
            "Booking, Booking_Seat, Payment, Review, Movie_Memory, Notification, Watchlist, Demand_Analysis, Price_History. All are in 3NF (and BCNF apart from the documented exceptions below); the decomposition is lossless-join and dependency-preserving.")

    d.add_heading("4. Final relational schema and design decisions", 1)
    d.add_heading("4.1 Changes relative to Review 0 (all deliberate)", 2)
    table(d, ["Change", "Reason"], [
        ("User -> USERS, Admin -> ADMINS, Show -> SHOWS", "USER is an Oracle reserved word; SHOW is a SQL*Plus command"),
        ("language -> movie_language, description -> synopsis, comment -> review_comment, duration -> duration_min, name -> theatre_name", "reserved/ambiguous identifiers"),
        ("Seat.seat_type limited to STANDARD / PREMIUM; seat_location FRONT / MIDDLE / BACK", "domain constraints (CHECK) for the recommendation feature"),
        ("UNIQUE(screen_id, show_date, show_time) on SHOWS", "a screen cannot run two shows in the same slot"),
        ("UNIQUE(booking_id, show_id) on BOOKING and composite FK (booking_id, show_id) from BOOKING_SEAT", "makes the redundant show_id in BOOKING_SEAT impossible to contradict (see 4.2)"),
        ("CHECK(min_price <= base_price <= max_price) in addition to the current_price band", "base price outside the admin band would make the clamp meaningless"),
        ("Trigger: seat must belong to the screen of the show", "cross-table rule that a plain FK cannot express"),
        ("Booking cancellation deletes BOOKING_SEAT rows, keeps BOOKING (status CANCELLED) and PAYMENT (REFUNDED)", "frees seats while preserving the financial audit trail"),
        ("Pricing_Config rows are inserted, never updated", "Review-0 requirement 'history of changes is kept'")], widths=[8, 8.6])
    d.add_heading("4.2 Deliberate, controlled denormalisation", 2)
    bullets(d, [("BOOKING_SEAT.show_id: ", "depends on booking_id only (partial dependency, strictly a 2NF violation). It is kept because UNIQUE(show_id, seat_id) is the single constraint that "
                 "guarantees no double-booking at database level, and a UNIQUE constraint cannot span two tables. The composite foreign key (booking_id, show_id) -> BOOKING guarantees the copy can never disagree with BOOKING.show_id."),
                ("SHOWS.current_price: ", "derived from the latest demand analysis; stored for fast reads. CHECK constraint keeps it inside [min_price, max_price]; every change is logged in PRICE_HISTORY."),
                ("MOVIE.avg_rating and SCREEN.total_seats: ", "derivable from REVIEW and SEAT; maintained by a trigger (avg_rating) and by the admin screen-creation routine (total_seats)."),])

    d.add_heading("5. DDL - Oracle SQL/PL-SQL", 1)
    para(d, "The complete script is database/oracle/01_schema.sql (Appendix A). Highlights:")
    table(d, ["Table", "Primary key", "Foreign keys", "Key constraints"], [
        ("THEATRE", "theatre_id", "-", "NOT NULL name/location/city"),
        ("SCREEN", "screen_id", "theatre_id", "UNIQUE(theatre_id, screen_number); total_seats > 0"),
        ("SEAT", "seat_id", "screen_id", "UNIQUE(screen_id, seat_number); type, location CHECK"),
        ("MOVIE", "movie_id", "-", "certification IN (U,UA,A); duration > 0; avg_rating 0..5"),
        ("GENRE / MOVIE_GENRE", "genre_id / (movie_id, genre_id)", "movie_id, genre_id", "UNIQUE genre_name"),
        ("USERS / ADMINS", "user_id / admin_id", "-", "UNIQUE email; password_hash (salted hash)"),
        ("PRICING_CONFIG", "config_id", "admin_id", "weights 0..1 and CHECK(sum = 1); k > 0; window > 0"),
        ("USER_GENRE_PREFERENCE", "(user_id, genre_id)", "user_id, genre_id", "-"),
        ("SHOWS", "show_id", "movie_id, screen_id, admin_id", "CHECK(min<=current<=max); UNIQUE slot; time format"),
        ("BOOKING", "booking_id", "user_id, show_id", "status IN (CONFIRMED, CANCELLED); UNIQUE(booking_id, show_id)"),
        ("BOOKING_SEAT", "(booking_id, seat_id)", "(booking_id, show_id), seat_id", "UNIQUE(show_id, seat_id) = no double-booking"),
        ("PAYMENT", "payment_id", "booking_id (UNIQUE)", "method IN (UPI, CARD, NETBANKING, WALLET); status CHECK"),
        ("REVIEW", "review_id", "user_id, movie_id", "rating 1..5"),
        ("MOVIE_MEMORY", "memory_id", "booking_id, user_id, movie_id", "media_url NOT NULL"),
        ("NOTIFICATION", "notification_id", "user_id", "type, status CHECK"),
        ("WATCHLIST", "watchlist_id", "user_id, movie_id", "UNIQUE(user_id, movie_id); notified_status Y/N"),
        ("DEMAND_ANALYSIS", "demand_id", "show_id", "UNIQUE(show_id, analysis_timestamp); all signals 0..1"),
        ("PRICE_HISTORY", "price_id", "show_id", "old/new price, reason NOT NULL")], widths=[3.6, 3.6, 4, 5.4], size=8.5)
    para(d, "Surrogate keys are generated by one sequence per table (START WITH 10000) and a BEFORE INSERT trigger; seed rows use explicit ids 1..N so sample data and sequences never collide. "
            "Eight indexes support the foreign keys and hot queries. Two triggers enforce rules: trg_booking_seat_screen and trg_review_avg (statement level to avoid the mutating-table error).")

    d.add_heading("6. DML - sample data and operations", 1)
    d.add_heading("6.1 Sample data volume (database/oracle/03_seed.sql)", 2)
    table(d, ["Table", "Rows", "Table", "Rows"], [(ORDER_TABLES[i], counts.get(ORDER_TABLES[i], "-"), ORDER_TABLES[i + 10], counts.get(ORDER_TABLES[i + 10], "-")) for i in range(10)], widths=[5, 3, 5, 3])
    para(d, "Sample data is synthetic and generated reproducibly (random seed 42) by database/seed_data.py: 4 theatres in Vellore, Chennai, Bengaluru and Hyderabad with 8 screens and 776 seats; 12 well-known films "
            "(public facts: title, language, year, running time; synopses written for this project); 12 users; 64 shows over four days (yesterday to day-after-tomorrow); about a thousand bookings with payments; "
            "reviews, watchlists, memories and notifications. Times are relative to SYSDATE so a demo always has upcoming shows. Demo password for customers: Password@123 (stored only as salted hashes).")
    seed = read("database", "oracle", "03_seed.sql").split("\n")
    d.add_heading("6.2 Excerpt of the generated INSERT statements", 2)
    picks = []
    for t in ["theatre", "screen", "movie", "movie_genre", "shows", "booking", "booking_seat", "payment", "review", "watchlist"]:
        picks += [l for l in seed if l.startswith(f"INSERT INTO {t} (")][:2]
    code(d, "\n".join(l[:230] for l in picks), size=7)
    d.add_heading("6.3 DML operations the application performs", 2)
    code(d, "-- Insert a booking (inside book_seats; seats and payment follow in the same transaction)\n"
            "INSERT INTO booking (booking_id, user_id, show_id, booking_time, total_amount, booking_status)\n"
            "VALUES (seq_booking.NEXTVAL, :user, :show, SYSTIMESTAMP, :price * :n_seats, 'CONFIRMED');\n\n"
            "-- Cancel: free the seats, keep the audit trail\n"
            "DELETE FROM booking_seat WHERE booking_id = :b;\n"
            "UPDATE booking SET booking_status = 'CANCELLED' WHERE booking_id = :b;\n"
            "UPDATE payment SET payment_status = 'REFUNDED' WHERE booking_id = :b;\n\n"
            "-- Admin tightens the price band for one show\n"
            "UPDATE shows SET min_price = 220, max_price = 380 WHERE show_id = :s;\n\n"
            "-- Review (upsert done by the application): UPDATE if the user already reviewed the movie, else INSERT\n"
            "UPDATE review SET rating = 5, review_comment = 'Loved it', review_date = SYSTIMESTAMP WHERE user_id = :u AND movie_id = :m;\n\n"
            "-- Delete rules: a show with bookings cannot be deleted (FK), a movie with shows cannot be deleted\n"
            "DELETE FROM shows WHERE show_id = :s;   -- ORA-02292 if bookings exist", size=8)

    d.add_heading("7. Integrity constraints and business rules enforced by the database", 1)
    table(d, ["Business rule", "Mechanism", "Tested on SQLite"], [
        ("A seat cannot be sold twice for the same show", "UNIQUE(show_id, seat_id) + serialised booking transaction", "yes (unit + 8-thread race)"),
        ("A show's price always lies inside the admin band", "CHECK(min_price <= current_price <= max_price)", "yes"),
        ("Pricing weights sum to 1", "CHECK on PRICING_CONFIG", "yes"),
        ("One payment per booking", "UNIQUE(booking_id) on PAYMENT", "yes"),
        ("Seat belongs to the show's screen", "trigger trg_booking_seat_screen", "yes"),
        ("Movie rating reflects its reviews", "trigger trg_review_avg", "yes"),
        ("Valid domains (status, method, type, rating)", "CHECK constraints", "yes (via API validation tests)"),
        ("No orphan rows", "foreign keys with ON DELETE CASCADE only for dependent data", "yes (foreign_keys=ON)")], widths=[6, 6.6, 4])

    d.add_heading("8. Demonstration queries with results", 1)
    para(d, "database/queries.sql holds 12 queries in portable ANSI SQL. The results below were produced by actually running them on the SQLite build of the same schema and sample data "
            "(first rows shown); values depend on the moment the sample data was generated because dates are relative.")
    for blk in counts["_queries"].split("### ")[1:]:
        lines = blk.strip().split("\n"); d.add_paragraph().add_run(lines[0].replace("-- ", ""))
        d.paragraphs[-1].runs[0].bold = True; d.paragraphs[-1].paragraph_format.space_after = Pt(0); d.paragraphs[-1].paragraph_format.keep_with_next = True
        code(d, "\n".join(lines[1:]), size=8)

    d.add_heading("9. PL/SQL units (database/oracle/02_plsql.sql)", 1)
    table(d, ["Unit", "Purpose"], [
        ("pkg_dynamic_pricing.recompute_show(show_id)", "Computes O, S, P, U, demand score and multiplier for one upcoming show; inserts Demand_Analysis; updates Shows.current_price and inserts Price_History when the price changes"),
        ("pkg_dynamic_pricing.recompute_all", "Loops over every show that has not started"),
        ("book_seats(user, show, seat list, method, OUT booking_id)", "Locks the show row (FOR UPDATE), inserts Booking, Booking_Seat rows, Payment, Notification, reprices, COMMIT; duplicate seat -> ORA-20003 and ROLLBACK"),
        ("cancel_booking(booking, user)", "Frees seats, marks CANCELLED/REFUNDED, notifies, reprices"),
        ("JOB_DYNAMIC_PRICING (DBMS_SCHEDULER)", "Runs recompute_all every 15 minutes")], widths=[6, 10.6])
    d.add_heading("Dynamic pricing algorithm (as implemented)", 3)
    code(d, "O = booked_seats / total_seats\n"
            "S = MIN(1, bookings_last_60_min / speed_reference)      speed_reference = busiest hour seen for that capacity class (floor 5)\n"
            "P = 0.6 * (avg_rating / 5) + 0.4 * MIN(1, watchlist_count / popularity_reference)    popularity_reference = max watchlist count (floor 5)\n"
            "U = 1 - MIN(1, hours_remaining / booking_window_hours)\n"
            "demand_score = w_occupancy*O + w_booking_speed*S + w_popularity*P + w_urgency*U      (defaults 0.40 / 0.25 / 0.15 / 0.20)\n"
            "multiplier   = ROUND(1 + elasticity_k * (demand_score - 0.5), 3)                      (k = 0.60 -> 0.70 .. 1.30)\n"
            "current_price= LEAST(max_price, GREATEST(min_price, FLOOR(base_price * multiplier + 0.5)))", size=8.5)
    para(d, "Worked example from Review 0 (verified by an automated test): O=0.72, S=0.60, P=0.50, U=0.9167 -> demand 0.696 -> multiplier 1.118 -> 250 x 1.118 = 279.5 -> Rs.280 inside [200, 400]. "
            "Note: the multiplier is rounded to three decimals before it is applied, which is what makes the report's table (1.118 x 250 = 279.5 -> 280) reproducible; without that rounding the price would be Rs.279.")

    d.add_heading("10. Verification status and how to run", 1)
    note(d, ORACLE_NOTE)
    para(d, "Execution order in SQL*Plus / SQL Developer: @01_schema.sql, @02_plsql.sql, @03_seed.sql, @queries.sql (details in docs/SETUP.md of the repository).")
    page_break(d)
    d.add_heading("Appendix A - Complete Oracle DDL listing (01_schema.sql)", 1)
    code(d, read("database", "oracle", "01_schema.sql"), size=7)
    d.save(os.path.join(OUT, "Review_1_DBMS_Report.docx"))


# ================================================================== REVIEW 2
def review2():
    d = new_doc("Review 2 - Frontend, business logic, database connectivity | Smart Movie Booking System")
    title_page(d, "Review 2", "Frontend, Business Logic and Database Connectivity")
    contents(d, ["1. Scope and architecture", "2. Technology stack", "3. Frontend", "4. Backend REST API", "5. Business logic", "6. Database connectivity",
                 "7. Security", "8. Project structure", "Appendix - key source excerpts"])
    d.add_heading("1. Scope and architecture", 1)
    para(d, "Review 2 covers the working application built on the Review-1 database: a single-page frontend, a Flask REST backend containing all business logic, and the database connectivity layer. "
            "The application runs end to end: every screen described here was exercised in a real browser (results in Review 3).")
    code(d, "Browser (HTML/CSS/JS SPA, hash router)\n"
            "     |  fetch() JSON over HTTP, session cookie (HttpOnly, SameSite=Lax)\n"
            "Flask app (backend/app.py)  -- validation, RBAC, booking transaction, notifications\n"
            "     |-- backend/pricing.py : dynamic pricing engine (+ 15-minute scheduler thread)\n"
            "     |-- backend/db.py      : connection factory, schema + seed bootstrap\n"
            "SQLite database (same 20-table relational model as the Oracle scripts)", size=9)
    note(d, "Database note: the Oracle scripts remain the lab deliverable (Review 1). The running website connects to SQLite because no Oracle server was available in the build environment and SQLite lets the site run and deploy "
            "anywhere. The business rules are enforced by the same constraints in both. Section 6.4 shows how the connection layer would be swapped to Oracle (python-oracledb); that swap was not implemented or tested.")
    d.add_heading("2. Technology stack", 1)
    table(d, ["Layer", "Technology", "Why"], [
        ("Frontend", "HTML5, CSS3 (custom properties, grid, flex, dark/light via prefers-color-scheme), vanilla JavaScript ES2020", "no build step, no dependencies, easy to demonstrate"),
        ("Backend", "Python 3.12, Flask 3.1, Werkzeug", "small, readable REST layer; password hashing and sessions built in"),
        ("Database", "SQLite 3 (running app) / Oracle 11g+ (lab scripts)", "same schema; ACID transactions"),
        ("Server", "gunicorn (production), Flask dev server (local)", "WSGI"),
        ("Testing", "unittest, Flask test client, Playwright + Chromium", "API, DB and real-browser tests")], widths=[2.6, 8, 6])
    d.add_heading("3. Frontend", 1)
    para(d, "A single-page application (frontend/index.html, style.css, app.js - about 450 lines of JavaScript). Hash routing (#/movie/3, #/show/25) means deep links work without server configuration. "
            "All dynamic text is HTML-escaped by a tagged-template helper before insertion, which neutralises stored XSS in reviews and captions. The layout is responsive (verified at 390 px width) and follows the OS light/dark preference.")
    table(d, ["Route", "Screen", "Backend calls"], [
        ("#/", "Home: hero, search box, genre / language / rating filters, movie grid with live 'from' price", "GET /api/movies, /api/genres"),
        ("#/movie/:id", "Details, shows grouped by date and theatre with live price and seats left, watchlist toggle, reviews and review form", "GET /api/movies/:id, /shows, /reviews; POST /reviews; /watchlist"),
        ("#/show/:id", "Interactive seat map, smart recommendation, live price + demand meter, payment selection, booking", "GET /api/shows/:id, /recommend; POST /api/bookings"),
        ("#/bookings", "Booking history, cancel, add memory, link to review", "GET /api/bookings; POST /cancel; POST /memories"),
        ("#/memories, #/watchlist, #/notifications, #/preferences", "Memory gallery, watchlist, alerts with unread badge, genre preferences", "/api/memories, /watchlist, /notifications, /preferences"),
        ("#/login, #/register, #/admin-login", "Authentication forms with server-side validation messages", "/api/auth/*, /api/admin/login"),
        ("#/admin", "Dashboard (overview, shows, movies, theatres & screens, pricing engine, users)", "/api/admin/*")], widths=[3.6, 8, 5], size=8.5)
    d.add_heading("3.1 Interactive seat booking", 2)
    bullets(d, ["Seats are rendered row by row (A, B, ...), premium seats outlined, booked seats disabled and struck through, selected seats green, recommended seats dashed.",
                "The page polls the server every 12 seconds: availability and the live price update without reloading, and a seat someone else just took is automatically deselected.",
                "Smart recommendation: choose 1-6 seats; the server returns the best block of adjacent free seats (rows around 60% of the depth, centred columns, premium preferred) or the best individual seats when no block exists.",
                "If two people click Confirm for the same seat, the loser receives a clear message and the seat map refreshes (demonstrated in Review 3, screenshot 09)."])
    figure(d, os.path.join(SHOT, "01_home.png"), "Figure 1 - Home page with search and filters")
    figure(d, os.path.join(SHOT, "05_movie_detail.png"), "Figure 2 - Movie detail: shows with live dynamic prices (arrows show price above/below base)")
    figure(d, os.path.join(SHOT, "08_recommended_seats.png"), "Figure 3 - Seat map with recommended seats, demand meter and live price")
    figure(d, os.path.join(SHOT, "14_admin_overview.png"), "Figure 4 - Admin dashboard: totals, occupancy, demand and price per show")
    figure(d, os.path.join(SHOT, "18_mobile_home.png"), "Figure 5 - Mobile layout (390 px)", width=6)

    d.add_heading("4. Backend REST API", 1)
    para(d, "All endpoints return JSON; errors use {\"error\": message} with meaningful status codes (400 validation, 401 not logged in, 403 forbidden, 404, 409 conflict, 429 throttled).")
    table(d, ["Method and path", "Role", "Purpose"], [
        ("POST /api/auth/register, /login, /logout; GET /api/auth/me", "public", "accounts, session"), ("POST /api/admin/login", "public", "separate admin authentication"),
        ("GET /api/movies?q&genre&language&min_rating; /api/movies/:id; /api/genres", "public", "catalogue"), ("GET /api/movies/:id/shows, /reviews", "public", "shows with live price, reviews"),
        ("GET /api/shows/:id; /api/shows/:id/recommend?count=n", "public", "seat map + demand snapshot; seat recommendation"),
        ("POST /api/bookings; GET /api/bookings; POST /api/bookings/:id/cancel", "user", "atomic booking, history, cancellation"),
        ("POST /api/movies/:id/reviews", "user", "review (after attending)"), ("POST /api/bookings/:id/memories; GET/DELETE /api/memories", "user", "movie memories (media upload)"),
        ("GET/POST/DELETE /api/watchlist; GET/PUT /api/preferences; GET /api/notifications; POST /api/notifications/read", "user", "personalisation"),
        ("GET /api/admin/dashboard; GET /api/admin/shows/:id/pricing", "admin", "KPIs; demand and price audit"),
        ("POST /api/admin/pricing/run; GET/PUT /api/admin/pricing-config", "admin", "run engine; tune weights (history kept)"),
        ("POST/PUT/DELETE /api/admin/movies, /shows; GET/POST /api/admin/theatres; POST /api/admin/screens", "admin", "catalogue, schedule and screen/seat management"),
        ("GET /api/admin/users; POST /api/admin/admins", "admin", "account management"), ("GET /api/health", "public", "deployment health check")], widths=[8.4, 1.6, 6.6], size=8.5)

    d.add_heading("5. Business logic", 1)
    d.add_heading("5.1 Booking transaction (double-booking prevention)", 2)
    code(d, "BEGIN IMMEDIATE                                   -- takes the write lock: bookings are serialised\n"
            "  load show; refuse if show has started\n"
            "  verify all seat_ids belong to the show's screen\n"
            "  INSERT booking (price = shows.current_price * n_seats)\n"
            "  INSERT booking_seat for each seat           -- UNIQUE(show_id, seat_id) is the last line of defence\n"
            "      on constraint violation -> ROLLBACK, HTTP 409 'seats just booked by someone else'\n"
            "  INSERT payment (SUCCESS, simulated), INSERT notification\n"
            "  recompute_show(show)                         -- occupancy changed, so reprice and log\n"
            "COMMIT", size=8.5)
    para(d, "Defence in depth: (1) the UI hides booked seats, (2) the server checks inside a write-locked transaction, (3) the database UNIQUE constraint rejects duplicates even if the application had a bug. "
            "The price per seat is read inside the transaction, so the customer pays the price that was current at confirmation. Cancellation deletes the Booking_Seat rows (seats become free), marks Booking CANCELLED and Payment REFUNDED, notifies the user and reprices.")
    d.add_heading("5.2 Dynamic pricing engine (backend/pricing.py)", 2)
    para(d, "Implements Review-0 section 5 exactly (formulas in Review 1, section 9). It runs (a) immediately after every booking and cancellation, (b) when an admin saves new pricing weights, creates a show or presses Recompute, "
            "and (c) every 15 minutes from a scheduler thread. Each run inserts one Demand_Analysis row per show; a Price_History row is inserted only when the price changes, with reason demand_recalculation, "
            "suffixed '(clamped to max)' or '(clamped to min)' when the admin band was binding. The pure function compute_price(O, S, P, U, config, base, min, max) is unit-tested against the Review-0 worked example.")
    code(d, read("backend", "pricing.py").split("def latest_config")[0].split('"""', 2)[2], size=8)
    d.add_heading("5.3 Other rules", 2)
    bullets(d, [("Reviews: ", "only users who hold a confirmed booking for a show of that movie that has already started; one review per user per movie (later submissions update it); the movie's average rating is kept in sync by a database trigger."),
                ("Movie memories: ", "uploads (png/jpg/gif/webp/mp4/webm, max 8 MB) only for confirmed bookings whose show has started; stored under a random file name; linked to booking, user and movie."),
                ("Notifications: ", "booking confirmation / cancellation, 3-hour show reminders (scheduler), new-release alerts to users who follow a genre of a newly added movie, watchlist alerts when a show is scheduled for a watchlisted movie."),
                ("Seat recommendation: ", "adjacent block nearest the ideal row (60% depth) and centre columns, premium preferred; falls back to best single seats."),
                ("Admin: ", "movies with shows and shows with bookings cannot be deleted; show times validated (HH:MM), min <= base <= max; a duplicate screen/date/time slot is rejected; pricing weights must sum to 1.")])

    d.add_heading("6. Database connectivity", 1)
    d.add_heading("6.1 Connection layer (backend/db.py)", 2)
    code(d, read("backend", "db.py").split("def now_str")[0], size=8)
    bullets(d, ["One connection per request (flask.g), closed in the teardown hook; no global shared connection.",
                "PRAGMA foreign_keys=ON so referential integrity is enforced; WAL journal for concurrent readers during a write.",
                "isolation_level=None with explicit BEGIN IMMEDIATE ... COMMIT/ROLLBACK in a Tx context manager, so every multi-statement operation is atomic.",
                "All SQL uses bound parameters (?), never string concatenation of user input (injection test in Review 3). Only column-name fragments chosen by the program itself are formatted into SQL."])
    d.add_heading("6.2 Transaction helper and a query example", 2)
    code(d, 'class Tx:\n    """BEGIN IMMEDIATE ... COMMIT/ROLLBACK -- serialises writers so seat checks are race-free."""\n'
            '    def __enter__(self): conn().execute("BEGIN IMMEDIATE"); return conn()\n'
            '    def __exit__(self, et, ev, tb):\n        conn().execute("ROLLBACK" if et else "COMMIT")\n        return False\n\n'
            '# seat map: LEFT JOIN marks seats already present in booking_seat for this show\n'
            'SELECT st.seat_id, st.seat_number, st.seat_type, st.seat_location,\n'
            '       CASE WHEN bs.seat_id IS NULL THEN 0 ELSE 1 END AS booked\n'
            'FROM seat st LEFT JOIN booking_seat bs ON bs.seat_id = st.seat_id AND bs.show_id = ?\n'
            'WHERE st.screen_id = ?', size=8)
    d.add_heading("6.3 Bootstrap", 2)
    para(d, "On first start the app creates the database file, runs database/sqlite/schema.sql, loads the sample data produced by database/seed_data.py and runs a first pricing pass. "
            "The same generator writes the Oracle INSERT script, so both databases hold the same sample data.")
    d.add_heading("6.4 Connecting to Oracle instead (not implemented, not tested)", 2)
    para(d, "If the lab requires the website itself to talk to Oracle, replace db.py with python-oracledb and port the few SQLite-specific functions. Sketch:")
    code(d, "import oracledb\n"
            "pool = oracledb.create_pool(user=os.environ['ORA_USER'], password=os.environ['ORA_PASS'],\n"
            "                            dsn=os.environ['ORA_DSN'], min=1, max=4)   # e.g. localhost/XEPDB1\n"
            "with pool.acquire() as conn, conn.cursor() as cur:\n"
            "    out = cur.var(int)\n"
            "    cur.callproc('book_seats', [user_id, show_id, oracledb.DB_TYPE_..., 'UPI', out])   # PL/SQL unit from 02_plsql.sql\n"
            "    booking_id = out.getvalue()\n"
            "# Port list: datetime('now') -> SYSTIMESTAMP, GROUP_CONCAT -> LISTAGG, LIMIT n -> FETCH FIRST n ROWS ONLY,\n"
            "# INSERT OR IGNORE -> MERGE, '?' placeholders -> ':1' / named binds, lastrowid -> RETURNING ... INTO", size=8)
    d.add_heading("7. Security", 1)
    table(d, ["Concern", "Measure"], [
        ("Password storage", "Werkzeug scrypt salted hashes; test proves identical passwords produce different hashes"),
        ("Roles", "customers and admins in separate tables, separate login endpoints; admin API returns 403 to customers (tested)"),
        ("Brute force", "login throttling: 10 attempts per 5 minutes per IP and email, HTTP 429"),
        ("SQL injection", "bound parameters everywhere; injection strings in login and search tested"),
        ("XSS", "all rendered text HTML-escaped in the frontend; API serves JSON only"),
        ("CSRF", "SameSite=Lax HttpOnly session cookie; state-changing calls need JSON bodies (cross-site form posts cannot set JSON content type)"),
        ("Uploads", "extension allow-list, 8 MB limit, random server-side names; files served from a dedicated folder"),
        ("Secrets", "SECRET_KEY from the environment (must be set in production); no secrets in the repository")], widths=[3.6, 13])
    para(d, "Not covered (out of scope for a lab project): CSRF tokens, email verification, password reset, real payment gateway, rate limiting beyond login, content scanning of uploads.", italic=True)
    d.add_heading("8. Project structure", 1)
    code(d, "smart-movie-booking/\n  backend/   app.py  pricing.py  db.py\n  frontend/  index.html  style.css  app.js  sample/\n  database/  oracle/{01_schema,02_plsql,03_seed}.sql  sqlite/schema.sql  seed_data.py  queries.sql\n"
            "  tests/     test_all.py        scripts/  e2e_browser.py  build_reports.py\n  docs/      SETUP.md  diagrams/  screenshots/  evidence/  reports/\n  wsgi.py  manage.py  requirements.txt  Procfile  Dockerfile  render.yaml  README.md", size=8.5)
    page_break(d)
    d.add_heading("Appendix - key source excerpts", 1)
    app = read("backend", "app.py")
    start = app.index('    @app.post("/api/bookings")'); end = app.index("    def my_booking_rows")
    d.add_heading("A. Booking endpoint (backend/app.py)", 2); code(d, app[start:end], size=7)
    start = app.index('    @app.get("/api/shows/<int:sid>/recommend")'); end = app.index("    # ================= bookings")
    d.add_heading("B. Seat recommendation", 2); code(d, app[start:end], size=7)
    d.add_heading("C. Frontend seat selection (frontend/app.js)", 2)
    js = read("frontend", "app.js"); s = js.index("async function pageShow"); e = js.index("async function pageBookings")
    code(d, js[s:e], size=6.5)
    d.save(os.path.join(OUT, "Review_2_DBMS_Report.docx"))


# ================================================================== REVIEW 3
def review3(ev, counts):
    d = new_doc("Review 3 - Testing, validation, demonstration, final documentation | Smart Movie Booking System")
    title_page(d, "Review 3 (Final)", "Testing, Validation, Screenshots, Demonstration and Final Documentation")
    contents(d, ["1. Summary of the project", "2. Test strategy and environment", "3. Automated unit / database / API tests (29)", "4. Real-browser end-to-end tests (18)",
                 "5. Validation of key requirements", "6. Screenshots", "7. Demonstration script", "8. Software documentation and installation", "9. Repository and deployment status",
                 "10. Limitations and future work", "11. Conclusion"])
    n_unit = int(ev["unit_summary"][0]); e2e = ev["e2e"]; e_pass = sum(1 for r in e2e if r[1] == "PASS")
    d.add_heading("1. Summary of the project", 1)
    para(d, "The Smart Movie Experience & Booking System is a database-driven web platform: customers browse movies, choose a theatre and show, pick seats on an interactive map with smart recommendation, "
            "and book with guaranteed protection against double-booking; they review movies, keep a watchlist, set genre preferences, receive notifications and attach memories to bookings. "
            "Administrators run a demand-analysis pipeline that drives dynamic ticket pricing bounded by configurable limits, and manage the catalogue from a dashboard. "
            "The project delivers a 3NF schema of 20 tables, Oracle DDL/PL/SQL/DML scripts, a Flask + SQLite web application and an automated test suite.")
    table(d, ["Deliverable", "Where", "Verified here?"], [
        ("Normalization, DDL, DML (Oracle)", "database/oracle/*.sql, Review 1", "Oracle scripts NOT executed (no Oracle server); same model executed on SQLite"),
        ("Website: frontend + backend + real database", "frontend/, backend/, SQLite file", f"Yes - {n_unit} API/DB tests and {e_pass}/{len(e2e)} browser checks passed"),
        ("Dynamic pricing algorithm", "backend/pricing.py, pkg_dynamic_pricing", "Python version tested incl. Review-0 worked example; PL/SQL not executed"),
        ("Word reports", "Review_1/2/3 .docx", "Generated from the real files and test evidence"),
        ("GitHub repository", "local git repository with commits", "NOT published - requires your GitHub authorisation"),
        ("Online deployment", "render.yaml, Procfile, Dockerfile", "NOT deployed - requires your hosting account; configs untested")], widths=[5, 5, 6.6], size=9)

    d.add_heading("2. Test strategy and environment", 1)
    bullets(d, [("Levels: ", "formula tests (pure functions), database constraint tests, API tests through Flask's test client, concurrency test with 8 threads, real-browser tests with Playwright/Chromium."),
                ("Isolation: ", "every test class builds a fresh temporary SQLite database seeded with the sample data; nothing touches a shared database."),
                ("Environment: ", "Ubuntu 24 sandbox, Python 3.12.3, Flask 3.1.3, SQLite 3.45.1, Playwright 1.56 (Chromium headless). No network access, no Oracle server."),
                ("Evidence: ", "raw outputs are stored in docs/evidence/ (unit_test_output.txt, e2e_results.json, query_outputs.txt). The tables below are copied from those files, not written by hand.")])
    d.add_heading("3. Automated unit / database / API tests", 1)
    para(d, f"Command: python -m unittest discover -s tests -v   |   Result: Ran {ev['unit_summary'][0]} tests in {ev['unit_summary'][1]} s - {ev['unit_summary'][2]}")
    rows = [(i + 1, n, c.split(".")[-1], DESC.get(n, ""), "PASS" if res == "ok" else "FAIL") for i, (n, c, res) in enumerate(ev["unit"])]
    table(d, ["#", "Test", "Group", "What it proves", "Result"], rows, widths=[0.8, 5.6, 2.4, 6.6, 1.4], size=8)
    d.add_heading("3.1 Concurrency (double-booking) result", 2)
    para(d, "test_concurrent_booking_same_seat_only_one_wins starts eight logged-in customers at a barrier so they all POST a booking for the same seat of the same show at the same instant. "
            "Assertion: exactly one HTTP 201 and seven HTTP 409, and exactly one booking_seat row for that (show, seat). The test passed. This exercises both the write-locked transaction and the UNIQUE constraint.")
    d.add_heading("4. Real-browser end-to-end tests", 1)
    para(d, f"Command: python scripts/e2e_browser.py   |   Result: {e_pass} passed, {len(e2e) - e_pass} failed. The script starts the server on a temporary database, drives Chromium through the UI and saves the screenshots of section 6.")
    table(d, ["#", "Scenario", "Result", "Note"], [(i + 1, r[0], r[1], r[2]) for i, r in enumerate(e2e)], widths=[0.8, 10, 1.6, 4.2], size=8.5)
    para(d, "During development the first browser run failed 8 of 18 checks. Causes were found and fixed: (1) a test assumed the seeded user did not already follow the movie; (2) a race in the test between logout navigation and the next page load; "
            "(3) a real defect: a debounced search callback could run after navigating away and throw 'Cannot set properties of null' - fixed with a guard in the application. The deliberate 401/409 responses of the negative tests are excluded from the 'no console errors' check.", italic=True, size=10)

    d.add_heading("5. Validation of key requirements", 1)
    table(d, ["Requirement (Review 0)", "How validated", "Result"], [
        ("No two bookings claim the same seat for a show", "UNIQUE constraint test; overlapping-booking API test; 8-way race; browser test with second session; SQL audit query Q5 returns 0 rows", "Pass"),
        ("Failed booking leaves no partial data", "Overlapping booking rejected; the non-conflicting seat of that request stays free", "Pass"),
        ("Price always within admin bounds", "CHECK constraint test; after recompute_all no show violates the band; clamp tests", "Pass"),
        ("Pricing follows the documented algorithm", "Worked example reproduced: 0.696 / 1.118 / Rs.280", "Pass"),
        ("Every price decision is traceable", "Demand_Analysis row per recompute, Price_History row per change; admin audit view in UI", "Pass"),
        ("Passwords stored as salted hashes; separate admin/customer auth", "Hash-uniqueness test; RBAC tests", "Pass"),
        ("Search/filter movies", "API filter tests and browser test (title, language)", "Pass"),
        ("Reviews only after watching; ratings update", "Review rules test + trigger test + browser test", "Pass"),
        ("Memories linked to booking", "Upload test, bad-extension rejection, pre-show rejection, browser upload", "Pass"),
        ("Watchlist, preferences, notifications", "API and browser tests", "Pass"),
        ("Admin dashboard figures correct", "Dashboard totals compared with direct SQL aggregates", "Pass"),
        ("Responsive UI", "390 px viewport shows no horizontal overflow", "Pass")], widths=[5.6, 8.6, 2.4], size=8.5)
    d.add_heading("5.1 Not validated", 2)
    bullets(d, ["Oracle 01/02/03 scripts (no Oracle server) - run them in the lab and record outcomes.", "gunicorn, Dockerfile and render.yaml (no network/Docker in the build environment).",
                "Public deployment, HTTPS cookie behaviour, load/performance testing, multi-browser testing (only Chromium), accessibility audit beyond basic labels.",
                "The scheduler thread's 15-minute cycle (the same function was run on demand in tests, but a 15-minute wait was not observed)."])

    d.add_heading("6. Screenshots", 1)
    para(d, "All screenshots were captured by the automated browser run against the real application (sample data, light theme).")
    caps = {"01_home": "Home - 12 movies with search and filters", "02_filter_tamil": "Filter: language = Tamil (2 results)", "03_register": "Registration form",
            "04_login_error": "Wrong password rejected", "05_movie_detail": "Movie detail with shows and live prices", "06_watchlist": "Watchlist",
            "07_seat_map": "Seat map", "08_recommended_seats": "Smart seat recommendation (3 adjacent seats)", "09_double_booking_blocked": "Double booking blocked: seat taken by another session",
            "10_my_bookings": "My bookings after confirmation", "11_memories": "Movie memories gallery", "12_review": "Review posted after attending",
            "13_notifications": "Notifications", "14_admin_overview": "Admin dashboard overview", "15_admin_audit": "Admin: demand and price audit of one show",
            "16_admin_pricing": "Admin: pricing-engine configuration", "17_admin_shows": "Admin: schedule a show"}
    for i, (k, c) in enumerate(caps.items(), 1): figure(d, os.path.join(SHOT, k + ".png"), f"Figure {i} - {c}", width=14.5)
    figure(d, os.path.join(SHOT, "18_mobile_home.png"), f"Figure {len(caps) + 1} - Mobile layout (390 px)", width=5.5)

    d.add_heading("7. Demonstration script (10 minutes)", 1)
    for s in ["Start: python backend/app.py, open http://localhost:5000 (or the deployed URL once you deploy it).",
              "Browse: search 'inception', filter language Tamil, open a movie - point out the live price and the up/down arrow next to it.",
              "Register a new account, then log in; add a movie to the watchlist; set genre preferences.",
              "Open a show: ask for a 3-seat recommendation, show the demand meter and 'x% above/below base' text, pay (simulated) and confirm.",
              "Double booking: in a private window log in as diya.patel@example.com and book one of the same seats first; then press Confirm in the first window - the booking is refused and the map refreshes.",
              "My bookings: cancel a booking and show that its seats are free again and the price reacted; upload a memory for a past booking; review the movie.",
              "Admin (admin@smartmovie.local / Admin@123): dashboard KPIs, Audit a show (Demand_Analysis + Price_History), change pricing weights and watch prices recompute, schedule a new show for a watchlisted movie and show the user's alert.",
              "Database: run database/queries.sql (Q5 returns no rows = no double-booking), show the CHECK/UNIQUE constraints and, in the lab, the Oracle package pkg_dynamic_pricing."]:
        d.add_paragraph(s, style="List Number")

    d.add_heading("8. Software documentation and installation", 1)
    d.add_heading("8.1 Technologies", 2)
    table(d, ["Technology", "Version used", "Role"], [
        ("Oracle Database SQL/PL-SQL", "scripts for 11g+ (not executed here)", "Lab DDL, DML, triggers, pricing package, scheduler job"),
        ("SQLite", "3.45.1", "Database of the running website (same relational model)"), ("Python", "3.12.3", "Backend language"),
        ("Flask / Werkzeug", "3.1.3 / 3.1.7", "REST API, sessions, password hashing"), ("gunicorn", "23.0.0 (pinned, not run here)", "Production WSGI server"),
        ("HTML5 / CSS3 / JavaScript", "ES2020", "Single-page frontend, no frameworks"), ("Playwright + Chromium", "1.56", "Browser tests and screenshots"),
        ("Graphviz", "dot/neato", "ER diagram"), ("python-docx", "1.2.0", "Report generation"), ("Git", "-", "Version control")], widths=[4.6, 4.6, 7.4])
    d.add_heading("8.2 Installation and execution", 2)
    for s in ["Install Python 3.10+ and Git.", "git clone <repository-url>; cd smart-movie-booking", "python -m venv .venv; activate it (Windows: .venv\\Scripts\\activate; others: source .venv/bin/activate)",
              "pip install -r requirements.txt", "python backend/app.py -> open http://localhost:5000 (database created and seeded automatically)",
              "Logins: customer aarav.sharma@example.com / Password@123, admin admin@smartmovie.local / Admin@123",
              "Run tests: python manage.py test; browser tests: pip install playwright; playwright install chromium; python scripts/e2e_browser.py",
              "Oracle lab: in SQL*Plus run @database/oracle/01_schema.sql, @02_plsql.sql, @03_seed.sql, @database/queries.sql",
              "Reset demo data: python manage.py reseed"]:
        d.add_paragraph(s, style="List Number")
    para(d, "Full instructions including deployment and environment variables: docs/SETUP.md in the repository.")

    d.add_heading("9. Repository and deployment status", 1)
    note(d, "GitHub: NOT published. The build environment has no network access or GitHub credentials. A local git repository with commits is included. To publish: create an empty repository on github.com, then run "
            "'git remote add origin <url>', 'git branch -M main', 'git push -u origin main' (authenticate with a personal access token or gh auth login).\n"
            "Deployment: NOT done. render.yaml, Procfile and Dockerfile are provided (untested). To deploy on Render: New > Blueprint > select the repository; verify /api/health after the build.\n"
            "Repository URL: ______________________        Live website URL: ______________________   (fill in after you publish and verify)")
    d.add_heading("10. Limitations and future work", 1)
    bullets(d, ["Payments are simulated; no email/SMS delivery.", "SQLite serialises writers: adequate for a lab, not for a large cinema chain (use Oracle/PostgreSQL with row-level locking).",
                "Free hosting tiers have ephemeral disks: users, bookings and uploads reset on restart unless a disk or external database is attached.",
                "Possible extensions: temporary seat holds with expiry, QR e-tickets, per-seat-type pricing, personalised recommendations from watch history, real payment gateway, Oracle connection layer (python-oracledb)."])
    d.add_heading("11. Conclusion", 1)
    para(d, "The Review-0 design was implemented completely: the normalised 20-table model, the double-booking guarantee at database level, the bounded and fully audited dynamic-pricing algorithm, "
            "and the customer and admin journeys. The parts that remain for the team are the ones that need external accounts or servers: running the Oracle scripts in the lab, pushing to GitHub and deploying to a host.")
    d.save(os.path.join(OUT, "Review_3_Final_DBMS_Report.docx"))


if __name__ == "__main__":
    ev = load_evidence(); counts = dataset_counts(); counts["_queries"] = ev["queries"]
    review1(counts); review2(); review3(ev, counts)
    print("built:", os.listdir(OUT))
