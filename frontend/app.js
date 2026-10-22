/* Smart Movie – single-page frontend (vanilla JS, hash routing, talks to the Flask REST API). */
"use strict";
const $app = document.getElementById("app");
const state = { user: null, genres: [], unread: 0, timer: null, adminTab: "overview" };

// ---------- helpers ----------
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
class Raw { constructor(s) { this.s = s; } }
const raw = s => new Raw(s);
const html = (strs, ...vals) => new Raw(strs.reduce((o, s, i) => {
  let v = vals[i - 1];
  v = v instanceof Raw ? v.s : Array.isArray(v) ? v.map(x => x instanceof Raw ? x.s : esc(x)).join("") : esc(v);
  return o + v + s;
}));
const money = n => "₹" + Math.round(Number(n)).toLocaleString("en-IN");
const fmtDate = d => new Date(d.replace(" ", "T")).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
const fmtDT = d => new Date(d.replace(" ", "T")).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const hue = id => (id * 67) % 360;
const poster = (m, big) => html`<div class="poster ${big ? "big" : ""}" style="background:linear-gradient(145deg,hsl(${hue(m.movie_id)} 70% 35%),hsl(${(hue(m.movie_id) + 60) % 360} 70% 22%))">${m.title}</div>`;
const stars = r => r ? html`<span class="rating">★ ${Number(r).toFixed(1)}</span>` : html`<span class="muted">No ratings yet</span>`;

function toast(msg, kind = "ok") {
  const t = document.getElementById("toast"); t.textContent = msg; t.className = "show " + kind;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = ""), 3200);
}
async function api(path, opts = {}) {
  const o = { credentials: "same-origin", headers: {}, ...opts };
  if (o.json !== undefined) { o.method = o.method || "POST"; o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(o.json); }
  const r = await fetch("/api" + path, o);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(data.error || "Request failed"); e.status = r.status; throw e; }
  return data;
}
const render = (view) => { $app.innerHTML = view.s; };
const go = h => { location.hash = h; };
const needUser = () => { if (!state.user || state.user.role !== "user") { go("#/login"); return false; } return true; };
const needAdmin = () => { if (!state.user || state.user.role !== "admin") { go("#/admin-login"); return false; } return true; };

async function refreshUser() {
  const { user } = await api("/auth/me"); state.user = user;
  state.unread = 0;
  if (user && user.role === "user") { try { state.unread = (await api("/notifications")).filter(n => n.status === "UNREAD").length; } catch { } }
  const cur = location.hash || "#/";
  const link = (h, label, extra = "") => html`<a href="${h}" class="${cur === h ? "active" : ""}">${label}${raw(extra)}</a>`;
  let n = html`${link("#/", "Movies")}`;
  if (!user) n = html`${n}${link("#/login", "Log in")}${link("#/register", "Register")}${link("#/admin-login", "Admin")}`;
  else if (user.role === "user") n = html`${n}${link("#/bookings", "My bookings")}${link("#/watchlist", "Watchlist")}${link("#/memories", "Memories")}${link("#/notifications", "Alerts", state.unread ? `<span class="badge">${state.unread}</span>` : "")}${link("#/preferences", "Preferences")}<button data-act="logout">Log out (${user.full_name.split(" ")[0]})</button>`;
  else n = html`${n}${link("#/admin", "Dashboard")}<button data-act="logout">Log out (admin)</button>`;
  document.getElementById("nav-links").innerHTML = n.s;
}

// ---------- pages ----------
async function pageHome() {
  const [{ languages }, genres] = [await api("/movies"), await api("/genres")];
  state.genres = genres;
  render(html`<section class="hero"><h1>Find your next movie night</h1><p>Browse, pick the best seats, and book with demand-based pricing – book early, pay less.</p></section>
  <form class="filters" id="filters">
    <input type="search" name="q" placeholder="Search title or story…" aria-label="Search movies">
    <select name="genre" aria-label="Genre"><option value="">All genres</option>${genres.map(g => html`<option value="${g.genre_id}">${g.genre_name}</option>`)}</select>
    <select name="language" aria-label="Language"><option value="">All languages</option>${languages.map(l => html`<option>${l}</option>`)}</select>
    <select name="min_rating" aria-label="Minimum rating"><option value="">Any rating</option><option value="3">3★ &amp; up</option><option value="4">4★ &amp; up</option></select>
  </form><div id="movie-grid"></div>`);
  const f = document.getElementById("filters");
  const load = async () => {
    const p = new URLSearchParams([...new FormData(f)].filter(([, v]) => v));
    const { movies } = await api("/movies?" + p);
    const grid = document.getElementById("movie-grid"); if (!grid) return;    // user navigated away meanwhile
    grid.innerHTML = movies.length ? html`<div class="grid">${movies.map(m => html`
      <a class="card movie-card" href="#/movie/${m.movie_id}">${poster(m)}<div class="body">
        <div class="row between"><b>${m.title}</b>${stars(m.avg_rating)}</div>
        <div class="muted">${m.movie_language} · ${m.certification} · ${m.duration_min} min</div>
        <div>${(m.genres || "").split(", ").filter(Boolean).map(g => html`<span class="pill">${g}</span>`)}</div>
        <div class="muted" style="margin-top:6px">${m.upcoming_shows ? html`${m.upcoming_shows} shows · from <b>${money(m.from_price)}</b>` : "No upcoming shows"}</div></div></a>`)}</div>`.s : raw('<p class="muted center">No movies match your filters.</p>');
  };
  let t; f.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 200); });
  load();
}

