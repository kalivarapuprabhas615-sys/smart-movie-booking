-- =====================================================================================
-- Smart Movie Experience & Booking System  --  Oracle DDL (20 tables, 3NF)
-- Course BCSE302P | Kalivarapu Prabhas (24BCE2279), Shanmukha Abhinav (24BAI0238)
-- Written for Oracle 11g+ (sequences + triggers, no identity columns / no 12c-only syntax).
-- NOTE: this script has NOT been executed in the authoring sandbox (no Oracle server there).
--       Run it in SQL*Plus / SQL Developer / Live SQL, then 02_plsql.sql, then 03_seed.sql.
-- Reserved-word renames vs Review 0: User->USERS, Admin->ADMINS, Show->SHOWS,
--   Movie.language->movie_language, Movie.description->synopsis, Review.comment->review_comment,
--   Movie.duration->duration_min, Theatre.name->theatre_name.
-- =====================================================================================

-- Re-runnable: drop everything first (ignore "does not exist" errors on first run)
BEGIN
  FOR t IN (SELECT table_name FROM user_tables WHERE table_name IN (
      'PRICE_HISTORY','DEMAND_ANALYSIS','WATCHLIST','NOTIFICATION','MOVIE_MEMORY','REVIEW','PAYMENT',
      'BOOKING_SEAT','BOOKING','SHOWS','USER_GENRE_PREFERENCE','PRICING_CONFIG','ADMINS','USERS',
      'MOVIE_GENRE','GENRE','MOVIE','SEAT','SCREEN','THEATRE')) LOOP
    EXECUTE IMMEDIATE 'DROP TABLE ' || t.table_name || ' CASCADE CONSTRAINTS PURGE';
  END LOOP;
  FOR s IN (SELECT sequence_name FROM user_sequences WHERE sequence_name LIKE 'SEQ\_%' ESCAPE '\') LOOP
    EXECUTE IMMEDIATE 'DROP SEQUENCE ' || s.sequence_name;
  END LOOP;
END;
/

CREATE TABLE theatre (
  theatre_id   NUMBER(6)     CONSTRAINT pk_theatre PRIMARY KEY,
  theatre_name VARCHAR2(100) NOT NULL,
  location     VARCHAR2(150) NOT NULL,
  city         VARCHAR2(60)  NOT NULL
);

CREATE TABLE screen (
  screen_id     NUMBER(6) CONSTRAINT pk_screen PRIMARY KEY,
  theatre_id    NUMBER(6) NOT NULL CONSTRAINT fk_screen_theatre REFERENCES theatre(theatre_id) ON DELETE CASCADE,
  screen_number NUMBER(3) NOT NULL CONSTRAINT ck_screen_no CHECK (screen_number > 0),
  total_seats   NUMBER(4) NOT NULL CONSTRAINT ck_screen_seats CHECK (total_seats > 0),
  CONSTRAINT uq_screen UNIQUE (theatre_id, screen_number)
);

-- Weak entity: identified by (screen_id, seat_number)
CREATE TABLE seat (
  seat_id       NUMBER(8)    CONSTRAINT pk_seat PRIMARY KEY,
  screen_id     NUMBER(6)    NOT NULL CONSTRAINT fk_seat_screen REFERENCES screen(screen_id) ON DELETE CASCADE,
  seat_number   VARCHAR2(5)  NOT NULL,
  seat_type     VARCHAR2(10) NOT NULL CONSTRAINT ck_seat_type CHECK (seat_type IN ('STANDARD','PREMIUM')),
  seat_location VARCHAR2(10) NOT NULL CONSTRAINT ck_seat_loc  CHECK (seat_location IN ('FRONT','MIDDLE','BACK')),
  CONSTRAINT uq_seat UNIQUE (screen_id, seat_number)
);

CREATE TABLE movie (
  movie_id       NUMBER(6)     CONSTRAINT pk_movie PRIMARY KEY,
  title          VARCHAR2(150) NOT NULL,
  movie_language VARCHAR2(30)  NOT NULL,
  certification  VARCHAR2(3)   NOT NULL CONSTRAINT ck_movie_cert CHECK (certification IN ('U','UA','A')),
  duration_min   NUMBER(4)     NOT NULL CONSTRAINT ck_movie_dur CHECK (duration_min > 0),
  release_date   DATE          NOT NULL,
  synopsis       VARCHAR2(1000),
  avg_rating     NUMBER(3,2)   DEFAULT 0 NOT NULL CONSTRAINT ck_movie_rating CHECK (avg_rating BETWEEN 0 AND 5)
);

CREATE TABLE genre (
  genre_id   NUMBER(4)    CONSTRAINT pk_genre PRIMARY KEY,
  genre_name VARCHAR2(40) NOT NULL CONSTRAINT uq_genre_name UNIQUE
);

CREATE TABLE movie_genre (
  movie_id NUMBER(6) NOT NULL CONSTRAINT fk_mg_movie REFERENCES movie(movie_id) ON DELETE CASCADE,
  genre_id NUMBER(4) NOT NULL CONSTRAINT fk_mg_genre REFERENCES genre(genre_id) ON DELETE CASCADE,
  CONSTRAINT pk_movie_genre PRIMARY KEY (movie_id, genre_id)
);

CREATE TABLE users (
  user_id       NUMBER(8)     CONSTRAINT pk_users PRIMARY KEY,
  full_name     VARCHAR2(100) NOT NULL,
  email         VARCHAR2(150) NOT NULL CONSTRAINT uq_users_email UNIQUE,
  password_hash VARCHAR2(255) NOT NULL,          -- salted hash (never plain text)
  phone         VARCHAR2(15),
  join_date     DATE DEFAULT SYSDATE NOT NULL
);

CREATE TABLE admins (
  admin_id      NUMBER(4)     CONSTRAINT pk_admins PRIMARY KEY,
  full_name     VARCHAR2(100) NOT NULL,
  email         VARCHAR2(150) NOT NULL CONSTRAINT uq_admins_email UNIQUE,
  password_hash VARCHAR2(255) NOT NULL
);

CREATE TABLE pricing_config (
  config_id            NUMBER(6) CONSTRAINT pk_pricing_config PRIMARY KEY,
  admin_id             NUMBER(4) NOT NULL CONSTRAINT fk_pc_admin REFERENCES admins(admin_id),
  w_occupancy          NUMBER(4,3) NOT NULL CONSTRAINT ck_pc_w1 CHECK (w_occupancy     BETWEEN 0 AND 1),
  w_booking_speed      NUMBER(4,3) NOT NULL CONSTRAINT ck_pc_w2 CHECK (w_booking_speed BETWEEN 0 AND 1),
  w_popularity         NUMBER(4,3) NOT NULL CONSTRAINT ck_pc_w3 CHECK (w_popularity    BETWEEN 0 AND 1),
  w_urgency            NUMBER(4,3) NOT NULL CONSTRAINT ck_pc_w4 CHECK (w_urgency       BETWEEN 0 AND 1),
  elasticity_k         NUMBER(4,3) NOT NULL CONSTRAINT ck_pc_k  CHECK (elasticity_k > 0),
  booking_window_hours NUMBER(4)   NOT NULL CONSTRAINT ck_pc_win CHECK (booking_window_hours > 0),
  updated_at           TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT ck_pc_sum CHECK (ABS(w_occupancy + w_booking_speed + w_popularity + w_urgency - 1) < 0.0001)
);

CREATE TABLE user_genre_preference (
  user_id  NUMBER(8) NOT NULL CONSTRAINT fk_ugp_user  REFERENCES users(user_id) ON DELETE CASCADE,
  genre_id NUMBER(4) NOT NULL CONSTRAINT fk_ugp_genre REFERENCES genre(genre_id) ON DELETE CASCADE,
  CONSTRAINT pk_ugp PRIMARY KEY (user_id, genre_id)
);

CREATE TABLE shows (
  show_id       NUMBER(8) CONSTRAINT pk_shows PRIMARY KEY,
  movie_id      NUMBER(6) NOT NULL CONSTRAINT fk_shows_movie  REFERENCES movie(movie_id),
  screen_id     NUMBER(6) NOT NULL CONSTRAINT fk_shows_screen REFERENCES screen(screen_id),
  admin_id      NUMBER(4) NOT NULL CONSTRAINT fk_shows_admin  REFERENCES admins(admin_id),
  show_date     DATE NOT NULL,
  show_time     VARCHAR2(5) NOT NULL CONSTRAINT ck_show_time CHECK (REGEXP_LIKE(show_time, '^([01][0-9]|2[0-3]):[0-5][0-9]$')),
  base_price    NUMBER(8,2) NOT NULL CONSTRAINT ck_show_base CHECK (base_price > 0),
  current_price NUMBER(8,2) NOT NULL,
  min_price     NUMBER(8,2) NOT NULL,
  max_price     NUMBER(8,2) NOT NULL,
  CONSTRAINT uq_show_slot UNIQUE (screen_id, show_date, show_time),
  CONSTRAINT ck_show_base_band  CHECK (min_price <= base_price AND base_price <= max_price),
  CONSTRAINT ck_show_price_band CHECK (min_price <= current_price AND current_price <= max_price)
);

CREATE TABLE booking (
  booking_id     NUMBER(10) CONSTRAINT pk_booking PRIMARY KEY,
  user_id        NUMBER(8)  NOT NULL CONSTRAINT fk_booking_user REFERENCES users(user_id),
  show_id        NUMBER(8)  NOT NULL CONSTRAINT fk_booking_show REFERENCES shows(show_id),
  booking_time   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  total_amount   NUMBER(10,2) NOT NULL CONSTRAINT ck_booking_amt CHECK (total_amount >= 0),
  booking_status VARCHAR2(10) NOT NULL CONSTRAINT ck_booking_status CHECK (booking_status IN ('CONFIRMED','CANCELLED')),
  CONSTRAINT uq_booking_show UNIQUE (booking_id, show_id)   -- target of composite FK from booking_seat
);

-- M:N Booking-Seat.  UNIQUE (show_id, seat_id) = database-level double-booking prevention.
CREATE TABLE booking_seat (
  booking_id NUMBER(10) NOT NULL,
  seat_id    NUMBER(8)  NOT NULL CONSTRAINT fk_bs_seat REFERENCES seat(seat_id),
  show_id    NUMBER(8)  NOT NULL,
  CONSTRAINT pk_booking_seat PRIMARY KEY (booking_id, seat_id),
  CONSTRAINT uq_show_seat UNIQUE (show_id, seat_id),
  CONSTRAINT fk_bs_booking FOREIGN KEY (booking_id, show_id) REFERENCES booking(booking_id, show_id) ON DELETE CASCADE
);

CREATE TABLE payment (
  payment_id     NUMBER(10) CONSTRAINT pk_payment PRIMARY KEY,
  booking_id     NUMBER(10) NOT NULL CONSTRAINT uq_payment_booking UNIQUE
                 CONSTRAINT fk_payment_booking REFERENCES booking(booking_id),
  amount         NUMBER(10,2) NOT NULL CONSTRAINT ck_pay_amt CHECK (amount >= 0),
  payment_date   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  payment_method VARCHAR2(12) NOT NULL CONSTRAINT ck_pay_method CHECK (payment_method IN ('UPI','CARD','NETBANKING','WALLET')),
  payment_status VARCHAR2(10) NOT NULL CONSTRAINT ck_pay_status CHECK (payment_status IN ('SUCCESS','FAILED','REFUNDED','PENDING'))
);

CREATE TABLE review (
  review_id      NUMBER(10) CONSTRAINT pk_review PRIMARY KEY,
  user_id        NUMBER(8)  NOT NULL CONSTRAINT fk_review_user  REFERENCES users(user_id) ON DELETE CASCADE,
  movie_id       NUMBER(6)  NOT NULL CONSTRAINT fk_review_movie REFERENCES movie(movie_id) ON DELETE CASCADE,
  rating         NUMBER(1)  NOT NULL CONSTRAINT ck_review_rating CHECK (rating BETWEEN 1 AND 5),
  review_comment VARCHAR2(1000),
  review_date    TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE TABLE movie_memory (
  memory_id   NUMBER(10) CONSTRAINT pk_movie_memory PRIMARY KEY,
  booking_id  NUMBER(10) NOT NULL CONSTRAINT fk_mm_booking REFERENCES booking(booking_id) ON DELETE CASCADE,
  user_id     NUMBER(8)  NOT NULL CONSTRAINT fk_mm_user    REFERENCES users(user_id) ON DELETE CASCADE,
  movie_id    NUMBER(6)  NOT NULL CONSTRAINT fk_mm_movie   REFERENCES movie(movie_id) ON DELETE CASCADE,
  media_url   VARCHAR2(300) NOT NULL,
  caption     VARCHAR2(300),
  upload_date TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE TABLE notification (
  notification_id NUMBER(10) CONSTRAINT pk_notification PRIMARY KEY,
  user_id    NUMBER(8) NOT NULL CONSTRAINT fk_notif_user REFERENCES users(user_id) ON DELETE CASCADE,
  message    VARCHAR2(500) NOT NULL,
  notif_type VARCHAR2(12) NOT NULL CONSTRAINT ck_notif_type CHECK (notif_type IN ('BOOKING','REMINDER','NEW_RELEASE','WATCHLIST','SYSTEM')),
  sent_date  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  status     VARCHAR2(6) DEFAULT 'UNREAD' NOT NULL CONSTRAINT ck_notif_status CHECK (status IN ('UNREAD','READ'))
);

CREATE TABLE watchlist (
  watchlist_id    NUMBER(10) CONSTRAINT pk_watchlist PRIMARY KEY,
  user_id         NUMBER(8) NOT NULL CONSTRAINT fk_wl_user  REFERENCES users(user_id) ON DELETE CASCADE,
  movie_id        NUMBER(6) NOT NULL CONSTRAINT fk_wl_movie REFERENCES movie(movie_id) ON DELETE CASCADE,
  added_date      TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  notified_status CHAR(1) DEFAULT 'N' NOT NULL CONSTRAINT ck_wl_notified CHECK (notified_status IN ('Y','N')),
  CONSTRAINT uq_watchlist UNIQUE (user_id, movie_id)
);

-- Weak entity of Show
CREATE TABLE demand_analysis (
  demand_id          NUMBER(12) CONSTRAINT pk_demand PRIMARY KEY,
  show_id            NUMBER(8)  NOT NULL CONSTRAINT fk_da_show REFERENCES shows(show_id) ON DELETE CASCADE,
  analysis_timestamp TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  occupancy_rate     NUMBER(5,4) NOT NULL CONSTRAINT ck_da_o CHECK (occupancy_rate   BETWEEN 0 AND 1),
  booking_speed      NUMBER(5,4) NOT NULL CONSTRAINT ck_da_s CHECK (booking_speed    BETWEEN 0 AND 1),
  popularity_score   NUMBER(5,4) NOT NULL CONSTRAINT ck_da_p CHECK (popularity_score BETWEEN 0 AND 1),
  time_remaining     NUMBER(8,2) NOT NULL CONSTRAINT ck_da_t CHECK (time_remaining >= 0),   -- hours
  demand_score       NUMBER(5,4) NOT NULL CONSTRAINT ck_da_d CHECK (demand_score     BETWEEN 0 AND 1),
  CONSTRAINT uq_demand UNIQUE (show_id, analysis_timestamp)
);

-- Weak entity of Show
CREATE TABLE price_history (
  price_id      NUMBER(12) CONSTRAINT pk_price_history PRIMARY KEY,
  show_id       NUMBER(8)  NOT NULL CONSTRAINT fk_ph_show REFERENCES shows(show_id) ON DELETE CASCADE,
  change_date   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
  old_price     NUMBER(8,2) NOT NULL,
  new_price     NUMBER(8,2) NOT NULL,
  change_reason VARCHAR2(100) NOT NULL
);

-- ---------- indexes on foreign keys / hot paths ----------
CREATE INDEX ix_shows_movie  ON shows(movie_id);
CREATE INDEX ix_shows_date   ON shows(show_date, show_time);
CREATE INDEX ix_booking_user ON booking(user_id);
CREATE INDEX ix_booking_show ON booking(show_id);
CREATE INDEX ix_review_movie ON review(movie_id);
CREATE INDEX ix_notif_user   ON notification(user_id);
CREATE INDEX ix_demand_show  ON demand_analysis(show_id);
CREATE INDEX ix_price_show   ON price_history(show_id);

-- ---------- sequences + BEFORE INSERT triggers for surrogate keys ----------
-- Sample data uses explicit ids (1..N); sequences start at 10000 so new rows never collide.
BEGIN
  FOR r IN (SELECT 'THEATRE' t,'THEATRE_ID' c FROM dual UNION ALL SELECT 'SCREEN','SCREEN_ID' FROM dual
            UNION ALL SELECT 'SEAT','SEAT_ID' FROM dual UNION ALL SELECT 'MOVIE','MOVIE_ID' FROM dual
            UNION ALL SELECT 'GENRE','GENRE_ID' FROM dual UNION ALL SELECT 'USERS','USER_ID' FROM dual
            UNION ALL SELECT 'ADMINS','ADMIN_ID' FROM dual UNION ALL SELECT 'PRICING_CONFIG','CONFIG_ID' FROM dual
            UNION ALL SELECT 'SHOWS','SHOW_ID' FROM dual UNION ALL SELECT 'BOOKING','BOOKING_ID' FROM dual
            UNION ALL SELECT 'PAYMENT','PAYMENT_ID' FROM dual UNION ALL SELECT 'REVIEW','REVIEW_ID' FROM dual
            UNION ALL SELECT 'MOVIE_MEMORY','MEMORY_ID' FROM dual UNION ALL SELECT 'NOTIFICATION','NOTIFICATION_ID' FROM dual
            UNION ALL SELECT 'WATCHLIST','WATCHLIST_ID' FROM dual UNION ALL SELECT 'DEMAND_ANALYSIS','DEMAND_ID' FROM dual
            UNION ALL SELECT 'PRICE_HISTORY','PRICE_ID' FROM dual) LOOP
    EXECUTE IMMEDIATE 'CREATE SEQUENCE seq_' || r.t || ' START WITH 10000 INCREMENT BY 1 NOCACHE';
    EXECUTE IMMEDIATE 'CREATE OR REPLACE TRIGGER trg_' || r.t || '_id BEFORE INSERT ON ' || r.t ||
                      ' FOR EACH ROW WHEN (NEW.' || r.c || ' IS NULL) BEGIN :NEW.' || r.c ||
                      ' := seq_' || r.t || '.NEXTVAL; END;';
  END LOOP;
END;
/

-- Seat can only be booked for a show running on the seat's own screen.
CREATE OR REPLACE TRIGGER trg_booking_seat_screen
BEFORE INSERT ON booking_seat
FOR EACH ROW
DECLARE
  v_seat_screen  NUMBER;
  v_show_screen  NUMBER;
BEGIN
  SELECT screen_id INTO v_seat_screen FROM seat  WHERE seat_id = :NEW.seat_id;
  SELECT screen_id INTO v_show_screen FROM shows WHERE show_id = :NEW.show_id;
  IF v_seat_screen <> v_show_screen THEN
    RAISE_APPLICATION_ERROR(-20010, 'Seat does not belong to the screen of this show');
  END IF;
END;
/

-- Keep movie.avg_rating in sync with reviews (statement-level => no mutating-table error).
CREATE OR REPLACE TRIGGER trg_review_avg
AFTER INSERT OR UPDATE OR DELETE ON review
BEGIN
  UPDATE movie m
     SET avg_rating = NVL((SELECT ROUND(AVG(r.rating), 2) FROM review r WHERE r.movie_id = m.movie_id), 0);
END;
/

SELECT table_name FROM user_tables ORDER BY table_name;
