"""Automated tests: schema constraints, pricing engine, API behaviour, double-booking race.
Run:  python -m unittest discover -s tests -v
"""
import io, os, sqlite3, sys, tempfile, threading, unittest
from datetime import datetime, timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import db as dbmod, pricing
from app import create_app

PW = "Password@123"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path = os.path.join(cls.tmp, "t.db")
        cls.app = create_app(cls.path, start_scheduler=False)

    def conn(self): return dbmod.connect(self.path)
    def client(self, email=None, pw=PW, admin=False):
        c = self.app.test_client()
        if email: self.assertEqual(c.post("/api/admin/login" if admin else "/api/auth/login", json=dict(email=email, password=pw)).status_code, 200)
        return c
    def upcoming_show(self, min_free=8):
        for s in self.conn().execute("""SELECT s.show_id, s.screen_id FROM shows s WHERE datetime(show_date||' '||show_time) > datetime('now','localtime','+30 minutes')
                                        ORDER BY show_id""").fetchall():
            free = self.conn().execute("""SELECT seat_id FROM seat WHERE screen_id=? AND seat_id NOT IN (SELECT seat_id FROM booking_seat WHERE show_id=?) ORDER BY seat_id""",
                                       (s["screen_id"], s["show_id"])).fetchall()
            if len(free) >= min_free: return s["show_id"], [f[0] for f in free]
        self.fail("no upcoming show")


class TestPricingFormula(unittest.TestCase):
    CFG = dict(w_occupancy=.40, w_booking_speed=.25, w_popularity=.15, w_urgency=.20, elasticity_k=.60)

    def test_report_worked_example(self):          # Review-0 section 5.6
        score, mult, raw, price = pricing.compute_price(0.72, 0.60, 0.50, 1 - 2 / 24, self.CFG, 250, 200, 400)
        self.assertAlmostEqual(score, 0.696, places=3)
        self.assertEqual(mult, 1.118); self.assertEqual(raw, 280); self.assertEqual(price, 280)

    def test_neutral_demand_keeps_base(self):
        self.assertEqual(pricing.compute_price(.5, .5, .5, .5, self.CFG, 250, 200, 400)[3], 250)

    def test_extremes_and_clamping(self):
        self.assertEqual(pricing.compute_price(1, 1, 1, 1, self.CFG, 250, 200, 400)[1], 1.3)     # +30%
        self.assertEqual(pricing.compute_price(0, 0, 0, 0, self.CFG, 250, 200, 400)[1], 0.7)     # -30%
        _, _, raw, price = pricing.compute_price(1, 1, 1, 1, self.CFG, 250, 200, 300)            # raw 325 -> capped at 300
        self.assertEqual((raw, price), (325, 300))
        _, _, raw, price = pricing.compute_price(0, 0, 0, 0, self.CFG, 250, 190, 400)            # raw 175 -> raised to 190
        self.assertEqual((raw, price), (175, 190))