async function pageMovie(id) {
  const [m, shows, reviews] = await Promise.all([api("/movies/" + id), api(`/movies/${id}/shows`), api(`/movies/${id}/reviews`)]);
  const byDate = {};
  shows.forEach(s => ((byDate[s.show_date] ||= {})[s.theatre_name + " · " + s.city] ||= []).push(s));
  render(html`<div class="detail">${poster(m, true)}<div>
    <h1>${m.title}</h1><div class="row">${stars(m.avg_rating)}<span class="muted">(${m.review_count} reviews)</span></div>
    <p class="muted">${m.movie_language} · ${m.certification} · ${m.duration_min} min · Released ${m.release_date}</p>
    <div>${(m.genres || "").split(", ").filter(Boolean).map(g => html`<span class="pill">${g}</span>`)}</div>
    <p>${m.synopsis}</p>
    <button class="btn ${m.in_watchlist ? "ghost" : ""}" data-act="watch" data-id="${m.movie_id}" data-in="${m.in_watchlist ? 1 : 0}">${m.in_watchlist ? "✓ In watchlist (remove)" : "+ Add to watchlist"}</button>
  </div></div>
  <section class="panel"><h2>Shows &amp; live prices</h2>
  ${shows.length ? Object.entries(byDate).map(([d, th]) => html`<h3>${fmtDate(d)}</h3>${Object.entries(th).map(([name, list]) => html`
    <div style="margin-bottom:12px"><div class="muted" style="margin-bottom:6px">${name}</div><div class="showtimes">${list.map(s => html`
      <a class="slot ${s.seats_left < 10 ? "low" : ""}" href="#/show/${s.show_id}"><b>${s.show_time}</b>${money(s.current_price)}${s.current_price > s.base_price ? " ▲" : s.current_price < s.base_price ? " ▼" : ""}<br><small>${s.seats_left} seats left · Screen ${s.screen_number}</small></a>`)}</div></div>`)}`) : raw('<p class="muted">No upcoming shows scheduled yet. Add it to your watchlist and we will notify you.</p>')}
  </section>
  <section class="panel"><h2>Reviews</h2>
  ${m.can_review ? html`<form class="stack" id="review-form"><label>Your rating<select name="rating">${[5, 4, 3, 2, 1].map(r => html`<option value="${r}">${"★".repeat(r)} (${r})</option>`)}</select></label>
    <label>Comment<textarea name="comment" rows="3" maxlength="1000"></textarea></label><button class="btn">Submit review</button></form>`
      : raw('<p class="muted">You can review this movie after attending one of its shows.</p>')}
  ${reviews.map(r => html`<div class="review"><b>${r.full_name}</b> <span class="rating">${"★".repeat(r.rating)}</span><div>${r.review_comment}</div></div>`)}</section>`);
  const rf = document.getElementById("review-form");
  if (rf) rf.addEventListener("submit", async e => {
    e.preventDefault(); const f = new FormData(rf);
    try { await api(`/movies/${id}/reviews`, { json: { rating: f.get("rating"), comment: f.get("comment") } }); toast("Review saved"); route(); } catch (er) { toast(er.message, "err"); }
  });
}

