-- Smart Movie Experience & Booking System -- SQLite schema (used by the running web app)
-- Same 20-table relational model as database/oracle/01_schema.sql.
-- Naming: reserved words from the Review-0 report are renamed: User->users, Admin->admins, Show->shows.
PRAGMA foreign_keys = ON;

CREATE TABLE theatre (
  theatre_id   INTEGER PRIMARY KEY,
  theatre_name TEXT NOT NULL,
  location     TEXT NOT NULL,
  city         TEXT NOT NULL
);

CREATE TABLE screen (
  screen_id     INTEGER PRIMARY KEY,
  theatre_id    INTEGER NOT NULL REFERENCES theatre(theatre_id) ON DELETE CASCADE,
  screen_number INTEGER NOT NULL CHECK (screen_number > 0),
  total_seats   INTEGER NOT NULL CHECK (total_seats > 0),
  UNIQUE (theatre_id, screen_number)
);

-- Weak entity: identified by (screen_id, seat_number)
CREATE TABLE seat (
  seat_id       INTEGER PRIMARY KEY,
  screen_id     INTEGER NOT NULL REFERENCES screen(screen_id) ON DELETE CASCADE,
  seat_number   TEXT NOT NULL,
  seat_type     TEXT NOT NULL CHECK (seat_type IN ('STANDARD','PREMIUM')),
  seat_location TEXT NOT NULL CHECK (seat_location IN ('FRONT','MIDDLE','BACK')),
  UNIQUE (screen_id, seat_number)
);

CREATE TABLE movie (
  movie_id       INTEGER PRIMARY KEY,
  title          TEXT NOT NULL,
  movie_language TEXT NOT NULL,
  certification  TEXT NOT NULL CHECK (certification IN ('U','UA','A')),
  duration_min   INTEGER NOT NULL CHECK (duration_min > 0),
  release_date   TEXT NOT NULL,
  synopsis       TEXT,
  avg_rating     REAL NOT NULL DEFAULT 0 CHECK (avg_rating BETWEEN 0 AND 5)
);

CREATE TABLE genre (
  genre_id   INTEGER PRIMARY KEY,
  genre_name TEXT NOT NULL UNIQUE
);

CREATE TABLE movie_genre (
  movie_id INTEGER NOT NULL REFERENCES movie(movie_id) ON DELETE CASCADE,
  genre_id INTEGER NOT NULL REFERENCES genre(genre_id) ON DELETE CASCADE,
  PRIMARY KEY (movie_id, genre_id)
);

CREATE TABLE users (
  user_id       INTEGER PRIMARY KEY,
  full_name     TEXT NOT NULL,
  email         TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  phone         TEXT,
  join_date     TEXT NOT NULL
);

CREATE TABLE admins (
  admin_id      INTEGER PRIMARY KEY,
  full_name     TEXT NOT NULL,
  email         TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL
);

CREATE TABLE pricing_config (
  config_id            INTEGER PRIMARY KEY,
  admin_id             INTEGER NOT NULL REFERENCES admins(admin_id),
  w_occupancy          REAL NOT NULL CHECK (w_occupancy     BETWEEN 0 AND 1),
  w_booking_speed      REAL NOT NULL CHECK (w_booking_speed BETWEEN 0 AND 1),
  w_popularity         REAL NOT NULL CHECK (w_popularity    BETWEEN 0 AND 1),
  w_urgency            REAL NOT NULL CHECK (w_urgency       BETWEEN 0 AND 1),
  elasticity_k         REAL NOT NULL CHECK (elasticity_k > 0),
  booking_window_hours INTEGER NOT NULL CHECK (booking_window_hours > 0),
  updated_at           TEXT NOT NULL,
  CHECK (ABS(w_occupancy + w_booking_speed + w_popularity + w_urgency - 1) < 0.0001)
);

CREATE TABLE user_genre_preference (
  user_id  INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  genre_id INTEGER NOT NULL REFERENCES genre(genre_id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, genre_id)
);

CREATE TABLE shows (
  show_id       INTEGER PRIMARY KEY,
  movie_id      INTEGER NOT NULL REFERENCES movie(movie_id),
  screen_id     INTEGER NOT NULL REFERENCES screen(screen_id),
  admin_id      INTEGER NOT NULL REFERENCES admins(admin_id),
  show_date     TEXT NOT NULL,            -- YYYY-MM-DD
  show_time     TEXT NOT NULL,            -- HH:MM (24h)
  base_price    REAL NOT NULL CHECK (base_price > 0),
  current_price REAL NOT NULL,
  min_price     REAL NOT NULL,
  max_price     REAL NOT NULL,
  UNIQUE (screen_id, show_date, show_time),
  CHECK (min_price <= base_price AND base_price <= max_price),
  CHECK (min_price <= current_price AND current_price <= max_price)
);

CREATE TABLE booking (
  booking_id     INTEGER PRIMARY KEY,
  user_id        INTEGER NOT NULL REFERENCES users(user_id),
  show_id        INTEGER NOT NULL REFERENCES shows(show_id),
  booking_time   TEXT NOT NULL,
  total_amount   REAL NOT NULL CHECK (total_amount >= 0),
  booking_status TEXT NOT NULL CHECK (booking_status IN ('CONFIRMED','CANCELLED')),
  UNIQUE (booking_id, show_id)            -- target for the composite FK below
);

-- M:N Booking-Seat. UNIQUE(show_id, seat_id) is the database-level double-booking guard.
CREATE TABLE booking_seat (
  booking_id INTEGER NOT NULL,
  seat_id    INTEGER NOT NULL REFERENCES seat(seat_id),
  show_id    INTEGER NOT NULL,
  PRIMARY KEY (booking_id, seat_id),
  CONSTRAINT uq_show_seat UNIQUE (show_id, seat_id),
  FOREIGN KEY (booking_id, show_id) REFERENCES booking(booking_id, show_id) ON DELETE CASCADE
);

