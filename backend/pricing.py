"""
Dynamic pricing engine (Review-0 section 5). Mirrors pkg_dynamic_pricing in database/oracle/02_plsql.sql.

  O = seats_booked / total_seats
  S = min(1, bookings_last_60min / speed_reference)      (reference = busiest hour for that capacity class, floor 5)
  P = 0.6*(avg_rating/5) + 0.4*min(1, watchlist_count / popularity_reference)   (reference floor 5)
  U = 1 - min(1, hours_remaining / booking_window_hours)
  demand_score = wO*O + wS*S + wP*P + wU*U
  multiplier   = round(1 + k*(demand_score-0.5), 3)
  current_price= CLAMP(round_half_up(base*multiplier), min_price, max_price)
"""
from datetime import datetime, timedelta
import math

SPEED_FLOOR = 5
WATCHLIST_FLOOR = 5


def now_str(dt=None):
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def compute_price(o, s, p, u, cfg, base, pmin, pmax):
    """Pure function: signals -> (score, multiplier, raw_price, final_price). Used by tests and the engine."""
    score = cfg["w_occupancy"] * o + cfg["w_booking_speed"] * s + cfg["w_popularity"] * p + cfg["w_urgency"] * u
    score = min(1.0, max(0.0, score))
    mult = round(1 + cfg["elasticity_k"] * (score - 0.5), 3)
    raw = math.floor(base * mult + 0.5)
    return score, mult, raw, min(pmax, max(pmin, raw))


def latest_config(conn):
    return conn.execute("SELECT * FROM pricing_config ORDER BY updated_at DESC, config_id DESC LIMIT 1").fetchone()


def show_start(row):
    return datetime.strptime(f"{row['show_date']} {row['show_time']}", "%Y-%m-%d %H:%M")


def signals(conn, show_id, now=None):
    now = now or datetime.now()
    s = conn.execute("""SELECT s.*, sc.total_seats, m.avg_rating FROM shows s
                        JOIN screen sc ON sc.screen_id = s.screen_id JOIN movie m ON m.movie_id = s.movie_id
                        WHERE s.show_id = ?""", (show_id,)).fetchone()
    cfg = latest_config(conn)
    booked = conn.execute("SELECT COUNT(*) FROM booking_seat WHERE show_id=?", (show_id,)).fetchone()[0]
    since = now_str(now - timedelta(minutes=60))
    recent = conn.execute("SELECT COUNT(*) FROM booking WHERE show_id=? AND booking_status='CONFIRMED' AND booking_time>=?",
                          (show_id, since)).fetchone()[0]
    ref = conn.execute("""SELECT COALESCE(MAX(c),0) FROM (SELECT COUNT(*) c FROM booking b
            JOIN shows s2 ON s2.show_id=b.show_id JOIN screen sc2 ON sc2.screen_id=s2.screen_id
            WHERE sc2.total_seats=? AND b.booking_status='CONFIRMED'
            GROUP BY b.show_id, strftime('%Y-%m-%d %H', b.booking_time))""", (s["total_seats"],)).fetchone()[0]
    ref = max(ref, SPEED_FLOOR)
    wl = conn.execute("SELECT COUNT(*) FROM watchlist WHERE movie_id=?", (s["movie_id"],)).fetchone()[0]
    wl_ref = max(conn.execute("SELECT COALESCE(MAX(c),0) FROM (SELECT COUNT(*) c FROM watchlist GROUP BY movie_id)").fetchone()[0],
                 WATCHLIST_FLOOR)
    hours = max(0.0, (show_start(s) - now).total_seconds() / 3600)
    o = min(1.0, booked / s["total_seats"])
    sp = min(1.0, recent / ref)
    p = 0.6 * (s["avg_rating"] / 5) + 0.4 * min(1.0, wl / wl_ref)
    u = 1 - min(1.0, hours / cfg["booking_window_hours"])
    return s, cfg, dict(O=o, S=sp, P=p, U=u, hours=hours, booked=booked, recent=recent, speed_ref=ref, wl=wl, wl_ref=wl_ref)


def recompute_show(conn, show_id, now=None):
    """Recompute demand + price for one upcoming show. Caller controls the transaction. Returns dict or None."""
    now = now or datetime.now()
    s, cfg, sig = signals(conn, show_id, now)
    if show_start(s) <= now:
        return None
    score, mult, raw, new = compute_price(sig["O"], sig["S"], sig["P"], sig["U"], cfg, s["base_price"], s["min_price"], s["max_price"])
    stamp = now.strftime("%Y-%m-%d %H:%M:%S.%f")
    conn.execute("""INSERT INTO demand_analysis (show_id, analysis_timestamp, occupancy_rate, booking_speed, popularity_score,
                    time_remaining, demand_score) VALUES (?,?,?,?,?,?,?)""",
                 (show_id, stamp, round(sig["O"], 4), round(sig["S"], 4), round(sig["P"], 4), round(sig["hours"], 2), round(score, 4)))
    old = s["current_price"]
    if new != old:
        reason = "demand_recalculation"
        if raw > s["max_price"]: reason += " (clamped to max)"
        elif raw < s["min_price"]: reason += " (clamped to min)"
        conn.execute("INSERT INTO price_history (show_id, change_date, old_price, new_price, change_reason) VALUES (?,?,?,?,?)",
                     (show_id, now_str(now), old, new, reason))
        conn.execute("UPDATE shows SET current_price=? WHERE show_id=?", (new, show_id))
    return dict(show_id=show_id, score=score, multiplier=mult, raw=raw, old=old, new=new, **sig)


def recompute_all(conn, now=None):
    now = now or datetime.now()
    rows = conn.execute("SELECT show_id FROM shows WHERE datetime(show_date || ' ' || show_time) > datetime(?)", (now_str(now),)).fetchall()
    n = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for r in rows:
            if recompute_show(conn, r["show_id"], now): n += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK"); raise
    return n