async function pageShow(id) {
  if (!needUser()) return;
  let sel = new Set(), rec = new Set(), data;
  const draw = () => {
    const { show: s, seats, demand } = data;
    const pay0 = document.getElementById("pay")?.value, n0 = document.getElementById("rec-n")?.value;
    const rows = {}; seats.forEach(x => (rows[x.seat_number[0]] ||= []).push(x));
    const total = sel.size * s.current_price;
    const diff = Math.round((s.current_price / s.base_price - 1) * 100);
    render(html`<a href="#/movie/${s.movie_id}" class="muted">← ${s.title}</a>
    <h1>${s.title}</h1><p class="muted">${s.theatre_name}, ${s.city} · Screen ${s.screen_number} · ${fmtDate(s.show_date)} ${s.show_time}</p>
    <div class="seat-layout"><div class="panel"><div class="screen-bar"></div><div class="screen-label">SCREEN</div>
      <div class="seats" id="seats">${Object.entries(rows).map(([L, list]) => html`<div class="seat-row"><span class="rl">${L}</span>${list.map(x => html`<button class="seat ${x.seat_type === "PREMIUM" ? "premium" : ""} ${sel.has(x.seat_id) ? "sel" : ""} ${rec.has(x.seat_id) && !sel.has(x.seat_id) ? "rec" : ""}" data-act="seat" data-id="${x.seat_id}" ${x.booked ? "disabled" : ""} title="${x.seat_number} · ${x.seat_type} · ${x.seat_location}" aria-label="Seat ${x.seat_number} ${x.booked ? "booked" : "available"}">${x.seat_number.slice(1)}</button>`)}</div>`)}</div>
      <div class="legend"><span><i></i>Available</span><span><i style="border-color:#a78bfa"></i>Premium</span><span><i style="background:var(--ok)"></i>Selected</span><span><i style="background:#3b3f63"></i>Booked</span><span><i style="border:2px dashed var(--warn)"></i>Recommended</span></div></div>
    <aside class="panel"><div class="muted">Live price per seat</div><div class="price-big">${money(s.current_price)}</div>
      <div class="muted">Base ${money(s.base_price)} · ${diff === 0 ? "at base price" : diff > 0 ? `${diff}% above base (high demand)` : `${-diff}% below base (low demand)`}</div>
      ${demand ? html`<div style="margin:10px 0"><div class="row between muted"><span>Demand score</span><span>${demand.demand_score.toFixed(2)}</span></div><div class="meter"><i style="width:${demand.demand_score * 100}%"></i></div>
        <small class="muted">Occupancy ${(demand.occupancy_rate * 100).toFixed(0)}% · ${demand.time_remaining.toFixed(1)} h to show</small></div>` : ""}
      <hr style="border:0;border-top:1px solid var(--line)">
      <div class="row"><label>Smart recommend<select id="rec-n">${[1, 2, 3, 4, 5, 6].map(n => html`<option value="${n}">${n} seat${n > 1 ? "s" : ""}</option>`)}</select></label><button class="btn ghost sm" data-act="recommend">Suggest</button></div>
      <p>Selected: <b>${[...sel].map(i => seats.find(x => x.seat_id === i).seat_number).join(", ") || "none"}</b></p>
      <label>Payment (simulated)<select id="pay"><option>UPI</option><option>CARD</option><option>NETBANKING</option><option>WALLET</option></select></label>
      <p class="price-big" style="font-size:22px">Total ${money(total)}</p>
      <button class="btn" style="width:100%" data-act="book" ${sel.size && !s.started ? "" : "disabled"}>${s.started ? "Show started" : "Confirm booking"}</button></aside></div>`);
    if (pay0) document.getElementById("pay").value = pay0;
    if (n0) document.getElementById("rec-n").value = n0;
  };
  const load = async keep => {
    data = await api("/shows/" + id);
    const free = new Set(data.seats.filter(x => !x.booked).map(x => x.seat_id));
    sel = new Set([...sel].filter(x => free.has(x))); if (!keep) rec = new Set(); draw();
  };
  await load();
  page.ctx = { sel: () => sel, toggle: i => { sel.has(i) ? sel.delete(i) : sel.add(i); rec.delete(i); draw(); },
    recommend: async () => { const n = document.getElementById("rec-n").value; try { const r = await api(`/shows/${id}/recommend?count=${n}`); rec = new Set(r.seat_ids); sel = new Set(r.seat_ids); draw(); toast("Suggested: " + r.seats.join(", ") + (r.contiguous ? "" : " (not adjacent)")); } catch (e) { toast(e.message, "err"); } },
    book: async () => {
      try { const r = await api("/bookings", { json: { show_id: id, seat_ids: [...sel], payment_method: document.getElementById("pay").value } });
        toast(`Booked! ${r.seats} seat(s), ${money(r.total_amount)}`); await refreshUser(); go("#/bookings");
      } catch (e) { toast(e.message, "err"); await load(true); }
    } };
  state.timer = setInterval(() => { if (document.hidden) return; load(true).catch(() => { }); }, 12000);   // live availability + price
}

