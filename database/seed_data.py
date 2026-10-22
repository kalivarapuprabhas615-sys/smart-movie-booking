"""
Single source of truth for the sample data (DML).

  build_dataset()            -> dict {table: [row dicts]}
  load_sqlite(conn, ds)      -> inserts into the SQLite database used by the web app
  write_oracle_sql(ds, path) -> writes database/oracle/03_seed.sql (INSERT statements)

Relative times (At / Day) are resolved against "now" when loading SQLite, and against
SYSDATE when the Oracle script is run, so the demo always has upcoming shows.
Sample data only: reviews, users, bookings are synthetic. Movie facts (title, year, duration) are
public information; synopses are written for this project.
"""
import random
from datetime import datetime, timedelta, date
from werkzeug.security import generate_password_hash

USER_PASSWORD = "Password@123"
ADMIN_PASSWORD = "Admin@123"


class At:          # a timestamp: `day` days from today 00:00, plus `minutes`
    def __init__(self, day, minutes): self.day, self.minutes = day, minutes


class Day:         # a DATE: `day` days from today
    def __init__(self, day): self.day = day


GENRES = ["Action", "Drama", "Comedy", "Sci-Fi", "Thriller", "Romance", "Animation", "Adventure", "Crime", "Fantasy"]

MOVIES = [  # title, language, cert, minutes, release, genres, synopsis
    ("Inception", "English", "UA", 148, "2010-07-16", ["Sci-Fi", "Thriller", "Action"],
     "A skilled thief who steals secrets from dreams is offered one last job: plant an idea instead of stealing one."),
    ("Interstellar", "English", "UA", 169, "2014-11-07", ["Sci-Fi", "Drama", "Adventure"],
     "With Earth's crops failing, a former pilot joins an expedition through a wormhole to find humanity a new home."),
    ("3 Idiots", "Hindi", "U", 170, "2009-12-25", ["Comedy", "Drama"],
     "Two friends search for their long-lost companion and recall how he taught them to chase learning, not grades."),
    ("Baahubali: The Beginning", "Telugu", "UA", 159, "2015-07-10", ["Action", "Adventure", "Fantasy"],
     "A young man raised in a village discovers his royal heritage and a kingdom that needs him."),
    ("Vikram", "Tamil", "UA", 174, "2022-06-03", ["Action", "Thriller", "Crime"],
     "A special agent investigates a string of masked-vigilante murders that leads to a far larger conspiracy."),
    ("Kantara", "Kannada", "UA", 148, "2022-09-30", ["Drama", "Thriller", "Fantasy"],
     "A village's bond with its forest and its guardian spirit is tested by a forest officer and a feudal landlord."),
    ("Spider-Man: Into the Spider-Verse", "English", "U", 117, "2018-12-14", ["Animation", "Action", "Adventure"],
     "Teenager Miles Morales meets spider-heroes from other dimensions and must learn to be one himself."),
    ("Dangal", "Hindi", "U", 161, "2016-12-23", ["Drama"],
     "A former wrestler trains his daughters to become champions against social expectations."),
    ("The Dark Knight", "English", "UA", 152, "2008-07-18", ["Action", "Crime", "Drama"],
     "Batman faces the Joker, an anarchist criminal who pushes Gotham to its limits."),
    ("Jai Bhim", "Tamil", "UA", 164, "2021-11-02", ["Drama", "Crime"],
     "A lawyer fights for justice for a tribal woman whose husband disappears in police custody."),
    ("Zindagi Na Milegi Dobara", "Hindi", "UA", 155, "2011-07-15", ["Comedy", "Drama", "Romance"],
     "Three friends take a bachelor-party road trip across Spain and confront their fears and each other."),
    ("RRR", "Telugu", "UA", 187, "2022-03-25", ["Action", "Drama"],
     "Two revolutionaries form an unlikely friendship while fighting colonial rule in 1920s India."),
]

# theatre name, location, city, [(rows, cols) per screen], price uplift
THEATRES = [
    ("Starlight Multiplex", "Katpadi Road", "Vellore", [(10, 10), (8, 10)], 0),
    ("Orion Cinemas", "Anna Nagar", "Chennai", [(10, 12), (10, 10)], 30),
    ("Galaxy Screens", "Whitefield", "Bengaluru", [(8, 12), (10, 10)], 30),
    ("Lumiere Grand", "Banjara Hills", "Hyderabad", [(10, 10), (8, 10)], 20),
]
SLOTS = [("11:00", 200), ("15:00", 250), ("19:00", 300), ("22:15", 300)]

