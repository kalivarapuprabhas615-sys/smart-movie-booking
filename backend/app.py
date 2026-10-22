"""Smart Movie Experience & Booking System -- Flask REST API + static frontend."""
import os, re, sqlite3, threading, time, uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, g, jsonify, request, session, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

import db as dbmod
import pricing

ROOT = dbmod.ROOT
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_MEDIA = {"png", "jpg", "jpeg", "gif", "webp", "mp4", "webm"}
METHODS = {"UPI", "CARD", "NETBANKING", "WALLET"}
MAX_SEATS = 10


class ApiError(Exception):
    def __init__(self, msg, code=400): self.msg, self.code = msg, code


def create_app(db_path=None, start_scheduler=None, autoreseed=None):
    app = Flask(__name__, static_folder=None)
    app.config["DB_PATH"] = db_path or os.environ.get("DATABASE_PATH", os.path.join(ROOT, "data", "smartmovie.db"))
    app.config["UPLOAD_DIR"] = os.environ.get("UPLOAD_DIR", os.path.join(os.path.dirname(app.config["DB_PATH"]), "uploads"))
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1")
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    # ---------- bootstrap database ----------
    path = app.config["DB_PATH"]
    if not os.path.exists(path):
        dbmod.init_db(path).close()
    elif (os.environ.get("DEMO_AUTORESEED", "1") == "1" if autoreseed is None else autoreseed):
        c = dbmod.connect(path)
        stale = not dbmod.has_upcoming_shows(c)
        c.close()
        if stale: dbmod.init_db(path).close()      # demo data only: roll the sample schedule forward

    # ---------- per-request connection ----------
    def conn():
        if "db" not in g: g.db = dbmod.connect(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(_):
        c = g.pop("db", None)
        if c is not None: c.close()

    class Tx:
        """BEGIN IMMEDIATE ... COMMIT/ROLLBACK -- serialises writers so seat checks are race-free."""
        def __enter__(self): conn().execute("BEGIN IMMEDIATE"); return conn()
        def __exit__(self, et, ev, tb):
            conn().execute("ROLLBACK" if et else "COMMIT")
            return False

    def q(sql, args=()): return [dict(r) for r in conn().execute(sql, args).fetchall()]
    def q1(sql, args=()):
        r = conn().execute(sql, args).fetchone()
        return dict(r) if r else None

    @app.errorhandler(ApiError)
    def on_api_error(e): return jsonify(error=e.msg), e.code

    @app.errorhandler(413)
    def too_big(_): return jsonify(error="File too large (max 8 MB)"), 413

    @app.errorhandler(404)
    def nf(_):
        if request.path.startswith("/api/"): return jsonify(error="Not found"), 404
        return send_from_directory(os.path.join(ROOT, "frontend"), "index.html")

    @app.errorhandler(sqlite3.IntegrityError)
    def on_integrity(e): return jsonify(error=f"Database constraint violated: {e}"), 409

    def body():
        d = request.get_json(silent=True)
        if not isinstance(d, dict): raise ApiError("JSON body required")
        return d

    def num(d, k, lo=None, hi=None, integer=False):
        try: v = float(d.get(k))
        except (TypeError, ValueError): raise ApiError(f"'{k}' must be a number")
        if lo is not None and v < lo or hi is not None and v > hi: raise ApiError(f"'{k}' out of range")
        return int(v) if integer else v

    def login_required(f):
        @wraps(f)
        def w(*a, **k):
            if session.get("role") != "user": raise ApiError("Please log in", 401)
            return f(*a, **k)
        return w

    def admin_required(f):
        @wraps(f)
        def w(*a, **k):
            if session.get("role") != "admin": raise ApiError("Admin access required", 403)
            return f(*a, **k)
        return w

    uid = lambda: session["uid"]
    now_s = lambda: pricing.now_str()
    UPCOMING = "datetime(s.show_date || ' ' || s.show_time) > datetime(?)"

    # ---------- login throttling ----------
    attempts = {}
    def throttle(key):
        t = time.time(); lst = [x for x in attempts.get(key, []) if t - x < 300]
        if len(lst) >= 10: raise ApiError("Too many attempts, try again in a few minutes", 429)
        lst.append(t); attempts[key] = lst

    # ================= static / uploads =================
    @app.get("/")
    def index(): return send_from_directory(os.path.join(ROOT, "frontend"), "index.html")

    @app.get("/uploads/<path:name>")
    def uploads(name): return send_from_directory(app.config["UPLOAD_DIR"], name)

    @app.get("/<path:name>")
    def static_files(name):
        full = os.path.join(ROOT, "frontend", name)
        if os.path.isfile(full): return send_from_directory(os.path.join(ROOT, "frontend"), name)
        raise ApiError("Not found", 404)

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", movies=q1("SELECT COUNT(*) c FROM movie")["c"], time=now_s())

    # ================= auth =================
    @app.post("/api/auth/register")
    def register():
        d = body()
        name, email, pw = (d.get("full_name") or "").strip(), (d.get("email") or "").strip().lower(), d.get("password") or ""
        if len(name) < 2: raise ApiError("Name is required")
        if not EMAIL_RE.match(email): raise ApiError("Valid email required")
        if len(pw) < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
            raise ApiError("Password needs 8+ characters with letters and digits")
        phone = (d.get("phone") or "").strip() or None
        if phone and not re.fullmatch(r"\d{10}", phone): raise ApiError("Phone must be 10 digits")
        if q1("SELECT 1 x FROM users WHERE email=?", (email,)): raise ApiError("Email already registered", 409)
        with Tx() as c:
            cur = c.execute("INSERT INTO users (full_name,email,password_hash,phone,join_date) VALUES (?,?,?,?,date('now','localtime'))",
                            (name, email, generate_password_hash(pw), phone))
            uid_new = cur.lastrowid
            for gid in d.get("genres") or []:
                c.execute("INSERT OR IGNORE INTO user_genre_preference VALUES (?,?)", (uid_new, int(gid)))
            c.execute("INSERT INTO notification (user_id,message,notif_type,sent_date) VALUES (?,?,?,?)",
                      (uid_new, "Welcome to Smart Movie! Pick your favourite genres to get tailored alerts.", "SYSTEM", now_s()))
        session.clear(); session.update(uid=uid_new, role="user", name=name)
        return jsonify(user=dict(user_id=uid_new, full_name=name, email=email, role="user")), 201

    @app.post("/api/auth/login")
    def login():
        d = body(); email = (d.get("email") or "").strip().lower()
        throttle(f"u:{request.remote_addr}:{email}")
        u = q1("SELECT * FROM users WHERE email=?", (email,))
        if not u or not check_password_hash(u["password_hash"], d.get("password") or ""): raise ApiError("Invalid email or password", 401)
        session.clear(); session.update(uid=u["user_id"], role="user", name=u["full_name"])
        return jsonify(user=dict(user_id=u["user_id"], full_name=u["full_name"], email=u["email"], role="user"))

    @app.post("/api/admin/login")
    def admin_login():
        d = body(); email = (d.get("email") or "").strip().lower()
        throttle(f"a:{request.remote_addr}:{email}")
        a = q1("SELECT * FROM admins WHERE email=?", (email,))
        if not a or not check_password_hash(a["password_hash"], d.get("password") or ""): raise ApiError("Invalid admin credentials", 401)
        session.clear(); session.update(uid=a["admin_id"], role="admin", name=a["full_name"])
        return jsonify(user=dict(user_id=a["admin_id"], full_name=a["full_name"], email=a["email"], role="admin"))

    @app.post("/api/auth/logout")
    def logout(): session.clear(); return jsonify(ok=True)

    @app.get("/api/auth/me")
    def me():
        if not session.get("role"): return jsonify(user=None)
        return jsonify(user=dict(user_id=session["uid"], full_name=session["name"], role=session["role"]))

    # ================= catalogue =================
    @app.get("/api/genres")
    def genres(): return jsonify(q("SELECT * FROM genre ORDER BY genre_name"))

    def movie_rows(where="1=1", args=()):
        return q(f"""SELECT m.*, (SELECT GROUP_CONCAT(g.genre_name, ', ') FROM movie_genre mg JOIN genre g ON g.genre_id=mg.genre_id
                      WHERE mg.movie_id=m.movie_id) AS genres,
                      (SELECT COUNT(*) FROM review r WHERE r.movie_id=m.movie_id) AS review_count,
                      (SELECT COUNT(*) FROM shows s WHERE s.movie_id=m.movie_id AND {UPCOMING}) AS upcoming_shows,
                      (SELECT MIN(s.current_price) FROM shows s WHERE s.movie_id=m.movie_id AND {UPCOMING}) AS from_price
                    FROM movie m WHERE {where} ORDER BY m.avg_rating DESC, m.title""", (now_s(), now_s()) + tuple(args))

    @app.get("/api/movies")
    def movies():
        where, args = ["1=1"], []
        if request.args.get("q"):
            where.append("(m.title LIKE ? OR m.synopsis LIKE ?)"); args += [f"%{request.args['q']}%"] * 2
        if request.args.get("genre"):
            where.append("EXISTS (SELECT 1 FROM movie_genre mg WHERE mg.movie_id=m.movie_id AND mg.genre_id=?)"); args.append(request.args["genre"])
        if request.args.get("language"):
            where.append("m.movie_language=?"); args.append(request.args["language"])
        if request.args.get("min_rating"):
            where.append("m.avg_rating>=?"); args.append(float(request.args["min_rating"]))
        rows = movie_rows(" AND ".join(where), args)
        langs = [r["movie_language"] for r in q("SELECT DISTINCT movie_language FROM movie ORDER BY 1")]
        return jsonify(movies=rows, languages=langs)

    @app.get("/api/movies/<int:mid>")
    def movie_detail(mid):
        rows = movie_rows("m.movie_id=?", (mid,))
        if not rows: raise ApiError("Movie not found", 404)
        m = rows[0]
        m["in_watchlist"] = bool(session.get("role") == "user" and
                                 q1("SELECT 1 x FROM watchlist WHERE user_id=? AND movie_id=?", (uid(), mid)))
        m["can_review"] = bool(session.get("role") == "user" and q1(
            """SELECT 1 x FROM booking b JOIN shows s ON s.show_id=b.show_id WHERE b.user_id=? AND s.movie_id=? AND b.booking_status='CONFIRMED'
               AND datetime(s.show_date||' '||s.show_time) <= datetime(?)""", (uid(), mid, now_s())))
        return jsonify(m)

    @app.get("/api/movies/<int:mid>/shows")
    def movie_shows(mid):
        rows = q(f"""SELECT s.show_id, s.show_date, s.show_time, s.base_price, s.current_price, s.min_price, s.max_price,
                     t.theatre_id, t.theatre_name, t.city, t.location, sc.screen_number, sc.total_seats,
                     sc.total_seats - (SELECT COUNT(*) FROM booking_seat bs WHERE bs.show_id=s.show_id) AS seats_left
                     FROM shows s JOIN screen sc ON sc.screen_id=s.screen_id JOIN theatre t ON t.theatre_id=sc.theatre_id
                     WHERE s.movie_id=? AND {UPCOMING} ORDER BY s.show_date, s.show_time, t.theatre_name""", (mid, now_s()))
        return jsonify(rows)

    @app.get("/api/movies/<int:mid>/reviews")
    def movie_reviews(mid):
        return jsonify(q("""SELECT r.review_id, r.rating, r.review_comment, r.review_date, u.full_name FROM review r
                            JOIN users u ON u.user_id=r.user_id WHERE r.movie_id=? ORDER BY r.review_date DESC""", (mid,)))

    @app.post("/api/movies/<int:mid>/reviews")
    @login_required
    def post_review(mid):
        d = body(); rating = num(d, "rating", 1, 5, integer=True)
        comment = (d.get("comment") or "").strip()[:1000]
        if not q1("SELECT 1 x FROM movie WHERE movie_id=?", (mid,)): raise ApiError("Movie not found", 404)
        attended = q1("""SELECT 1 x FROM booking b JOIN shows s ON s.show_id=b.show_id WHERE b.user_id=? AND s.movie_id=?
                         AND b.booking_status='CONFIRMED' AND datetime(s.show_date||' '||s.show_time) <= datetime(?)""", (uid(), mid, now_s()))
        if not attended: raise ApiError("You can review a movie after attending one of its shows", 403)
        with Tx() as c:
            ex = c.execute("SELECT review_id FROM review WHERE user_id=? AND movie_id=?", (uid(), mid)).fetchone()
            if ex: c.execute("UPDATE review SET rating=?, review_comment=?, review_date=? WHERE review_id=?", (rating, comment, now_s(), ex[0]))
            else: c.execute("INSERT INTO review (user_id,movie_id,rating,review_comment,review_date) VALUES (?,?,?,?,?)", (uid(), mid, rating, comment, now_s()))
        return jsonify(movie=q1("SELECT avg_rating FROM movie WHERE movie_id=?", (mid,))), 201

    # ================= shows & seats =================
    def show_info(sid):
        s = q1("""SELECT s.*, m.title, m.movie_language, m.certification, m.duration_min, t.theatre_name, t.city, sc.screen_number, sc.total_seats
                  FROM shows s JOIN movie m ON m.movie_id=s.movie_id JOIN screen sc ON sc.screen_id=s.screen_id
                  JOIN theatre t ON t.theatre_id=sc.theatre_id WHERE s.show_id=?""", (sid,))
        if not s: raise ApiError("Show not found", 404)
        s["started"] = pricing.show_start(s) <= datetime.now()
        return s

    def seat_map(sid, screen_id):
        return q("""SELECT st.seat_id, st.seat_number, st.seat_type, st.seat_location,
                    CASE WHEN bs.seat_id IS NULL THEN 0 ELSE 1 END AS booked
                    FROM seat st LEFT JOIN booking_seat bs ON bs.seat_id=st.seat_id AND bs.show_id=?
                    WHERE st.screen_id=? ORDER BY length(st.seat_number) = 2 DESC, st.seat_number""", (sid, screen_id))

    def seat_sort_key(s): return (s["seat_number"][0], int(s["seat_number"][1:]))

    @app.get("/api/shows/<int:sid>")
    def show_detail(sid):
        s = show_info(sid)
        seats = sorted(seat_map(sid, s["screen_id"]), key=seat_sort_key)
        last = q1("SELECT * FROM demand_analysis WHERE show_id=? ORDER BY analysis_timestamp DESC LIMIT 1", (sid,))
        trend = q("SELECT change_date, old_price, new_price FROM price_history WHERE show_id=? ORDER BY change_date DESC LIMIT 1", (sid,))
        return jsonify(show=s, seats=seats, demand=last, last_change=trend[0] if trend else None)

    @app.get("/api/shows/<int:sid>/recommend")
    def recommend(sid):
        s = show_info(sid)
        n = max(1, min(int(request.args.get("count", 1) or 1), 6))
        seats = sorted(seat_map(sid, s["screen_id"]), key=seat_sort_key)
        rows = {}
        for st in seats: rows.setdefault(st["seat_number"][0], []).append(st)
        letters = sorted(rows); ideal_row = 0.6 * (len(letters) - 1)
        def score(st, ri, ncols):
            col = int(st["seat_number"][1:]); center = (ncols + 1) / 2
            return -abs(ri - ideal_row) - 0.5 * abs(col - center) / (ncols / 2) * 2 + (0.5 if st["seat_type"] == "PREMIUM" else 0)
        best = None
        for ri, L in enumerate(letters):
            row = rows[L]; ncols = len(row)
            for i in range(0, ncols - n + 1):
                grp = row[i:i + n]
                if any(x["booked"] for x in grp): continue
                sc = sum(score(x, ri, ncols) for x in grp) / n
                if best is None or sc > best[0]: best = (sc, grp)
        if best is None:      # no contiguous block: best individual free seats
            free = [(score(st, letters.index(st["seat_number"][0]), len(rows[st["seat_number"][0]])), st) for st in seats if not st["booked"]]
            free.sort(key=lambda x: -x[0])
            if len(free) < n: raise ApiError("Not enough free seats", 409)
            return jsonify(seat_ids=[x[1]["seat_id"] for x in free[:n]], seats=[x[1]["seat_number"] for x in free[:n]], contiguous=False)
        return jsonify(seat_ids=[x["seat_id"] for x in best[1]], seats=[x["seat_number"] for x in best[1]], contiguous=True)

    # ================= bookings =================
    @app.post("/api/bookings")
    @login_required
    def create_booking():
        d = body(); sid = num(d, "show_id", integer=True)
        seat_ids = d.get("seat_ids")
        if not isinstance(seat_ids, list) or not seat_ids: raise ApiError("Select at least one seat")
        try: seat_ids = sorted({int(x) for x in seat_ids})
        except (TypeError, ValueError): raise ApiError("Invalid seat ids")
        if len(seat_ids) > MAX_SEATS: raise ApiError(f"At most {MAX_SEATS} seats per booking")
        method = (d.get("payment_method") or "UPI").upper()
        if method not in METHODS: raise ApiError("Invalid payment method")
        with Tx() as c:
            s = c.execute("""SELECT s.*, m.title FROM shows s JOIN movie m ON m.movie_id=s.movie_id WHERE s.show_id=?""", (sid,)).fetchone()
            if not s: raise ApiError("Show not found", 404)
            if pricing.show_start(s) <= datetime.now(): raise ApiError("This show has already started", 409)
            ok = c.execute(f"SELECT COUNT(*) FROM seat WHERE screen_id=? AND seat_id IN ({','.join('?' * len(seat_ids))})",
                           [s["screen_id"], *seat_ids]).fetchone()[0]
            if ok != len(seat_ids): raise ApiError("Some seats do not belong to this show's screen")
            price = s["current_price"]; total = price * len(seat_ids)
            cur = c.execute("INSERT INTO booking (user_id,show_id,booking_time,total_amount,booking_status) VALUES (?,?,?,?,?)",
                            (uid(), sid, now_s(), total, "CONFIRMED"))
            bid = cur.lastrowid
            try:
                for seat in seat_ids: c.execute("INSERT INTO booking_seat (booking_id,seat_id,show_id) VALUES (?,?,?)", (bid, seat, sid))
            except sqlite3.IntegrityError as e:
                if "UNIQUE" in str(e): raise ApiError("One or more selected seats were just booked by someone else. Please pick again.", 409)
                raise
            c.execute("INSERT INTO payment (booking_id,amount,payment_date,payment_method,payment_status) VALUES (?,?,?,?,'SUCCESS')",
                      (bid, total, now_s(), method))
            c.execute("INSERT INTO notification (user_id,message,notif_type,sent_date) VALUES (?,?,?,?)",
                      (uid(), f"Booking #{bid} confirmed for {s['title']} on {s['show_date']} at {s['show_time']}.", "BOOKING", now_s()))
            pricing.recompute_show(c, sid)
        return jsonify(booking_id=bid, total_amount=total, price_per_seat=price, seats=len(seat_ids)), 201

    def my_booking_rows(where="b.user_id=?", args=None):
        return q(f"""SELECT b.booking_id, b.booking_time, b.total_amount, b.booking_status, s.show_id, s.show_date, s.show_time, m.movie_id, m.title,
                     t.theatre_name, t.city, sc.screen_number, p.payment_method, p.payment_status,
                     (SELECT GROUP_CONCAT(st.seat_number, ', ') FROM booking_seat bs JOIN seat st ON st.seat_id=bs.seat_id WHERE bs.booking_id=b.booking_id) AS seats,
                     (SELECT COUNT(*) FROM movie_memory mm WHERE mm.booking_id=b.booking_id) AS memory_count
                     FROM booking b JOIN shows s ON s.show_id=b.show_id JOIN movie m ON m.movie_id=s.movie_id
                     JOIN screen sc ON sc.screen_id=s.screen_id JOIN theatre t ON t.theatre_id=sc.theatre_id
                     LEFT JOIN payment p ON p.booking_id=b.booking_id WHERE {where} ORDER BY b.booking_time DESC""", args or (uid(),))

    @app.get("/api/bookings")
    @login_required
    def my_bookings():
        rows = my_booking_rows()
        for r in rows: r["started"] = datetime.strptime(f"{r['show_date']} {r['show_time']}", "%Y-%m-%d %H:%M") <= datetime.now()
        return jsonify(rows)

    @app.post("/api/bookings/<int:bid>/cancel")
    @login_required
    def cancel(bid):
        with Tx() as c:
            b = c.execute("SELECT b.*, s.show_date, s.show_time FROM booking b JOIN shows s ON s.show_id=b.show_id WHERE booking_id=? AND user_id=?",
                          (bid, uid())).fetchone()
            if not b: raise ApiError("Booking not found", 404)
            if b["booking_status"] == "CANCELLED": raise ApiError("Already cancelled", 409)
            if pricing.show_start(b) <= datetime.now(): raise ApiError("Cannot cancel after the show has started", 409)
            c.execute("DELETE FROM booking_seat WHERE booking_id=?", (bid,))          # frees the seats
            c.execute("UPDATE booking SET booking_status='CANCELLED' WHERE booking_id=?", (bid,))
            c.execute("UPDATE payment SET payment_status='REFUNDED' WHERE booking_id=?", (bid,))
            c.execute("INSERT INTO notification (user_id,message,notif_type,sent_date) VALUES (?,?,?,?)",
                      (uid(), f"Booking #{bid} cancelled and refunded.", "SYSTEM", now_s()))
            pricing.recompute_show(c, b["show_id"])
        return jsonify(ok=True)

    # ================= movie memories =================
    @app.post("/api/bookings/<int:bid>/memories")
    @login_required
    def add_memory(bid):
        b = q1("""SELECT b.*, s.movie_id, s.show_date, s.show_time FROM booking b JOIN shows s ON s.show_id=b.show_id
                  WHERE b.booking_id=? AND b.user_id=?""", (bid, uid()))
        if not b: raise ApiError("Booking not found", 404)
        if b["booking_status"] != "CONFIRMED": raise ApiError("Booking is cancelled", 409)
        if pricing.show_start(b) > datetime.now(): raise ApiError("Memories can be added after you have watched the movie", 403)
        f = request.files.get("file")
        if not f or not f.filename: raise ApiError("Choose a photo or video")
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in ALLOWED_MEDIA: raise ApiError("Allowed types: " + ", ".join(sorted(ALLOWED_MEDIA)))
        name = f"{uuid.uuid4().hex}.{ext}"
        f.save(os.path.join(app.config["UPLOAD_DIR"], name))
        with Tx() as c:
            cur = c.execute("INSERT INTO movie_memory (booking_id,user_id,movie_id,media_url,caption,upload_date) VALUES (?,?,?,?,?,?)",
                            (bid, uid(), b["movie_id"], f"/uploads/{name}", (request.form.get("caption") or "")[:300], now_s()))
        return jsonify(memory_id=cur.lastrowid, media_url=f"/uploads/{name}"), 201

    @app.get("/api/memories")
    @login_required
    def memories():
        return jsonify(q("""SELECT mm.*, m.title FROM movie_memory mm JOIN movie m ON m.movie_id=mm.movie_id
                            WHERE mm.user_id=? ORDER BY mm.upload_date DESC""", (uid(),)))

    @app.delete("/api/memories/<int:mid>")
    @login_required
    def del_memory(mid):
        with Tx() as c:
            n = c.execute("DELETE FROM movie_memory WHERE memory_id=? AND user_id=?", (mid, uid())).rowcount
        if not n: raise ApiError("Memory not found", 404)
        return jsonify(ok=True)

    # ================= watchlist / preferences / notifications =================
    @app.get("/api/watchlist")
    @login_required
    def get_watchlist():
        return jsonify(q(f"""SELECT w.watchlist_id, w.added_date, m.movie_id, m.title, m.movie_language, m.avg_rating,
                      (SELECT COUNT(*) FROM shows s WHERE s.movie_id=m.movie_id AND {UPCOMING}) AS upcoming_shows
                      FROM watchlist w JOIN movie m ON m.movie_id=w.movie_id WHERE w.user_id=? ORDER BY w.added_date DESC""", (now_s(), uid())))

    @app.post("/api/watchlist")
    @login_required
    def add_watchlist():
        mid = num(body(), "movie_id", integer=True)
        if not q1("SELECT 1 x FROM movie WHERE movie_id=?", (mid,)): raise ApiError("Movie not found", 404)
        with Tx() as c:
            c.execute("INSERT OR IGNORE INTO watchlist (user_id,movie_id,added_date,notified_status) VALUES (?,?,?,'N')", (uid(), mid, now_s()))
        return jsonify(ok=True), 201

    @app.delete("/api/watchlist/<int:mid>")
    @login_required
    def del_watchlist(mid):
        with Tx() as c: c.execute("DELETE FROM watchlist WHERE user_id=? AND movie_id=?", (uid(), mid))
        return jsonify(ok=True)

    @app.get("/api/preferences")
    @login_required
    def get_prefs(): return jsonify([r["genre_id"] for r in q("SELECT genre_id FROM user_genre_preference WHERE user_id=?", (uid(),))])

    @app.put("/api/preferences")
    @login_required
    def put_prefs():
        ids = body().get("genre_ids") or []
        with Tx() as c:
            c.execute("DELETE FROM user_genre_preference WHERE user_id=?", (uid(),))
            for gid in {int(x) for x in ids}: c.execute("INSERT INTO user_genre_preference VALUES (?,?)", (uid(), gid))
        return jsonify(ok=True)

    @app.get("/api/notifications")
    @login_required
    def notifications():
        return jsonify(q("SELECT * FROM notification WHERE user_id=? ORDER BY sent_date DESC, notification_id DESC LIMIT 50", (uid(),)))

    @app.post("/api/notifications/read")
    @login_required
    def read_notifs():
        with Tx() as c: c.execute("UPDATE notification SET status='READ' WHERE user_id=?", (uid(),))
        return jsonify(ok=True)

    # ================= admin =================
    @app.get("/api/admin/dashboard")
    @admin_required
    def dashboard():
        n = now_s()
        tot = q1(f"""SELECT (SELECT COUNT(*) FROM movie) movies,
                 (SELECT COUNT(*) FROM shows s WHERE {UPCOMING}) upcoming_shows,
                 (SELECT COUNT(*) FROM booking WHERE booking_status='CONFIRMED') bookings,
                 (SELECT COUNT(*) FROM booking WHERE booking_status='CANCELLED') cancelled,
                 (SELECT COALESCE(SUM(amount),0) FROM payment WHERE payment_status='SUCCESS') revenue,
                 (SELECT COUNT(*) FROM users) users""", (n,))
        shows = q(f"""SELECT s.show_id, m.title, t.theatre_name, sc.screen_number, s.show_date, s.show_time, s.base_price, s.current_price,
                      s.min_price, s.max_price, sc.total_seats,
                      (SELECT COUNT(*) FROM booking_seat bs WHERE bs.show_id=s.show_id) booked,
                      (SELECT demand_score FROM demand_analysis d WHERE d.show_id=s.show_id ORDER BY analysis_timestamp DESC LIMIT 1) demand_score,
                      datetime(s.show_date || ' ' || s.show_time) > datetime(?) AS upcoming
                      FROM shows s JOIN movie m ON m.movie_id=s.movie_id JOIN screen sc ON sc.screen_id=s.screen_id
                      JOIN theatre t ON t.theatre_id=sc.theatre_id ORDER BY s.show_date, s.show_time, t.theatre_name""", (n,))
        for s in shows: s["occupancy"] = round(s["booked"] / s["total_seats"], 3)
        up = [s for s in shows if s["upcoming"]]
        tot["avg_occupancy"] = round(sum(s["occupancy"] for s in up) / len(up), 3) if up else 0
        by_movie = q("""SELECT m.title, COALESCE(SUM(CASE WHEN p.payment_status='SUCCESS' THEN p.amount END),0) revenue, COUNT(b.booking_id) bookings
                        FROM movie m LEFT JOIN shows s ON s.movie_id=m.movie_id LEFT JOIN booking b ON b.show_id=s.show_id AND b.booking_status='CONFIRMED'
                        LEFT JOIN payment p ON p.booking_id=b.booking_id GROUP BY m.movie_id ORDER BY revenue DESC""")
        changes = q("""SELECT ph.*, m.title, s.show_date, s.show_time FROM price_history ph JOIN shows s ON s.show_id=ph.show_id
                       JOIN movie m ON m.movie_id=s.movie_id ORDER BY ph.change_date DESC, ph.price_id DESC LIMIT 12""")
        return jsonify(totals=tot, shows=shows, revenue_by_movie=by_movie, price_changes=changes)

    @app.get("/api/admin/shows/<int:sid>/pricing")
    @admin_required
    def show_pricing(sid):
        show_info(sid)
        return jsonify(demand=q("SELECT * FROM demand_analysis WHERE show_id=? ORDER BY analysis_timestamp DESC LIMIT 20", (sid,)),
                       history=q("SELECT * FROM price_history WHERE show_id=? ORDER BY change_date DESC", (sid,)))

    @app.post("/api/admin/pricing/run")
    @admin_required
    def run_pricing():
        return jsonify(repriced=pricing.recompute_all(conn()))

    @app.get("/api/admin/pricing-config")
    @admin_required
    def get_cfg(): return jsonify(config=dict(pricing.latest_config(conn())),
                                  history=q("SELECT * FROM pricing_config ORDER BY updated_at DESC, config_id DESC LIMIT 10"))

    @app.put("/api/admin/pricing-config")
    @admin_required
    def put_cfg():
        d = body()
        w = [num(d, k, 0, 1) for k in ("w_occupancy", "w_booking_speed", "w_popularity", "w_urgency")]
        if abs(sum(w) - 1) > 0.0001: raise ApiError("Weights must sum to 1")
        k = num(d, "elasticity_k", 0.01, 2); win = num(d, "booking_window_hours", 1, 720, integer=True)
        with Tx() as c:      # new row => history of changes is kept
            c.execute("""INSERT INTO pricing_config (admin_id,w_occupancy,w_booking_speed,w_popularity,w_urgency,elasticity_k,booking_window_hours,updated_at)
                         VALUES (?,?,?,?,?,?,?,?)""", (uid(), *w, k, win, pricing.now_str()))
        pricing.recompute_all(conn())
        return jsonify(ok=True)

    def check_movie(d):
        t = (d.get("title") or "").strip()
        if not t: raise ApiError("Title required")
        cert = d.get("certification", "UA")
        if cert not in ("U", "UA", "A"): raise ApiError("Certification must be U, UA or A")
        return (t, (d.get("movie_language") or "").strip() or "English", cert, num(d, "duration_min", 1, 600, integer=True),
                (d.get("release_date") or datetime.now().strftime("%Y-%m-%d")), (d.get("synopsis") or "")[:1000])

    @app.post("/api/admin/movies")
    @admin_required
    def admin_add_movie():
        d = body(); vals = check_movie(d); gids = [int(x) for x in d.get("genre_ids") or []]
        with Tx() as c:
            mid = c.execute("INSERT INTO movie (title,movie_language,certification,duration_min,release_date,synopsis) VALUES (?,?,?,?,?,?)", vals).lastrowid
            for g_ in gids: c.execute("INSERT OR IGNORE INTO movie_genre VALUES (?,?)", (mid, g_))
            if gids:   # new-release notifications to users following these genres
                us = c.execute(f"SELECT DISTINCT user_id FROM user_genre_preference WHERE genre_id IN ({','.join('?'*len(gids))})", gids).fetchall()
                for u in us:
                    c.execute("INSERT INTO notification (user_id,message,notif_type,sent_date) VALUES (?,?,?,?)",
                              (u[0], f"New release in a genre you follow: {vals[0]}", "NEW_RELEASE", now_s()))
        return jsonify(movie_id=mid), 201

    @app.put("/api/admin/movies/<int:mid>")
    @admin_required
    def admin_edit_movie(mid):
        d = body(); vals = check_movie(d)
        with Tx() as c:
            n = c.execute("UPDATE movie SET title=?,movie_language=?,certification=?,duration_min=?,release_date=?,synopsis=? WHERE movie_id=?", (*vals, mid)).rowcount
            if not n: raise ApiError("Movie not found", 404)
            if "genre_ids" in d:
                c.execute("DELETE FROM movie_genre WHERE movie_id=?", (mid,))
                for g_ in d["genre_ids"]: c.execute("INSERT OR IGNORE INTO movie_genre VALUES (?,?)", (mid, int(g_)))
        return jsonify(ok=True)

    @app.delete("/api/admin/movies/<int:mid>")
    @admin_required
    def admin_del_movie(mid):
        if q1("SELECT 1 x FROM shows WHERE movie_id=?", (mid,)): raise ApiError("Movie has shows; delete or keep them first", 409)
        with Tx() as c: c.execute("DELETE FROM movie WHERE movie_id=?", (mid,))
        return jsonify(ok=True)

    @app.get("/api/admin/theatres")
    @admin_required
    def admin_theatres():
        th = q("SELECT * FROM theatre ORDER BY theatre_name")
        for t in th: t["screens"] = q("SELECT * FROM screen WHERE theatre_id=? ORDER BY screen_number", (t["theatre_id"],))
        return jsonify(th)

    @app.post("/api/admin/theatres")
    @admin_required
    def admin_add_theatre():
        d = body(); vals = [(d.get(k) or "").strip() for k in ("theatre_name", "location", "city")]
        if not all(vals): raise ApiError("Name, location and city required")
        with Tx() as c: tid = c.execute("INSERT INTO theatre (theatre_name,location,city) VALUES (?,?,?)", vals).lastrowid
        return jsonify(theatre_id=tid), 201

    @app.post("/api/admin/screens")
    @admin_required
    def admin_add_screen():
        d = body(); tid = num(d, "theatre_id", integer=True); rows_ = num(d, "rows", 1, 26, integer=True); cols = num(d, "cols", 1, 30, integer=True)
        with Tx() as c:
            if not c.execute("SELECT 1 FROM theatre WHERE theatre_id=?", (tid,)).fetchone(): raise ApiError("Theatre not found", 404)
            nxt = c.execute("SELECT COALESCE(MAX(screen_number),0)+1 FROM screen WHERE theatre_id=?", (tid,)).fetchone()[0]
            sid = c.execute("INSERT INTO screen (theatre_id,screen_number,total_seats) VALUES (?,?,?)", (tid, nxt, rows_ * cols)).lastrowid
            for r in range(rows_):
                frac = r / rows_; loc = "FRONT" if frac < .3 else ("MIDDLE" if frac < .75 else "BACK")
                for col in range(1, cols + 1):
                    typ = "PREMIUM" if loc == "MIDDLE" and 3 <= col <= cols - 2 else "STANDARD"
                    c.execute("INSERT INTO seat (screen_id,seat_number,seat_type,seat_location) VALUES (?,?,?,?)", (sid, f"{chr(65 + r)}{col}", typ, loc))
        return jsonify(screen_id=sid, seats=rows_ * cols), 201

    def check_show(d):
        base = num(d, "base_price", 1); lo = num(d, "min_price", 1); hi = num(d, "max_price", 1)
        if not lo <= base <= hi: raise ApiError("Require min_price <= base_price <= max_price")
        tm = d.get("show_time") or ""
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", tm): raise ApiError("show_time must be HH:MM")
        try: datetime.strptime(d.get("show_date") or "", "%Y-%m-%d")
        except ValueError: raise ApiError("show_date must be YYYY-MM-DD")
        return base, lo, hi, d["show_date"], tm

    @app.post("/api/admin/shows")
    @admin_required
    def admin_add_show():
        d = body(); base, lo, hi, dt, tm = check_show(d)
        mid, scr = num(d, "movie_id", integer=True), num(d, "screen_id", integer=True)
        with Tx() as c:
            try:
                sid = c.execute("""INSERT INTO shows (movie_id,screen_id,admin_id,show_date,show_time,base_price,current_price,min_price,max_price)
                                   VALUES (?,?,?,?,?,?,?,?,?)""", (mid, scr, uid(), dt, tm, base, base, lo, hi)).lastrowid
            except sqlite3.IntegrityError as e:
                raise ApiError("That screen already has a show at that date/time" if "UNIQUE" in str(e) else f"Invalid movie/screen: {e}", 409)
            title = c.execute("SELECT title FROM movie WHERE movie_id=?", (mid,)).fetchone()[0]
            for w in c.execute("SELECT watchlist_id, user_id FROM watchlist WHERE movie_id=? AND notified_status='N'", (mid,)).fetchall():
                c.execute("INSERT INTO notification (user_id,message,notif_type,sent_date) VALUES (?,?,?,?)",
                          (w[1], f"{title} on your watchlist now has shows on {dt}. Book early for the best price!", "WATCHLIST", now_s()))
                c.execute("UPDATE watchlist SET notified_status='Y' WHERE watchlist_id=?", (w[0],))
            pricing.recompute_show(c, sid)
        return jsonify(show_id=sid), 201

    @app.put("/api/admin/shows/<int:sid>")
    @admin_required
    def admin_edit_show(sid):
        d = body(); base, lo, hi, _, _ = check_show({**d, "show_date": "2000-01-01", "show_time": "00:00"})
        with Tx() as c:
            s = c.execute("SELECT current_price FROM shows WHERE show_id=?", (sid,)).fetchone()
            if not s: raise ApiError("Show not found", 404)
            cur = min(hi, max(lo, s[0]))
            c.execute("UPDATE shows SET base_price=?, min_price=?, max_price=?, current_price=? WHERE show_id=?", (base, lo, hi, cur, sid))
            if cur != s[0]:
                c.execute("INSERT INTO price_history (show_id,change_date,old_price,new_price,change_reason) VALUES (?,?,?,?,?)",
                          (sid, now_s(), s[0], cur, "admin_bounds_changed"))
            pricing.recompute_show(c, sid)
        return jsonify(ok=True)

    @app.delete("/api/admin/shows/<int:sid>")
    @admin_required
    def admin_del_show(sid):
        if q1("SELECT 1 x FROM booking WHERE show_id=?", (sid,)): raise ApiError("Show has bookings and cannot be deleted", 409)
        with Tx() as c: c.execute("DELETE FROM shows WHERE show_id=?", (sid,))
        return jsonify(ok=True)

    @app.get("/api/admin/users")
    @admin_required
    def admin_users():
        return jsonify(users=q("""SELECT u.user_id, u.full_name, u.email, u.phone, u.join_date,
                                  (SELECT COUNT(*) FROM booking b WHERE b.user_id=u.user_id AND b.booking_status='CONFIRMED') bookings FROM users u ORDER BY u.user_id"""),
                       admins=q("SELECT admin_id, full_name, email FROM admins"))

    @app.post("/api/admin/admins")
    @admin_required
    def admin_add_admin():
        d = body(); email = (d.get("email") or "").strip().lower()
        if not EMAIL_RE.match(email) or len(d.get("password") or "") < 8: raise ApiError("Valid email and 8+ char password required")
        with Tx() as c:
            c.execute("INSERT INTO admins (full_name,email,password_hash) VALUES (?,?,?)", ((d.get("full_name") or "Admin").strip(), email, generate_password_hash(d["password"])))
        return jsonify(ok=True), 201

    # ================= background jobs =================
    def run_jobs():
        c = dbmod.connect(app.config["DB_PATH"])
        try:
            pricing.recompute_all(c)
            soon = (datetime.now() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")
            c.execute("BEGIN IMMEDIATE")
            for b in c.execute("""SELECT b.booking_id, b.user_id, m.title, s.show_time FROM booking b JOIN shows s ON s.show_id=b.show_id
                  JOIN movie m ON m.movie_id=s.movie_id WHERE b.booking_status='CONFIRMED'
                  AND datetime(s.show_date||' '||s.show_time) > datetime('now','localtime') AND datetime(s.show_date||' '||s.show_time) <= datetime(?)""", (soon,)).fetchall():
                tag = f"Reminder: Booking #{b['booking_id']}"
                if not c.execute("SELECT 1 FROM notification WHERE user_id=? AND message LIKE ?", (b["user_id"], tag + "%")).fetchone():
                    c.execute("INSERT INTO notification (user_id,message,notif_type,sent_date) VALUES (?,?,?,?)",
                              (b["user_id"], f"{tag} - {b['title']} starts at {b['show_time']}.", "REMINDER", now_s()))
            c.execute("COMMIT")
        finally: c.close()
    app.run_jobs = run_jobs

    @app.post("/api/admin/jobs/run")
    @admin_required
    def jobs_endpoint(): run_jobs(); return jsonify(ok=True)

    if start_scheduler is None: start_scheduler = os.environ.get("ENABLE_SCHEDULER", "1") == "1"
    if start_scheduler:
        def loop():
            while True:
                time.sleep(15 * 60)
                try: run_jobs()
                except Exception as e: print("scheduler error:", e)
        threading.Thread(target=loop, daemon=True).start()
    return app


app = None
def get_app():
    global app
    if app is None: app = create_app()
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    create_app().run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