async function pageBookings() {
  if (!needUser()) return;
  const rows = await api("/bookings");
  render(html`<h1>My bookings</h1>${rows.length ? rows.map(b => html`<div class="panel"><div class="row between"><div><h3>${b.title}</h3>
    <div class="muted">${b.theatre_name}, ${b.city} · Screen ${b.screen_number} · ${fmtDate(b.show_date)} ${b.show_time}</div>
    <div>Seats: <b>${b.seats || "—"}</b> · ${money(b.total_amount)} · ${b.payment_method} <span class="status ${b.payment_status}">${b.payment_status}</span></div></div>
    <div class="right"><div class="status ${b.booking_status}">${b.booking_status}</div><div class="muted">Booking #${b.booking_id}</div>
    ${b.booking_status === "CONFIRMED" && !b.started ? html`<button class="btn danger sm" data-act="cancel" data-id="${b.booking_id}">Cancel</button>` : ""}
    ${b.booking_status === "CONFIRMED" && b.started ? html`<button class="btn ghost sm" data-act="memory-form" data-id="${b.booking_id}">+ Memory (${b.memory_count})</button> <a class="btn ghost sm" href="#/movie/${b.movie_id}">Review</a>` : ""}</div></div>
    <div id="mem-${b.booking_id}"></div></div>`) : raw('<p class="muted">No bookings yet. <a href="#/">Browse movies</a>.</p>')}`);
}

async function pageMemories() {
  if (!needUser()) return;
  const ms = await api("/memories");
  render(html`<h1>Movie memories</h1><p class="muted">Photos and videos you attached to a booking after the show. Add them from “My bookings”.</p>
  ${ms.length ? html`<div class="grid">${ms.map(m => html`<div class="card">${/\.(mp4|webm)$/i.test(m.media_url) ? html`<video class="mem" src="${m.media_url}" controls></video>` : html`<img class="mem" src="${m.media_url}" alt="${m.caption || m.title}">`}
    <div class="body"><b>${m.title}</b><div class="muted">${m.caption}</div><div class="row between"><small class="muted">${fmtDT(m.upload_date)}</small><button class="btn ghost sm" data-act="del-memory" data-id="${m.memory_id}">Delete</button></div></div></div>`)}</div>` : raw('<p class="muted">No memories yet.</p>')}`);
}

async function pageWatchlist() {
  if (!needUser()) return;
  const ws = await api("/watchlist");
  render(html`<h1>My watchlist</h1>${ws.length ? html`<div class="grid">${ws.map(w => html`<div class="card"><a href="#/movie/${w.movie_id}" style="text-decoration:none">${poster(w)}</a><div class="body"><div class="row between"><b>${w.title}</b>${stars(w.avg_rating)}</div>
    <div class="muted">${w.upcoming_shows ? w.upcoming_shows + " upcoming shows" : "No shows yet – we'll notify you"}</div><button class="btn ghost sm" style="margin-top:8px" data-act="watch" data-id="${w.movie_id}" data-in="1" data-reload="1">Remove</button></div></div>`)}</div>` : raw('<p class="muted">Your watchlist is empty.</p>')}`);
}

async function pageNotifications() {
  if (!needUser()) return;
  const ns = await api("/notifications");
  render(html`<div class="row between"><h1>Notifications</h1><button class="btn ghost sm" data-act="read-all">Mark all read</button></div>
  ${ns.length ? ns.map(n => html`<div class="notif ${n.status}"><b>${n.notif_type.replace("_", " ")}</b> · <small class="muted">${fmtDT(n.sent_date)}</small><div>${n.message}</div></div>`) : raw('<p class="muted">Nothing yet.</p>')}`);
}

async function pagePreferences() {
  if (!needUser()) return;
  const [genres, mine] = await Promise.all([api("/genres"), api("/preferences")]);
  render(html`<h1>Genre preferences</h1><p class="muted">We use these to send you alerts about new releases.</p>
  <form id="prefs"><div class="chips">${genres.map(g => html`<label><input type="checkbox" name="g" value="${g.genre_id}" ${mine.includes(g.genre_id) ? "checked" : ""}>${g.genre_name}</label>`)}</div><br><button class="btn">Save preferences</button></form>`);
  document.getElementById("prefs").addEventListener("submit", async e => { e.preventDefault();
    const ids = [...document.querySelectorAll("#prefs input:checked")].map(i => Number(i.value));
    try { await api("/preferences", { method: "PUT", json: { genre_ids: ids } }); toast("Preferences saved"); } catch (er) { toast(er.message, "err"); } });
}