USERS = ["Aarav Sharma", "Diya Patel", "Rohan Iyer", "Meera Nair", "Karthik Reddy", "Ananya Krishnan",
         "Vikram Singh", "Sneha Menon", "Arjun Verma", "Priya Raman", "Rahul Das", "Lakshmi Narayanan"]

COMMENTS = {
    5: ["Brilliant from start to finish.", "A masterpiece, worth every rupee.", "Edge-of-the-seat entertainment!", "Gave me goosebumps. Watch it on the big screen."],
    4: ["Really good, a few slow patches.", "Strong performances and great music.", "Very enjoyable with family."],
    3: ["Decent one-time watch.", "Good idea, uneven execution."],
    2: ["Expected more.", "Too long for what it offers."],
}


def build_dataset(seed=42, now=None):
    rnd = random.Random(seed)
    now = now or datetime.now()
    now_min = now.hour * 60 + now.minute
    ds = {t: [] for t in [
        "theatre", "screen", "seat", "movie", "genre", "movie_genre", "users", "admins", "pricing_config",
        "user_genre_preference", "shows", "booking", "booking_seat", "payment", "review", "movie_memory",
        "notification", "watchlist"]}

    # --- admins & pricing config ---
    ds["admins"].append(dict(admin_id=1, full_name="Theatre Admin", email="admin@smartmovie.local",
                             password_hash=generate_password_hash(ADMIN_PASSWORD)))
    ds["pricing_config"].append(dict(config_id=1, admin_id=1, w_occupancy=0.40, w_booking_speed=0.25, w_popularity=0.15,
                                     w_urgency=0.20, elasticity_k=0.60, booking_window_hours=24, updated_at=At(-30, 600)))

    # --- genres, movies ---
    for i, g in enumerate(GENRES, 1):
        ds["genre"].append(dict(genre_id=i, genre_name=g))
    gid = {g: i for i, g in enumerate(GENRES, 1)}
    for i, (t, lang, cert, dur, rel, gens, syn) in enumerate(MOVIES, 1):
        ds["movie"].append(dict(movie_id=i, title=t, movie_language=lang, certification=cert, duration_min=dur,
                                release_date=rel, synopsis=syn, avg_rating=0))
        for g in gens:
            ds["movie_genre"].append(dict(movie_id=i, genre_id=gid[g]))

    # --- theatres, screens, seats ---
    seat_id = 0
    screens = {}   # screen_id -> dict(seat_ids list, total, uplift)
    sid = 0
    for ti, (name, loc, city, layouts, uplift) in enumerate(THEATRES, 1):
        ds["theatre"].append(dict(theatre_id=ti, theatre_name=name, location=loc, city=city))
        for sn, (rows, cols) in enumerate(layouts, 1):
            sid += 1
            ds["screen"].append(dict(screen_id=sid, theatre_id=ti, screen_number=sn, total_seats=rows * cols))
            ids = []
            for r in range(rows):
                frac = r / rows
                loc_ = "FRONT" if frac < 0.3 else ("MIDDLE" if frac < 0.75 else "BACK")
                for c in range(1, cols + 1):
                    seat_id += 1
                    typ = "PREMIUM" if loc_ == "MIDDLE" and 3 <= c <= cols - 2 else "STANDARD"
                    ds["seat"].append(dict(seat_id=seat_id, screen_id=sid, seat_number=f"{chr(65 + r)}{c}",
                                           seat_type=typ, seat_location=loc_))
                    ids.append(seat_id)
            screens[sid] = dict(seats=ids, total=rows * cols, uplift=uplift)

    # --- users, preferences ---
    for i, nm in enumerate(USERS, 1):
        email = nm.lower().replace(" ", ".") + "@example.com"
        ds["users"].append(dict(user_id=i, full_name=nm, email=email, password_hash=generate_password_hash(USER_PASSWORD),
                                phone="98%08d" % rnd.randint(10**7, 10**8 - 1), join_date=Day(-rnd.randint(20, 400))))
        for g in rnd.sample(range(1, len(GENRES) + 1), 3):
            ds["user_genre_preference"].append(dict(user_id=i, genre_id=g))

    # --- shows ---
    show_id = 0
    shows = {}
    movie_cycle = []
    for day in range(-1, 3):                      # yesterday .. day after tomorrow  (4 days)
        for scr in screens:
            slots = sorted(rnd.sample(SLOTS, 2))
            for tm, base in slots:
                if not movie_cycle:
                    movie_cycle = list(range(1, len(MOVIES) + 1)); rnd.shuffle(movie_cycle)
                show_id += 1
                base += screens[scr]["uplift"]
                hh, mm = map(int, tm.split(":"))
                ds["shows"].append(dict(
                    show_id=show_id, movie_id=movie_cycle.pop(), screen_id=scr, admin_id=1, show_date=Day(day), show_time=tm,
                    base_price=base, current_price=base, min_price=round(base * 0.8), max_price=round(base * 1.6)))
                shows[show_id] = dict(day=day, start_min=hh * 60 + mm, screen=scr, base=base)

    # --- bookings / payments / booking seats ---
    booking_id = pay_id = 0
    taken = {s: set() for s in shows}
    past_confirmed = []
    methods = ["UPI", "UPI", "CARD", "NETBANKING", "WALLET"]
    for sh, info in shows.items():
        scr = screens[info["screen"]]
        start_rel = info["day"] * 1440 + info["start_min"] - now_min   # minutes from now to show start
        is_past = start_rel <= 0
        target = int(scr["total"] * (rnd.uniform(0.45, 0.8) if is_past else rnd.uniform(0.05, 0.6)))
        while len(taken[sh]) < target:
            n = rnd.choices([1, 2, 3, 4, 5], [2, 5, 3, 2, 1])[0]
            free = [s for s in scr["seats"] if s not in taken[sh]]
            if len(free) < n: break
            i = rnd.randrange(len(free) - n + 1); chosen = free[i:i + n]       # seats are row-ordered -> mostly adjacent
            booking_id += 1; pay_id += 1
            uid = rnd.randint(1, len(USERS))
            lead = rnd.randint(60, 1500) if is_past else start_rel + rnd.randint(5, 2500)   # minutes before show start
            when = At(info["day"], info["start_min"] - lead)
            cancelled = (not is_past) and rnd.random() < 0.06
            amt = info["base"] * n
            ds["booking"].append(dict(booking_id=booking_id, user_id=uid, show_id=sh, booking_time=when, total_amount=amt,
                                      booking_status="CANCELLED" if cancelled else "CONFIRMED"))
            ds["payment"].append(dict(payment_id=pay_id, booking_id=booking_id, amount=amt, payment_date=when,
                                      payment_method=rnd.choice(methods),
                                      payment_status="REFUNDED" if cancelled else "SUCCESS"))
            if not cancelled:
                for s in chosen:
                    taken[sh].add(s)
                    ds["booking_seat"].append(dict(booking_id=booking_id, seat_id=s, show_id=sh))
                if is_past: past_confirmed.append((booking_id, uid, sh))

    # --- reviews (historical sample reviews) ---
    rid = 0
    seen = set()
    for _ in range(40):
        uid, mid = rnd.randint(1, len(USERS)), rnd.randint(1, len(MOVIES))
        if (uid, mid) in seen: continue
        seen.add((uid, mid)); rid += 1
        rating = rnd.choices([5, 4, 3, 2], [5, 4, 2, 1])[0]
        ds["review"].append(dict(review_id=rid, user_id=uid, movie_id=mid, rating=rating,
                                 review_comment=rnd.choice(COMMENTS[rating]), review_date=At(-rnd.randint(1, 40), 840)))
    sums = {}
    for r in ds["review"]: sums.setdefault(r["movie_id"], []).append(r["rating"])
    for m in ds["movie"]:
        if m["movie_id"] in sums: m["avg_rating"] = round(sum(sums[m["movie_id"]]) / len(sums[m["movie_id"]]), 2)

    # --- watchlists ---
    seen = set(); wid = 0
    for _ in range(30):
        uid, mid = rnd.randint(1, len(USERS)), rnd.randint(1, len(MOVIES))
        if (uid, mid) in seen: continue
        seen.add((uid, mid)); wid += 1
        ds["watchlist"].append(dict(watchlist_id=wid, user_id=uid, movie_id=mid, added_date=At(-rnd.randint(0, 20), 700),
                                    notified_status=rnd.choice("YN")))

    # --- memories (linked to bookings of shows that already started) ---
    for k, (b, uid, sh) in enumerate(past_confirmed[:4], 1):
        mv = next(s for s in ds["shows"] if s["show_id"] == sh)["movie_id"]
        ds["movie_memory"].append(dict(memory_id=k, booking_id=b, user_id=uid, movie_id=mv,
                                       media_url=f"/sample/memory{k}.png",
                                       caption=["Movie night with friends!", "Front-row feels", "Popcorn + great film",
                                                "Ticket stub for the scrapbook"][k - 1],
                                       upload_date=At(shows[sh]["day"], shows[sh]["start_min"] + 200)))

    # --- notifications ---
    nid = 0
    for b in ds["booking"][:12]:
        nid += 1
        ds["notification"].append(dict(
            notification_id=nid, user_id=b["user_id"], notif_type="BOOKING" if b["booking_status"] == "CONFIRMED" else "SYSTEM",
            message=(f"Booking #{b['booking_id']} confirmed. Enjoy the show!" if b["booking_status"] == "CONFIRMED"
                     else f"Booking #{b['booking_id']} was cancelled and refunded."),
            sent_date=b["booking_time"], status=rnd.choice(["READ", "UNREAD"])))
    for w in ds["watchlist"][:6]:
        nid += 1
        ds["notification"].append(dict(notification_id=nid, user_id=w["user_id"], notif_type="WATCHLIST",
                                       message="A movie on your watchlist now has shows scheduled. Book early for the best price!",
                                       sent_date=At(0, 480), status="UNREAD"))
    return ds


