"""Real-browser end-to-end test (Playwright/Chromium). Starts the app on a temp DB, drives the UI, saves screenshots + results JSON."""
import json, os, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright, expect
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "docs", "screenshots"); os.makedirs(SHOTS, exist_ok=True)
PORT = 5077; BASE = f"http://127.0.0.1:{PORT}"
tmp = tempfile.mkdtemp()
env = dict(os.environ, DATABASE_PATH=os.path.join(tmp, "e2e.db"), PORT=str(PORT), ENABLE_SCHEDULER="0")
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "backend", "app.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
results = []
def check(name, fn):
    try: fn(); results.append((name, "PASS", ""))
    except Exception as e:
        results.append((name, "FAIL", str(e).splitlines()[0][:200]))
        try: PG.screenshot(path="/tmp/fail_" + name[:20].replace(" ", "_") + ".png")
        except Exception: pass
    print(results[-1])
try:
    for _ in range(60):
        try: urllib.request.urlopen(BASE + "/api/health"); break
        except Exception: time.sleep(.5)
    with sync_playwright() as p:
        b = p.chromium.launch(); ctx = b.new_context(viewport={"width": 1280, "height": 860}); pg = ctx.new_page(); PG = pg
        errors = []; pg.on("pageerror", lambda e: errors.append(str(e))); pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text and "409" not in m.text else None)   # 401/409 = deliberate wrong-password and double-booking tests
        shot = lambda n: pg.screenshot(path=os.path.join(SHOTS, n + ".png"))
        state = {}
        def t_home():
            pg.goto(BASE); expect(pg.locator(".movie-card").first).to_be_visible(); assert pg.locator(".movie-card").count() == 12; shot("01_home")
        check("Home page lists 12 movies", t_home)
        def t_filter():
            pg.fill("input[name=q]", "inception"); pg.wait_for_timeout(600); assert pg.locator(".movie-card").count() == 1
            pg.fill("input[name=q]", ""); pg.select_option("select[name=language]", "Tamil"); pg.wait_for_timeout(600); n = pg.locator(".movie-card").count(); assert n == 2, n
            shot("02_filter_tamil"); pg.select_option("select[name=language]", "")
        check("Search and language filter", t_filter)
        def t_register():
            pg.goto(BASE + "/#/register"); pg.fill("input[name=full_name]", "Demo Student"); pg.fill("input[name=email]", "demo.student@example.com")
            pg.fill("input[name=password]", "Student123"); shot("03_register"); pg.click("form#auth button"); expect(pg.locator("#nav-links")).to_contain_text("Log out")
        check("Register new user", t_register)
        def t_logout_login():
            pg.click("text=Log out"); expect(pg.locator("#nav-links")).to_contain_text("Log in"); pg.goto(BASE + "/#/login")
            pg.fill("input[name=email]", "aarav.sharma@example.com"); pg.fill("input[name=password]", "wrong"); pg.click("form#auth button")
            expect(pg.locator("#toast")).to_contain_text("Invalid"); shot("04_login_error")
            pg.fill("input[name=password]", "Password@123"); pg.click("form#auth button"); expect(pg.locator("#nav-links")).to_contain_text("Log out")
        check("Logout, bad login rejected, good login", t_logout_login)
        def t_movie():
            pg.goto(BASE + "/#/"); pg.click("text=Inception"); expect(pg.locator("h1")).to_contain_text("Inception"); expect(pg.locator(".slot").first).to_be_visible(); shot("05_movie_detail")
        check("Movie detail with shows and live prices", t_movie)
        def t_watch():
            if pg.locator("button:has-text('In watchlist')").count():          # seeded user may already follow it: test removal first
                pg.click("button:has-text('In watchlist')"); expect(pg.locator("#toast")).to_contain_text("Removed"); expect(pg.locator("button:has-text('Add to watchlist')")).to_be_visible()
            pg.click("button:has-text('Add to watchlist')"); expect(pg.locator("#toast")).to_contain_text("Added"); pg.goto(BASE + "/#/watchlist")
            expect(pg.locator(".card").first).to_contain_text("Inception"); shot("06_watchlist")
        check("Add movie to watchlist", t_watch)
        def t_seats():
            pg.goto(BASE + "/#/"); pg.click("text=Inception"); pg.locator(".slot").first.click(); expect(pg.locator(".seat").first).to_be_visible()
            state["url"] = pg.url; state["price"] = pg.locator(".price-big").first.inner_text(); shot("07_seat_map")
            pg.select_option("#rec-n", "3"); pg.click("text=Suggest"); expect(pg.locator(".seat.sel")).to_have_count(3); shot("08_recommended_seats")
        check("Seat map renders and recommendation selects 3 seats", t_seats)
        def t_double():
            # a second browser session grabs one of the selected seats first -> first user's booking must be rejected
            ids = pg.locator(".seat.sel").evaluate_all("els => els.map(e => e.dataset.id)"); sid = state["url"].split("/")[-1]
            ctx2 = b.new_context(); r2 = ctx2.new_page(); r2.goto(BASE); r2.evaluate("""async () => { await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:'diya.patel@example.com',password:'Password@123'})}); }""")
            res = r2.evaluate("""async ([sid,seat]) => { const r = await fetch('/api/bookings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({show_id:+sid,seat_ids:[+seat],payment_method:'UPI'})}); return r.status }""", [sid, ids[1]])
            assert res == 201, res; ctx2.close()
            pg.click("button:has-text('Confirm booking')"); expect(pg.locator("#toast")).to_contain_text("just booked"); shot("09_double_booking_blocked")
            pg.wait_for_timeout(300); assert pg.locator(".seat.sel").count() == 2, pg.locator(".seat.sel").count()   # taken seat auto-deselected
        check("Double booking prevented in UI (seat taken by another user)", t_double)
        def t_book():
            pg.click("button:has-text('Confirm booking')"); expect(pg.locator("h1")).to_contain_text("My bookings"); expect(pg.locator(".panel").first).to_contain_text("CONFIRMED"); shot("10_my_bookings")
        check("Confirm booking of remaining seats", t_book)
        def t_cancel():
            pg.once("dialog", lambda d: d.accept()); pg.locator("button:has-text('Cancel')").first.click(); expect(pg.locator("#toast")).to_contain_text("cancelled")
        check("Cancel booking", t_cancel)
        def t_memory():
            pg.goto(BASE + "/#/bookings"); pg.locator("[data-act=memory-form]").first.click()
            png = os.path.join(ROOT, "frontend", "sample", "memory1.png"); pg.set_input_files("form[data-memform] input[type=file]", png)
            pg.fill("form[data-memform] input[name=caption]", "E2E memory"); pg.click("form[data-memform] button"); expect(pg.locator("#toast")).to_contain_text("Memory saved")
            pg.goto(BASE + "/#/memories"); expect(pg.locator("img.mem").first).to_be_visible(); shot("11_memories")
        check("Upload movie memory for a watched booking", t_memory)
        def t_review():
            mid = pg.evaluate("async () => (await (await fetch('/api/bookings')).json()).find(b=>b.started && b.booking_status==='CONFIRMED').movie_id")
            pg.goto(f"{BASE}/#/movie/{mid}"); expect(pg.locator("#review-form")).to_be_visible(); pg.select_option("#review-form select", "5"); pg.fill("#review-form textarea", "E2E review: great!")
            pg.click("#review-form button"); expect(pg.locator("#toast")).to_contain_text("Review saved"); expect(pg.locator(".review").first).to_contain_text("E2E review"); shot("12_review")
        check("Post review after attending a show", t_review)
        def t_notif():
            pg.goto(BASE + "/#/notifications"); expect(pg.locator(".notif").first).to_be_visible(); shot("13_notifications")
            pg.goto(BASE + "/#/preferences"); pg.locator("#prefs input").first.check(); pg.click("#prefs button"); expect(pg.locator("#toast")).to_contain_text("saved")
        check("Notifications and genre preferences", t_notif)
        def t_rbac():
            pg.goto(BASE + "/#/admin"); expect(pg.locator("h1")).to_contain_text("Admin login")     # customer cannot open admin
        check("Customer redirected away from admin dashboard", t_rbac)
        def t_admin():
            pg.click("text=Log out"); expect(pg.locator("#nav-links")).to_contain_text("Log in"); pg.goto(BASE + "/#/admin-login"); pg.fill("input[name=email]", "admin@smartmovie.local"); pg.fill("input[name=password]", "Admin@123"); pg.click("form#auth button")
            expect(pg.locator("h1")).to_contain_text("Admin dashboard"); expect(pg.locator(".stat").first).to_be_visible(); shot("14_admin_overview")
            pg.click("text=Recompute prices now"); expect(pg.locator("#toast")).to_contain_text("Repriced"); pg.locator("[data-act=show-pricing]").first.click(); expect(pg.locator("#audit")).to_contain_text("Audit"); shot("15_admin_audit")
        check("Admin login, dashboard, repricing and audit", t_admin)
        def t_admin_tabs():
            pg.click("button:has-text('Pricing engine')"); expect(pg.locator("#cfg-form")).to_be_visible(); shot("16_admin_pricing")
            pg.click("button:has-text('Shows')"); expect(pg.locator("#show-form")).to_be_visible(); shot("17_admin_shows")
            pg.click("button:has-text('Movies')"); pg.fill("#movie-form input[name=title]", "E2E Test Movie"); pg.click("#movie-form button"); expect(pg.locator("#toast")).to_contain_text("Saved")
            expect(pg.locator("table")).to_contain_text("E2E Test Movie"); pg.click("button:has-text('Users')"); expect(pg.locator("table").first).to_contain_text("demo.student@example.com")
        check("Admin: pricing config, show scheduling, add movie, users", t_admin_tabs)
        def t_mobile():
            m = b.new_context(viewport={"width": 390, "height": 800}).new_page(); m.goto(BASE); expect(m.locator(".movie-card").first).to_be_visible()
            assert m.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "horizontal overflow"; m.screenshot(path=os.path.join(SHOTS, "18_mobile_home.png"))
        check("Responsive layout at 390px (no horizontal overflow)", t_mobile)
        check("No JavaScript errors in console", lambda: (_ for _ in ()).throw(AssertionError("; ".join(errors)[:300])) if errors else None)
        b.close()
finally:
    srv.terminate()
json.dump(results, open(os.path.join(ROOT, "docs", "evidence", "e2e_results.json"), "w"), indent=1)
print("PASS:", sum(r[1] == "PASS" for r in results), "FAIL:", sum(r[1] == "FAIL" for r in results))