function pageAuth(kind) {
  const reg = kind === "register", adm = kind === "admin";
  render(html`<div class="panel" style="max-width:480px;margin:30px auto"><h1>${adm ? "Admin login" : reg ? "Create account" : "Welcome back"}</h1>
  <form class="stack" id="auth">${reg ? html`<label>Full name<input name="full_name" required minlength="2"></label>` : ""}
    <label>Email<input type="email" name="email" required autocomplete="username"></label>
    <label>Password<input type="password" name="password" required minlength="${reg ? 8 : 1}" autocomplete="${reg ? "new-password" : "current-password"}"></label>
    ${reg ? html`<label>Phone (optional, 10 digits)<input name="phone" pattern="\\d{10}"></label>` : ""}
    <button class="btn">${adm ? "Log in as admin" : reg ? "Register" : "Log in"}</button></form>
  <p class="muted">${adm ? "Demo: admin@smartmovie.local / Admin@123" : reg ? raw('Have an account? <a href="#/login">Log in</a>') : raw('New here? <a href="#/register">Register</a> · Demo: aarav.sharma@example.com / Password@123')}</p></div>`);
  document.getElementById("auth").addEventListener("submit", async e => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    try { const r = await api(adm ? "/admin/login" : reg ? "/auth/register" : "/auth/login", { json: f }); state.user = r.user; await refreshUser(); go(adm ? "#/admin" : "#/"); toast("Welcome, " + r.user.full_name.split(" ")[0]); }
    catch (er) { toast(er.message, "err"); }
  });
}