# ---------------------------------------------------------------- emitters
def _sqlite_val(v, now):
    if isinstance(v, At):
        return (datetime(now.year, now.month, now.day) + timedelta(days=v.day, minutes=v.minutes)).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, Day):
        return (date(now.year, now.month, now.day) + timedelta(days=v.day)).isoformat()
    return v


ORDER = ["admins", "pricing_config", "genre", "movie", "movie_genre", "theatre", "screen", "seat", "users",
         "user_genre_preference", "shows", "booking", "booking_seat", "payment", "review", "movie_memory",
         "watchlist", "notification"]


def load_sqlite(conn, ds, now=None):
    now = now or datetime.now()
    for t in ORDER:
        rows = ds[t]
        if not rows: continue
        cols = list(rows[0].keys())
        conn.executemany(f"INSERT INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         [[_sqlite_val(r[c], now) for c in cols] for r in rows])
    conn.commit()


def _ora_val(v):
    if v is None: return "NULL"
    if isinstance(v, At): return f"CAST(TRUNC(SYSDATE)+({v.day})+({v.minutes})/1440 AS TIMESTAMP)"
    if isinstance(v, Day): return f"TRUNC(SYSDATE)+({v.day})"
    if isinstance(v, (int, float)): return repr(v)
    s = str(v)
    if len(s) == 10 and s[4] == "-" and s[7] == "-": return f"DATE '{s}'"
    return "'" + s.replace("'", "''") + "'"


def write_oracle_sql(ds, path):
    with open(path, "w") as f:
        f.write("-- Smart Movie Experience & Booking System -- Oracle DML (sample data)\n"
                "-- GENERATED by database/seed_data.py -- run AFTER 01_schema.sql and 02_plsql.sql.\n"
                f"-- Demo logins: admin@smartmovie.local / {ADMIN_PASSWORD} ; users: <first.last>@example.com / {USER_PASSWORD}\n"
                "-- Times are relative to SYSDATE so the demo always has upcoming shows.\n"
                "SET DEFINE OFF\nALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS';\n\n")
        for t in ORDER:
            rows = ds[t]
            if not rows: continue
            f.write(f"-- {t}: {len(rows)} rows\n")
            cols = list(rows[0].keys())
            for r in rows:
                f.write(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join(_ora_val(r[c]) for c in cols)});\n")
            f.write("\n")
        f.write("COMMIT;\n\n-- First pricing pass (also demonstrates the PL/SQL package)\n"
                "BEGIN\n  pkg_dynamic_pricing.recompute_all;\nEND;\n/\nCOMMIT;\n")


if __name__ == "__main__":
    import os
    ds = build_dataset()
    out = os.path.join(os.path.dirname(__file__), "oracle", "03_seed.sql")
    write_oracle_sql(ds, out)
    print("wrote", out, {t: len(r) for t, r in ds.items()})