CREATE TABLE payment (
  payment_id     INTEGER PRIMARY KEY,
  booking_id     INTEGER NOT NULL UNIQUE REFERENCES booking(booking_id),
  amount         REAL NOT NULL CHECK (amount >= 0),
  payment_date   TEXT NOT NULL,
  payment_method TEXT NOT NULL CHECK (payment_method IN ('UPI','CARD','NETBANKING','WALLET')),
  payment_status TEXT NOT NULL CHECK (payment_status IN ('SUCCESS','FAILED','REFUNDED','PENDING'))
);

CREATE TABLE review (
  review_id      INTEGER PRIMARY KEY,
  user_id        INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  movie_id       INTEGER NOT NULL REFERENCES movie(movie_id) ON DELETE CASCADE,
  rating         INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  review_comment TEXT,
  review_date    TEXT NOT NULL
);

CREATE TABLE movie_memory (
  memory_id   INTEGER PRIMARY KEY,
  booking_id  INTEGER NOT NULL REFERENCES booking(booking_id) ON DELETE CASCADE,
  user_id     INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  movie_id    INTEGER NOT NULL REFERENCES movie(movie_id) ON DELETE CASCADE,
  media_url   TEXT NOT NULL,
  caption     TEXT,
  upload_date TEXT NOT NULL
);

CREATE TABLE notification (
  notification_id INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  message    TEXT NOT NULL,
  notif_type TEXT NOT NULL CHECK (notif_type IN ('BOOKING','REMINDER','NEW_RELEASE','WATCHLIST','SYSTEM')),
  sent_date  TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'UNREAD' CHECK (status IN ('UNREAD','READ'))
);

CREATE TABLE watchlist (
  watchlist_id    INTEGER PRIMARY KEY,
  user_id         INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  movie_id        INTEGER NOT NULL REFERENCES movie(movie_id) ON DELETE CASCADE,
  added_date      TEXT NOT NULL,
  notified_status TEXT NOT NULL DEFAULT 'N' CHECK (notified_status IN ('Y','N')),
  UNIQUE (user_id, movie_id)
);

-- Weak entity of Show
CREATE TABLE demand_analysis (
  demand_id          INTEGER PRIMARY KEY,
  show_id            INTEGER NOT NULL REFERENCES shows(show_id) ON DELETE CASCADE,
  analysis_timestamp TEXT NOT NULL,
  occupancy_rate     REAL NOT NULL CHECK (occupancy_rate  BETWEEN 0 AND 1),
  booking_speed      REAL NOT NULL CHECK (booking_speed   BETWEEN 0 AND 1),
  popularity_score   REAL NOT NULL CHECK (popularity_score BETWEEN 0 AND 1),
  time_remaining     REAL NOT NULL CHECK (time_remaining >= 0),   -- hours
  demand_score       REAL NOT NULL CHECK (demand_score    BETWEEN 0 AND 1),
  UNIQUE (show_id, analysis_timestamp)
);

-- Weak entity of Show
CREATE TABLE price_history (
  price_id      INTEGER PRIMARY KEY,
  show_id       INTEGER NOT NULL REFERENCES shows(show_id) ON DELETE CASCADE,
  change_date   TEXT NOT NULL,
  old_price     REAL NOT NULL,
  new_price     REAL NOT NULL,
  change_reason TEXT NOT NULL
);

-- ---------- indexes on foreign keys / hot paths ----------
CREATE INDEX ix_shows_movie     ON shows(movie_id);
CREATE INDEX ix_shows_date      ON shows(show_date, show_time);
CREATE INDEX ix_booking_user    ON booking(user_id);
CREATE INDEX ix_booking_show    ON booking(show_id);
CREATE INDEX ix_review_movie    ON review(movie_id);
CREATE INDEX ix_notif_user      ON notification(user_id);
CREATE INDEX ix_demand_show     ON demand_analysis(show_id);
CREATE INDEX ix_price_show      ON price_history(show_id);

-- ---------- triggers (business rules at database level) ----------
-- A seat can only be booked for a show that runs on the seat's own screen.
CREATE TRIGGER trg_booking_seat_screen
BEFORE INSERT ON booking_seat
BEGIN
  SELECT RAISE(ABORT, 'Seat does not belong to the screen of this show')
  WHERE (SELECT screen_id FROM seat  WHERE seat_id = NEW.seat_id)
     <> (SELECT screen_id FROM shows WHERE show_id = NEW.show_id);
END;

-- Keep movie.avg_rating in sync with reviews.
CREATE TRIGGER trg_review_ai AFTER INSERT ON review BEGIN
  UPDATE movie SET avg_rating = (SELECT ROUND(AVG(rating),2) FROM review WHERE movie_id = NEW.movie_id)
  WHERE movie_id = NEW.movie_id;
END;
CREATE TRIGGER trg_review_au AFTER UPDATE ON review BEGIN
  UPDATE movie SET avg_rating = (SELECT ROUND(AVG(rating),2) FROM review WHERE movie_id = NEW.movie_id)
  WHERE movie_id = NEW.movie_id;
END;
CREATE TRIGGER trg_review_ad AFTER DELETE ON review BEGIN
  UPDATE movie SET avg_rating = COALESCE((SELECT ROUND(AVG(rating),2) FROM review WHERE movie_id = OLD.movie_id),0)
  WHERE movie_id = OLD.movie_id;
END;