// ---------- admin ----------
async function pageAdmin() {
  if (!needAdmin()) return;
  const tabs = [["overview", "Overview"], ["shows", "Shows"], ["movies", "Movies"], ["theatres", "Theatres & screens"], ["pricing", "Pricing engine"], ["users", "Users"]];
  const tab = state.adminTab;
  let body = html``;
  if (tab === "overview") {
    const d = await api("/admin/dashboard"), t = d.totals, maxRev = Math.max(...d.revenue_by_movie.map(r => r.revenue), 1);
    body = html`<div class="stats"><div class="stat"><b>${t.movies}</b>Movies</div><div class="stat"><b>${t.upcoming_shows}</b>Upcoming shows</div><div class="stat"><b>${t.bookings}</b>Confirmed bookings</div>
      <div class="stat"><b>${money(t.revenue)}</b>Revenue</div><div class="stat"><b>${(t.avg_occupancy * 100).toFixed(0)}%</b>Avg occupancy (upcoming)</div><div class="stat"><b>${t.users}</b>Users</div></div>
      <div class="panel"><div class="row between"><h2>Upcoming shows – occupancy, demand &amp; price</h2><button class="btn sm" data-act="run-pricing">Recompute prices now</button></div><div class="tablewrap"><table><tr><th>Movie</th><th>Theatre</th><th>When</th><th>Occupancy</th><th>Demand</th><th>Base → Current</th><th></th></tr>
      ${d.shows.filter(s => s.upcoming).map(s => html`<tr><td>${s.title}</td><td>${s.theatre_name} · S${s.screen_number}</td><td>${s.show_date} ${s.show_time}</td>
      <td><div class="row"><div class="bar"><i style="width:${s.occupancy * 100}%"></i></div>${(s.occupancy * 100).toFixed(0)}%</div></td><td>${s.demand_score == null ? "—" : s.demand_score.toFixed(2)}</td>
      <td>${money(s.base_price)} → <b>${money(s.current_price)}</b></td><td><button class="btn ghost sm" data-act="show-pricing" data-id="${s.show_id}">Audit</button></td></tr>`)}</table></div><div id="audit"></div></div>
      <div class="panel"><h2>Revenue by movie</h2>${d.revenue_by_movie.map(r => html`<div class="row" style="margin:6px 0"><div style="width:230px">${r.title}</div><div class="bar" style="flex:1"><i style="width:${r.revenue / maxRev * 100}%"></i></div><div style="width:110px" class="right">${money(r.revenue)}</div></div>`)}</div>
      <div class="panel"><h2>Latest price changes</h2><div class="tablewrap"><table><tr><th>When</th><th>Movie</th><th>Show</th><th>Old → New</th><th>Reason</th></tr>${d.price_changes.map(c => html`<tr><td>${c.change_date}</td><td>${c.title}</td><td>${c.show_date} ${c.show_time}</td><td>${money(c.old_price)} → ${money(c.new_price)}</td><td>${c.change_reason}</td></tr>`)}</table></div></div>`;
  } else if (tab === "shows") {
    const [d, ms, th] = await Promise.all([api("/admin/dashboard"), api("/movies"), api("/admin/theatres")]);
    const today = new Date().toISOString().slice(0, 10);
    body = html`<div class="panel"><h2>Schedule a show</h2><form class="inline" id="show-form">
      <label>Movie<select name="movie_id">${ms.movies.map(m => html`<option value="${m.movie_id}">${m.title}</option>`)}</select></label>
      <label>Screen<select name="screen_id">${th.flatMap(t => t.screens.map(s => html`<option value="${s.screen_id}">${t.theatre_name} – Screen ${s.screen_number} (${s.total_seats})</option>`))}</select></label>
      <label>Date<input type="date" name="show_date" value="${today}" required></label><label>Time<input type="time" name="show_time" value="19:00" required></label>
      <label>Base ₹<input type="number" name="base_price" value="250" min="1" style="width:90px"></label><label>Min ₹<input type="number" name="min_price" value="200" min="1" style="width:90px"></label><label>Max ₹<input type="number" name="max_price" value="400" min="1" style="width:90px"></label>
      <button class="btn">Add show</button></form></div>
      <div class="panel"><h2>All shows</h2><div class="tablewrap"><table><tr><th>#</th><th>Movie</th><th>Theatre</th><th>When</th><th>Min / Base / Max</th><th>Now</th><th></th></tr>
      ${d.shows.map(s => html`<tr><td>${s.show_id}</td><td>${s.title}</td><td>${s.theatre_name} · S${s.screen_number}</td><td>${s.show_date} ${s.show_time}</td><td>${money(s.min_price)} / ${money(s.base_price)} / ${money(s.max_price)}</td><td>${money(s.current_price)}</td>
      <td><button class="btn danger sm" data-act="del-show" data-id="${s.show_id}">Delete</button></td></tr>`)}</table></div></div>`;
  } else if (tab === "movies") {
    const [ms, gs] = await Promise.all([api("/movies"), api("/genres")]);
    body = html`<div class="panel"><h2>Add a movie</h2><form class="inline" id="movie-form"><label>Title<input name="title" required></label><label>Language<input name="movie_language" value="English" style="width:110px"></label>
      <label>Cert<select name="certification"><option>U</option><option selected>UA</option><option>A</option></select></label><label>Minutes<input type="number" name="duration_min" value="120" style="width:90px"></label>
      <label>Release<input type="date" name="release_date"></label><label>Synopsis<input name="synopsis" style="width:260px"></label>
      <div class="chips">${gs.map(g => html`<label><input type="checkbox" name="g" value="${g.genre_id}">${g.genre_name}</label>`)}</div><button class="btn">Add movie</button></form></div>
      <div class="panel"><h2>Catalogue</h2><div class="tablewrap"><table><tr><th>Title</th><th>Language</th><th>Genres</th><th>Rating</th><th>Shows</th><th></th></tr>${ms.movies.map(m => html`<tr><td>${m.title}</td><td>${m.movie_language}</td><td>${m.genres}</td><td>${m.avg_rating}</td><td>${m.upcoming_shows}</td><td><button class="btn danger sm" data-act="del-movie" data-id="${m.movie_id}">Delete</button></td></tr>`)}</table></div></div>`;
  } else if (tab === "theatres") {
    const th = await api("/admin/theatres");
    body = html`<div class="panel"><h2>Add theatre</h2><form class="inline" id="theatre-form"><label>Name<input name="theatre_name" required></label><label>Location<input name="location" required></label><label>City<input name="city" required></label><button class="btn">Add</button></form></div>
      <div class="panel"><h2>Add screen (seats are generated)</h2><form class="inline" id="screen-form"><label>Theatre<select name="theatre_id">${th.map(t => html`<option value="${t.theatre_id}">${t.theatre_name}</option>`)}</select></label>
      <label>Rows (A…)<input type="number" name="rows" value="8" min="1" max="26" style="width:80px"></label><label>Seats per row<input type="number" name="cols" value="10" min="1" max="30" style="width:80px"></label><button class="btn">Add screen</button></form></div>
      <div class="panel"><h2>Theatres</h2>${th.map(t => html`<p><b>${t.theatre_name}</b> – ${t.location}, ${t.city}<br><span class="muted">${t.screens.map(s => `Screen ${s.screen_number}: ${s.total_seats} seats`).join(" · ")}</span></p>`)}</div>`;
  } else if (tab === "pricing") {
    const { config: c, history } = await api("/admin/pricing-config");
    body = html`<div class="panel"><h2>Dynamic pricing parameters</h2><p class="muted">demand = wO·O + wS·S + wP·P + wU·U (weights must sum to 1) · multiplier = 1 + k·(demand − 0.5) · price = clamp(base × multiplier, min, max)</p>
      <form class="inline" id="cfg-form">${["w_occupancy", "w_booking_speed", "w_popularity", "w_urgency"].map(k => html`<label>${k}<input type="number" step="0.01" min="0" max="1" name="${k}" value="${c[k]}" style="width:100px"></label>`)}
      <label>elasticity_k<input type="number" step="0.05" min="0.05" name="elasticity_k" value="${c.elasticity_k}" style="width:100px"></label><label>window (h)<input type="number" name="booking_window_hours" value="${c.booking_window_hours}" style="width:90px"></label>
      <button class="btn">Save &amp; reprice</button></form></div>
      <div class="panel"><h2>Config history</h2><div class="tablewrap"><table><tr><th>Updated</th><th>wO</th><th>wS</th><th>wP</th><th>wU</th><th>k</th><th>window</th></tr>${history.map(h => html`<tr><td>${h.updated_at}</td><td>${h.w_occupancy}</td><td>${h.w_booking_speed}</td><td>${h.w_popularity}</td><td>${h.w_urgency}</td><td>${h.elasticity_k}</td><td>${h.booking_window_hours}h</td></tr>`)}</table></div></div>`;
  } else {
    const { users, admins } = await api("/admin/users");
    body = html`<div class="panel"><h2>Customers</h2><div class="tablewrap"><table><tr><th>#</th><th>Name</th><th>Email</th><th>Phone</th><th>Joined</th><th>Bookings</th></tr>${users.map(u => html`<tr><td>${u.user_id}</td><td>${u.full_name}</td><td>${u.email}</td><td>${u.phone || ""}</td><td>${u.join_date}</td><td>${u.bookings}</td></tr>`)}</table></div></div>
      <div class="panel"><h2>Admin accounts</h2><p>${admins.map(a => `${a.full_name} <${a.email}>`).join(" · ")}</p><form class="inline" id="admin-form"><label>Name<input name="full_name"></label><label>Email<input type="email" name="email" required></label><label>Password<input type="password" name="password" minlength="8" required></label><button class="btn">Create admin</button></form></div>`;
  }
  render(html`<h1>Admin dashboard</h1><div class="tabs">${tabs.map(([k, l]) => html`<button class="${k === tab ? "on" : ""}" data-act="tab" data-id="${k}">${l}</button>`)}</div>${body}`);
  const bind = (id, fn) => { const f = document.getElementById(id); if (f) f.addEventListener("submit", async e => { e.preventDefault(); try { await fn(new FormData(f)); toast("Saved"); route(); } catch (er) { toast(er.message, "err"); } }); };
  bind("show-form", f => api("/admin/shows", { json: Object.fromEntries(f) }));
  bind("movie-form", f => { const o = Object.fromEntries(f); o.genre_ids = f.getAll("g"); return api("/admin/movies", { json: o }); });
  bind("theatre-form", f => api("/admin/theatres", { json: Object.fromEntries(f) }));
  bind("screen-form", f => api("/admin/screens", { json: Object.fromEntries(f) }));
  bind("cfg-form", f => api("/admin/pricing-config", { method: "PUT", json: Object.fromEntries(f) }));
  bind("admin-form", f => api("/admin/admins", { json: Object.fromEntries(f) }));
}