class TestSchema(Base):
    def test_twenty_tables(self):
        names = {r[0] for r in self.conn().execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(len(names), 20, names)

    def test_double_booking_blocked_by_unique_constraint(self):
        c = self.conn(); sid, seats = self.upcoming_show()
        for b in (900001, 900002):
            c.execute("INSERT INTO booking (booking_id,user_id,show_id,booking_time,total_amount,booking_status) VALUES (?,1,?,?,100,'CONFIRMED')", (b, sid, "2026-01-01 10:00:00"))
        c.execute("INSERT INTO booking_seat VALUES (900001,?,?)", (seats[0], sid))
        with self.assertRaises(sqlite3.IntegrityError): c.execute("INSERT INTO booking_seat VALUES (900002,?,?)", (seats[0], sid))
        c.execute("DELETE FROM booking WHERE booking_id IN (900001,900002)")

    def test_price_band_check(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn().execute("UPDATE shows SET current_price = max_price + 1 WHERE show_id=1")

    def test_weights_must_sum_to_one(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn().execute("INSERT INTO pricing_config (admin_id,w_occupancy,w_booking_speed,w_popularity,w_urgency,elasticity_k,booking_window_hours,updated_at) VALUES (1,.5,.5,.5,.5,.6,24,'x')")

    def test_one_payment_per_booking(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn().execute("INSERT INTO payment (booking_id,amount,payment_date,payment_method,payment_status) VALUES (1,1,'x','UPI','SUCCESS')")

    def test_seat_must_belong_to_show_screen(self):
        c = self.conn(); sid, _ = self.upcoming_show()
        other = c.execute("SELECT seat_id FROM seat WHERE screen_id <> (SELECT screen_id FROM shows WHERE show_id=?) LIMIT 1", (sid,)).fetchone()[0]
        c.execute("INSERT INTO booking (booking_id,user_id,show_id,booking_time,total_amount,booking_status) VALUES (900003,1,?,?,1,'CONFIRMED')", (sid, "2026-01-01 10:00:00"))
        with self.assertRaises(sqlite3.DatabaseError): c.execute("INSERT INTO booking_seat VALUES (900003,?,?)", (other, sid))
        c.execute("DELETE FROM booking WHERE booking_id=900003")

    def test_rating_trigger(self):
        c = self.conn(); c.execute("INSERT INTO movie (movie_id,title,movie_language,certification,duration_min,release_date) VALUES (9001,'T','English','U',90,'2020-01-01')")
        for u, r in ((1, 5), (2, 4)): c.execute("INSERT INTO review (user_id,movie_id,rating,review_comment,review_date) VALUES (?,9001,?,'x','x')", (u, r))
        self.assertEqual(c.execute("SELECT avg_rating FROM movie WHERE movie_id=9001").fetchone()[0], 4.5)
        c.execute("DELETE FROM movie WHERE movie_id=9001")


class TestPricingEngine(Base):
    def test_all_prices_inside_bounds_and_logged(self):
        c = self.conn()
        n = pricing.recompute_all(c)
        self.assertGreater(n, 10)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM shows WHERE current_price<min_price OR current_price>max_price").fetchone()[0], 0)
        self.assertGreaterEqual(c.execute("SELECT COUNT(*) FROM demand_analysis").fetchone()[0], n)
        for h in c.execute("SELECT * FROM price_history").fetchall():
            self.assertNotEqual(h["old_price"], h["new_price"])

    def test_booking_raises_occupancy_signal(self):
        sid, seats = self.upcoming_show(20); cl = self.client("rohan.iyer@example.com")
        before = cl.get(f"/api/shows/{sid}").json["demand"]["occupancy_rate"]
        self.assertEqual(cl.post("/api/bookings", json=dict(show_id=sid, seat_ids=seats[:6], payment_method="CARD")).status_code, 201)
        after = cl.get(f"/api/shows/{sid}").json["demand"]["occupancy_rate"]
        self.assertGreater(after, before)


class TestApi(Base):
    def test_register_validation_and_login(self):
        c = self.app.test_client()
        self.assertEqual(c.post("/api/auth/register", json=dict(full_name="A", email="bad", password="x")).status_code, 400)
        self.assertEqual(c.post("/api/auth/register", json=dict(full_name="New User", email="new.user@example.com", password="abcdefgh")).status_code, 400)  # no digit
        r = c.post("/api/auth/register", json=dict(full_name="New User", email="new.user@example.com", password="Strong123"))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(c.post("/api/auth/register", json=dict(full_name="New User", email="new.user@example.com", password="Strong123")).status_code, 409)
        self.assertEqual(self.app.test_client().post("/api/auth/login", json=dict(email="new.user@example.com", password="wrong")).status_code, 401)
        self.assertEqual(self.app.test_client().post("/api/auth/login", json=dict(email="new.user@example.com", password="Strong123")).status_code, 200)

    def test_passwords_are_salted_hashes(self):
        rows = self.conn().execute("SELECT password_hash FROM users").fetchall()
        self.assertEqual(len({r[0] for r in rows}), len(rows))            # same password, different salts
        self.assertTrue(all(PW not in r[0] for r in rows))

    def test_auth_required_and_rbac(self):
        anon = self.app.test_client()
        self.assertEqual(anon.post("/api/bookings", json={}).status_code, 401)
        self.assertEqual(anon.get("/api/watchlist").status_code, 401)
        user = self.client("aarav.sharma@example.com")
        self.assertEqual(user.get("/api/admin/dashboard").status_code, 403)
        self.assertEqual(self.client("admin@smartmovie.local", "Admin@123", admin=True).get("/api/admin/dashboard").status_code, 200)

    def test_search_and_filters(self):
        c = self.app.test_client()
        self.assertEqual([m["title"] for m in c.get("/api/movies?q=inception").json["movies"]], ["Inception"])
        langs = {m["movie_language"] for m in c.get("/api/movies?language=Tamil").json["movies"]}
        self.assertEqual(langs, {"Tamil"})
        self.assertTrue(all(m["avg_rating"] >= 4 for m in c.get("/api/movies?min_rating=4").json["movies"]))
        gid = c.get("/api/genres").json[0]["genre_id"]
        self.assertTrue(c.get(f"/api/movies?genre={gid}").json["movies"])

    def test_booking_flow_double_booking_and_cancel(self):
        sid, seats = self.upcoming_show(); a = self.client("meera.nair@example.com"); b = self.client("arjun.verma@example.com")
        r = a.post("/api/bookings", json=dict(show_id=sid, seat_ids=seats[:2], payment_method="UPI")); self.assertEqual(r.status_code, 201)
        bid = r.json["booking_id"]
        r2 = b.post("/api/bookings", json=dict(show_id=sid, seat_ids=[seats[1], seats[2]], payment_method="UPI"))
        self.assertEqual(r2.status_code, 409)                                         # overlapping seat rejected
        booked = {s["seat_id"] for s in a.get(f"/api/shows/{sid}").json["seats"] if s["booked"]}
        self.assertNotIn(seats[2], booked)                                            # failed booking left no partial rows (atomic)
        self.assertEqual(a.post(f"/api/bookings/{bid}/cancel").status_code, 200)
        self.assertEqual(b.post("/api/bookings", json=dict(show_id=sid, seat_ids=[seats[1]], payment_method="WALLET")).status_code, 201)   # freed
        self.assertEqual(a.post(f"/api/bookings/{bid}/cancel").status_code, 409)     # cannot cancel twice

    def test_invalid_booking_inputs(self):
        sid, seats = self.upcoming_show(); a = self.client("diya.patel@example.com")
        self.assertEqual(a.post("/api/bookings", json=dict(show_id=sid, seat_ids=[])).status_code, 400)
        self.assertEqual(a.post("/api/bookings", json=dict(show_id=sid, seat_ids=seats[:2], payment_method="BITCOIN")).status_code, 400)
        other = self.conn().execute("SELECT seat_id FROM seat WHERE screen_id <> (SELECT screen_id FROM shows WHERE show_id=?) LIMIT 1", (sid,)).fetchone()[0]
        self.assertEqual(a.post("/api/bookings", json=dict(show_id=sid, seat_ids=[other])).status_code, 400)
        self.assertEqual(a.post("/api/bookings", json=dict(show_id=999999, seat_ids=[1])).status_code, 404)

    def test_cannot_book_started_show(self):
        past = self.conn().execute("SELECT show_id, screen_id FROM shows WHERE datetime(show_date||' '||show_time) < datetime('now','localtime') LIMIT 1").fetchone()
        seat = self.conn().execute("SELECT seat_id FROM seat WHERE screen_id=? LIMIT 1", (past["screen_id"],)).fetchone()[0]
        self.assertEqual(self.client("diya.patel@example.com").post("/api/bookings", json=dict(show_id=past["show_id"], seat_ids=[seat])).status_code, 409)

    def test_concurrent_booking_same_seat_only_one_wins(self):
        sid, seats = self.upcoming_show(); target = seats[3]
        emails = ["aarav.sharma", "diya.patel", "rohan.iyer", "meera.nair", "karthik.reddy", "ananya.krishnan", "vikram.singh", "sneha.menon"]
        clients = [self.client(e + "@example.com") for e in emails]; results = []; gate = threading.Barrier(len(clients))
        def go(cl):
            gate.wait(); results.append(cl.post("/api/bookings", json=dict(show_id=sid, seat_ids=[target], payment_method="UPI")).status_code)
        ts = [threading.Thread(target=go, args=(c,)) for c in clients]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sorted(results).count(201), 1, results)
        self.assertEqual(sorted(results).count(409), len(clients) - 1, results)
        n = self.conn().execute("SELECT COUNT(*) FROM booking_seat WHERE show_id=? AND seat_id=?", (sid, target)).fetchone()[0]
        self.assertEqual(n, 1)

    def test_recommendation_returns_free_adjacent_seats(self):
        sid, _ = self.upcoming_show(); cl = self.app.test_client()
        r = cl.get(f"/api/shows/{sid}/recommend?count=3").json
        self.assertEqual(len(r["seat_ids"]), 3); self.assertTrue(r["contiguous"])
        free = {s["seat_id"] for s in cl.get(f"/api/shows/{sid}").json["seats"] if not s["booked"]}
        self.assertTrue(set(r["seat_ids"]) <= free)

    def _attended(self):
        r = self.conn().execute("""SELECT b.user_id, b.booking_id, s.movie_id, u.email FROM booking b JOIN shows s ON s.show_id=b.show_id JOIN users u ON u.user_id=b.user_id
            WHERE b.booking_status='CONFIRMED' AND datetime(s.show_date||' '||s.show_time) < datetime('now','localtime') LIMIT 1""").fetchone()
        return r

    def test_review_rules_and_rating_update(self):
        att = self._attended(); cl = self.client(att["email"])
        self.assertEqual(cl.post(f"/api/movies/{att['movie_id']}/reviews", json=dict(rating=4, comment="Good")).status_code, 201)
        self.assertEqual(cl.post(f"/api/movies/{att['movie_id']}/reviews", json=dict(rating=5, comment="Better")).status_code, 201)   # upsert
        self.assertEqual(self.conn().execute("SELECT COUNT(*) FROM review WHERE user_id=? AND movie_id=?", (att["user_id"], att["movie_id"])).fetchone()[0], 1)
        self.assertEqual(cl.post(f"/api/movies/{att['movie_id']}/reviews", json=dict(rating=9)).status_code, 400)
        sid = self.upcoming_show()[0]; mid = self.conn().execute("SELECT movie_id FROM shows WHERE show_id=?", (sid,)).fetchone()[0]
        newbie = self.client("lakshmi.narayanan@example.com")
        if not self.conn().execute("""SELECT 1 FROM booking b JOIN shows s ON s.show_id=b.show_id WHERE b.user_id=12 AND s.movie_id=? AND datetime(s.show_date||' '||s.show_time)<datetime('now','localtime')""", (mid,)).fetchone():
            self.assertEqual(newbie.post(f"/api/movies/{mid}/reviews", json=dict(rating=3)).status_code, 403)    # not attended

    def test_watchlist_and_preferences_and_notifications(self):
        cl = self.client("priya.raman@example.com")
        self.assertEqual(cl.post("/api/watchlist", json=dict(movie_id=3)).status_code, 201)
        self.assertEqual(cl.post("/api/watchlist", json=dict(movie_id=3)).status_code, 201)       # idempotent
        self.assertIn(3, [w["movie_id"] for w in cl.get("/api/watchlist").json])
        self.assertEqual(cl.delete("/api/watchlist/3").status_code, 200)
        self.assertNotIn(3, [w["movie_id"] for w in cl.get("/api/watchlist").json])
        self.assertEqual(cl.put("/api/preferences", json=dict(genre_ids=[1, 2])).status_code, 200)
        self.assertEqual(sorted(cl.get("/api/preferences").json), [1, 2])
        self.assertEqual(cl.post("/api/notifications/read").status_code, 200)
        self.assertTrue(all(n["status"] == "READ" for n in cl.get("/api/notifications").json))

    def test_memory_upload(self):
        att = self._attended(); cl = self.client(att["email"])
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        r = cl.post(f"/api/bookings/{att['booking_id']}/memories", data=dict(file=(io.BytesIO(png), "m.png"), caption="Night out"), content_type="multipart/form-data")
        self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(self.app.test_client().get(r.json["media_url"]).status_code, 200)
        bad = cl.post(f"/api/bookings/{att['booking_id']}/memories", data=dict(file=(io.BytesIO(b"x"), "evil.exe")), content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 400)
        sid, seats = self.upcoming_show(); fut = cl.post("/api/bookings", json=dict(show_id=sid, seat_ids=[seats[-1]])).json["booking_id"]
        self.assertEqual(cl.post(f"/api/bookings/{fut}/memories", data=dict(file=(io.BytesIO(png), "m.png")), content_type="multipart/form-data").status_code, 403)

    def test_xss_payload_stored_verbatim_not_executable_server_side(self):
        att = self._attended(); cl = self.client(att["email"])
        cl.post(f"/api/movies/{att['movie_id']}/reviews", json=dict(rating=3, comment="<script>alert(1)</script>"))
        rv = self.app.test_client().get(f"/api/movies/{att['movie_id']}/reviews")
        self.assertEqual(rv.content_type, "application/json")      # served as JSON; UI escapes on render (esc())

    def test_sql_injection_attempt_is_inert(self):
        c = self.app.test_client()
        self.assertEqual(c.post("/api/auth/login", json=dict(email="' OR 1=1 --", password="x")).status_code, 401)
        r = c.get("/api/movies?q=' OR '1'='1"); self.assertEqual(r.status_code, 200); self.assertEqual(r.json["movies"], [])

    def test_admin_crud_pricing_config_and_notifications(self):
        ad = self.client("admin@smartmovie.local", "Admin@123", admin=True)
        gid = ad.get("/api/genres").json[0]["genre_id"]
        u = self.client("sneha.menon@example.com"); u.put("/api/preferences", json=dict(genre_ids=[gid]))
        mid = ad.post("/api/admin/movies", json=dict(title="Test Film", movie_language="English", certification="U", duration_min=100, genre_ids=[gid])).json["movie_id"]
        self.assertTrue(any("Test Film" in n["message"] for n in u.get("/api/notifications").json))        # new-release alert
        u.post("/api/watchlist", json=dict(movie_id=mid))
        scr = self.conn().execute("SELECT screen_id FROM screen LIMIT 1").fetchone()[0]
        day = (datetime.now() + timedelta(days=9)).strftime("%Y-%m-%d")
        r = ad.post("/api/admin/shows", json=dict(movie_id=mid, screen_id=scr, show_date=day, show_time="12:00", base_price=250, min_price=200, max_price=400))
        self.assertEqual(r.status_code, 201)
        self.assertTrue(any("Test Film" in n["message"] and n["notif_type"] == "WATCHLIST" for n in u.get("/api/notifications").json))   # watchlist alert
        self.assertEqual(ad.post("/api/admin/shows", json=dict(movie_id=mid, screen_id=scr, show_date=day, show_time="12:00", base_price=250, min_price=200, max_price=400)).status_code, 409)
        self.assertEqual(ad.post("/api/admin/shows", json=dict(movie_id=mid, screen_id=scr, show_date=day, show_time="13:00", base_price=250, min_price=300, max_price=400)).status_code, 400)
        self.assertEqual(ad.delete(f"/api/admin/movies/{mid}").status_code, 409)           # has shows
        self.assertEqual(ad.delete(f"/api/admin/shows/{r.json['show_id']}").status_code, 200)
        self.assertEqual(ad.delete(f"/api/admin/movies/{mid}").status_code, 200)
        cfg = dict(w_occupancy=.4, w_booking_speed=.25, w_popularity=.15, w_urgency=.2, elasticity_k=.6, booking_window_hours=24)
        self.assertEqual(ad.put("/api/admin/pricing-config", json={**cfg, "w_urgency": .5}).status_code, 400)   # sum != 1
        before = len(ad.get("/api/admin/pricing-config").json["history"])
        self.assertEqual(ad.put("/api/admin/pricing-config", json={**cfg, "elasticity_k": .8}).status_code, 200)
        self.assertEqual(len(ad.get("/api/admin/pricing-config").json["history"]), before + 1)      # history kept

    def test_admin_screen_generates_seats(self):
        ad = self.client("admin@smartmovie.local", "Admin@123", admin=True)
        tid = ad.post("/api/admin/theatres", json=dict(theatre_name="Test Hall", location="X", city="Y")).json["theatre_id"]
        r = ad.post("/api/admin/screens", json=dict(theatre_id=tid, rows=4, cols=6)); self.assertEqual(r.status_code, 201)
        self.assertEqual(self.conn().execute("SELECT COUNT(*) FROM seat WHERE screen_id=?", (r.json["screen_id"],)).fetchone()[0], 24)

    def test_dashboard_numbers_consistent_with_database(self):
        ad = self.client("admin@smartmovie.local", "Admin@123", admin=True); d = ad.get("/api/admin/dashboard").json
        c = self.conn()
        self.assertEqual(d["totals"]["bookings"], c.execute("SELECT COUNT(*) FROM booking WHERE booking_status='CONFIRMED'").fetchone()[0])
        self.assertAlmostEqual(d["totals"]["revenue"], c.execute("SELECT SUM(amount) FROM payment WHERE payment_status='SUCCESS'").fetchone()[0], places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