// ---------- actions (delegated) ----------
const page = { ctx: null };
const actions = {
  async logout() { await api("/auth/logout", { method: "POST" }); state.user = null; await refreshUser(); go("#/"); },
  seat: el => page.ctx.toggle(Number(el.dataset.id)),
  recommend: () => page.ctx.recommend(),
  book: () => page.ctx.book(),
  async watch(el) {
    if (!needUser()) return; const id = Number(el.dataset.id);
    try { el.dataset.in === "1" ? await api("/watchlist/" + id, { method: "DELETE" }) : await api("/watchlist", { json: { movie_id: id } }); toast(el.dataset.in === "1" ? "Removed from watchlist" : "Added to watchlist"); route(); } catch (e) { toast(e.message, "err"); }
  },
  async cancel(el) { if (!confirm("Cancel this booking? Seats will be released and refunded.")) return;
    try { await api(`/bookings/${el.dataset.id}/cancel`, { method: "POST" }); toast("Booking cancelled"); await refreshUser(); route(); } catch (e) { toast(e.message, "err"); } },
  "memory-form"(el) {
    const id = el.dataset.id; document.getElementById("mem-" + id).innerHTML = html`<form class="inline" data-memform="${id}" style="margin-top:12px"><label>Photo / video<input type="file" name="file" accept="image/*,video/mp4,video/webm" required></label><label>Caption<input name="caption" maxlength="300"></label><button class="btn sm">Upload</button></form>`.s;
  },
  async "del-memory"(el) { try { await api("/memories/" + el.dataset.id, { method: "DELETE" }); toast("Deleted"); route(); } catch (e) { toast(e.message, "err"); } },
  async "read-all"() { await api("/notifications/read", { method: "POST" }); await refreshUser(); route(); },
  tab(el) { state.adminTab = el.dataset.id; route(); },
  async "run-pricing"() { try { const r = await api("/admin/pricing/run", { method: "POST" }); toast(`Repriced ${r.repriced} upcoming shows`); route(); } catch (e) { toast(e.message, "err"); } },
  async "show-pricing"(el) {
    const r = await api(`/admin/shows/${el.dataset.id}/pricing`);
    document.getElementById("audit").innerHTML = html`<h3>Audit – show #${el.dataset.id}</h3><div class="tablewrap"><table><tr><th>Snapshot</th><th>O</th><th>S</th><th>P</th><th>Hours left</th><th>Demand</th></tr>${r.demand.map(d => html`<tr><td>${d.analysis_timestamp.slice(0, 19)}</td><td>${d.occupancy_rate}</td><td>${d.booking_speed}</td><td>${d.popularity_score}</td><td>${d.time_remaining}</td><td>${d.demand_score}</td></tr>`)}</table></div>
      <h3>Price history</h3>${r.history.length ? r.history.map(h => html`<div>${h.change_date}: ${money(h.old_price)} → ${money(h.new_price)} <span class="muted">(${h.change_reason})</span></div>`) : raw('<p class="muted">No price changes yet.</p>')}`.s;
  },
  async "del-show"(el) { if (!confirm("Delete this show?")) return; try { await api("/admin/shows/" + el.dataset.id, { method: "DELETE" }); toast("Deleted"); route(); } catch (e) { toast(e.message, "err"); } },
  async "del-movie"(el) { if (!confirm("Delete this movie?")) return; try { await api("/admin/movies/" + el.dataset.id, { method: "DELETE" }); toast("Deleted"); route(); } catch (e) { toast(e.message, "err"); } },
};
document.addEventListener("click", e => { const el = e.target.closest("[data-act]"); if (el && actions[el.dataset.act]) { e.preventDefault(); actions[el.dataset.act](el); } });
document.addEventListener("submit", async e => {
  const f = e.target.closest("[data-memform]"); if (!f) return; e.preventDefault();
  try { const r = await fetch(`/api/bookings/${f.dataset.memform}/memories`, { method: "POST", body: new FormData(f), credentials: "same-origin" });
    const d = await r.json(); if (!r.ok) throw new Error(d.error); toast("Memory saved"); route(); } catch (er) { toast(er.message, "err"); }
});

// ---------- router ----------
async function route() {
  clearInterval(state.timer); window.scrollTo(0, 0);
  const [, p, a] = (location.hash || "#/").split("/");
  try {
    if (state.user === null && !route.init) { route.init = true; await refreshUser(); }
    else await refreshUser();
    switch (p) {
      case "": case undefined: return await pageHome();
      case "movie": return await pageMovie(a);
      case "show": return await pageShow(a);
      case "bookings": return await pageBookings();
      case "memories": return await pageMemories();
      case "watchlist": return await pageWatchlist();
      case "notifications": return await pageNotifications();
      case "preferences": return await pagePreferences();
      case "login": return pageAuth("login");
      case "register": return pageAuth("register");
      case "admin-login": return pageAuth("admin");
      case "admin": return await pageAdmin();
      default: render(html`<p class="center muted">Page not found.</p>`);
    }
  } catch (e) {
    if (e.status === 401) return go("#/login");
    render(html`<div class="panel"><h2>Something went wrong</h2><p>${e.message}</p><a href="#/">Back to movies</a></div>`);
  }
}
window.addEventListener("hashchange", route);
route();
