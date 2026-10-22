-- =====================================================================================
-- Oracle PL/SQL: dynamic pricing engine, atomic booking, cancellation, scheduler job
-- NOTE: NOT executed in the authoring sandbox (no Oracle server). Run after 01_schema.sql.
-- Logic mirrors backend/pricing.py exactly (that Python version IS tested).
-- =====================================================================================

CREATE OR REPLACE PACKAGE pkg_dynamic_pricing AS
  -- Recompute demand + price for one upcoming show (writes Demand_Analysis, Price_History, Shows.current_price)
  PROCEDURE recompute_show(p_show_id IN NUMBER);
  -- Recompute every show that has not started yet
  PROCEDURE recompute_all;
  -- multiplier = 1 + k * (demand_score - 0.5), rounded to 3 decimals
  FUNCTION calc_multiplier(p_score IN NUMBER, p_k IN NUMBER) RETURN NUMBER;
END pkg_dynamic_pricing;
/

CREATE OR REPLACE PACKAGE BODY pkg_dynamic_pricing AS

  FUNCTION calc_multiplier(p_score IN NUMBER, p_k IN NUMBER) RETURN NUMBER IS
  BEGIN
    RETURN ROUND(1 + p_k * (p_score - 0.5), 3);
  END;

  PROCEDURE recompute_show(p_show_id IN NUMBER) IS
    v_cfg        pricing_config%ROWTYPE;
    v_base       shows.base_price%TYPE;
    v_min        shows.min_price%TYPE;
    v_max        shows.max_price%TYPE;
    v_old        shows.current_price%TYPE;
    v_new        NUMBER;
    v_raw        NUMBER;
    v_total      NUMBER;
    v_rating     NUMBER;
    v_movie      NUMBER;
    v_start      DATE;
    v_hours      NUMBER;
    v_booked     NUMBER;
    v_recent     NUMBER;
    v_speed_ref  NUMBER;
    v_wl         NUMBER;
    v_wl_ref     NUMBER;
    v_o NUMBER; v_s NUMBER; v_p NUMBER; v_u NUMBER;
    v_score      NUMBER;
    v_mult       NUMBER;
    v_reason     VARCHAR2(100);
  BEGIN
    -- latest pricing configuration
    SELECT * INTO v_cfg FROM (SELECT * FROM pricing_config ORDER BY updated_at DESC, config_id DESC) WHERE ROWNUM = 1;

    SELECT s.base_price, s.min_price, s.max_price, s.current_price, s.movie_id, sc.total_seats, m.avg_rating,
           TO_DATE(TO_CHAR(s.show_date, 'YYYY-MM-DD') || ' ' || s.show_time, 'YYYY-MM-DD HH24:MI')
      INTO v_base, v_min, v_max, v_old, v_movie, v_total, v_rating, v_start
      FROM shows s JOIN screen sc ON sc.screen_id = s.screen_id JOIN movie m ON m.movie_id = s.movie_id
     WHERE s.show_id = p_show_id;

    IF v_start <= SYSDATE THEN RETURN; END IF;           -- only upcoming shows are repriced
    v_hours := (v_start - SYSDATE) * 24;

    -- O: occupancy
    SELECT COUNT(*) INTO v_booked FROM booking_seat WHERE show_id = p_show_id;
    v_o := LEAST(1, v_booked / v_total);

    -- S: booking speed = bookings in last 60 min / busiest hour seen for this capacity class (floor 5)
    SELECT COUNT(*) INTO v_recent FROM booking
     WHERE show_id = p_show_id AND booking_status = 'CONFIRMED' AND booking_time >= SYSTIMESTAMP - INTERVAL '60' MINUTE;
    SELECT NVL(MAX(c), 0) INTO v_speed_ref FROM (
      SELECT COUNT(*) c FROM booking b JOIN shows s2 ON s2.show_id = b.show_id JOIN screen sc2 ON sc2.screen_id = s2.screen_id
       WHERE sc2.total_seats = v_total AND b.booking_status = 'CONFIRMED'
       GROUP BY b.show_id, TRUNC(b.booking_time, 'HH24'));
    v_speed_ref := GREATEST(v_speed_ref, 5);
    v_s := LEAST(1, v_recent / v_speed_ref);

    -- P: popularity = 0.6*(avg_rating/5) + 0.4*min(1, watchlist_count/reference)
    SELECT COUNT(*) INTO v_wl FROM watchlist WHERE movie_id = v_movie;
    SELECT NVL(MAX(cnt), 0) INTO v_wl_ref FROM (SELECT COUNT(*) cnt FROM watchlist GROUP BY movie_id);
    v_wl_ref := GREATEST(v_wl_ref, 5);
    v_p := 0.6 * (v_rating / 5) + 0.4 * LEAST(1, v_wl / v_wl_ref);

    -- U: urgency = 1 - min(1, hours_remaining / booking_window_hours)
    v_u := 1 - LEAST(1, v_hours / v_cfg.booking_window_hours);

    v_score := v_cfg.w_occupancy * v_o + v_cfg.w_booking_speed * v_s + v_cfg.w_popularity * v_p + v_cfg.w_urgency * v_u;
    v_score := LEAST(1, GREATEST(0, v_score));
    v_mult  := calc_multiplier(v_score, v_cfg.elasticity_k);
    v_raw   := FLOOR(v_base * v_mult + 0.5);               -- nearest rupee, half up
    v_new   := LEAST(v_max, GREATEST(v_min, v_raw));       -- CLAMP(raw, min, max)

    INSERT INTO demand_analysis (show_id, analysis_timestamp, occupancy_rate, booking_speed, popularity_score, time_remaining, demand_score)
    VALUES (p_show_id, SYSTIMESTAMP, ROUND(v_o, 4), ROUND(v_s, 4), ROUND(v_p, 4), ROUND(v_hours, 2), ROUND(v_score, 4));

    IF v_new <> v_old THEN
      v_reason := 'demand_recalculation';
      IF v_raw > v_max THEN v_reason := 'demand_recalculation (clamped to max)';
      ELSIF v_raw < v_min THEN v_reason := 'demand_recalculation (clamped to min)'; END IF;
      INSERT INTO price_history (show_id, change_date, old_price, new_price, change_reason)
      VALUES (p_show_id, SYSTIMESTAMP, v_old, v_new, v_reason);
      UPDATE shows SET current_price = v_new WHERE show_id = p_show_id;
    END IF;
  END recompute_show;

  PROCEDURE recompute_all IS
  BEGIN
    FOR r IN (SELECT show_id FROM shows
               WHERE TO_DATE(TO_CHAR(show_date, 'YYYY-MM-DD') || ' ' || show_time, 'YYYY-MM-DD HH24:MI') > SYSDATE) LOOP
      recompute_show(r.show_id);
    END LOOP;
  END recompute_all;

END pkg_dynamic_pricing;
/

-- ---------------------------------------------------------------------------------
-- Atomic booking: locks the show row, inserts booking + seats + payment, notifies, reprices.
-- The UNIQUE (show_id, seat_id) constraint is the last line of defence against double-booking.
-- ---------------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE book_seats(
  p_user_id    IN  NUMBER,
  p_show_id    IN  NUMBER,
  p_seat_ids   IN  SYS.ODCINUMBERLIST,
  p_method     IN  VARCHAR2,
  p_booking_id OUT NUMBER) IS
  v_price  shows.current_price%TYPE;
  v_start  DATE;
  v_title  movie.title%TYPE;
BEGIN
  IF p_seat_ids IS NULL OR p_seat_ids.COUNT = 0 THEN
    RAISE_APPLICATION_ERROR(-20001, 'Select at least one seat');
  END IF;
  SELECT s.current_price, m.title,
         TO_DATE(TO_CHAR(s.show_date, 'YYYY-MM-DD') || ' ' || s.show_time, 'YYYY-MM-DD HH24:MI')
    INTO v_price, v_title, v_start
    FROM shows s JOIN movie m ON m.movie_id = s.movie_id
   WHERE s.show_id = p_show_id FOR UPDATE OF s.current_price;     -- serialise bookings per show
  IF v_start <= SYSDATE THEN RAISE_APPLICATION_ERROR(-20002, 'Show has already started'); END IF;

  p_booking_id := seq_booking.NEXTVAL;
  INSERT INTO booking (booking_id, user_id, show_id, booking_time, total_amount, booking_status)
  VALUES (p_booking_id, p_user_id, p_show_id, SYSTIMESTAMP, v_price * p_seat_ids.COUNT, 'CONFIRMED');
  BEGIN
    FOR i IN 1 .. p_seat_ids.COUNT LOOP
      INSERT INTO booking_seat (booking_id, seat_id, show_id) VALUES (p_booking_id, p_seat_ids(i), p_show_id);
    END LOOP;
  EXCEPTION
    WHEN DUP_VAL_ON_INDEX THEN
      ROLLBACK;
      RAISE_APPLICATION_ERROR(-20003, 'One or more seats were just booked by someone else');
  END;
  INSERT INTO payment (booking_id, amount, payment_date, payment_method, payment_status)
  VALUES (p_booking_id, v_price * p_seat_ids.COUNT, SYSTIMESTAMP, p_method, 'SUCCESS');
  INSERT INTO notification (user_id, message, notif_type)
  VALUES (p_user_id, 'Booking #' || p_booking_id || ' confirmed for ' || v_title || '.', 'BOOKING');
  pkg_dynamic_pricing.recompute_show(p_show_id);
  COMMIT;
END book_seats;
/

CREATE OR REPLACE PROCEDURE cancel_booking(p_booking_id IN NUMBER, p_user_id IN NUMBER) IS
  v_show   NUMBER;
  v_status booking.booking_status%TYPE;
  v_start  DATE;
BEGIN
  SELECT b.show_id, b.booking_status,
         TO_DATE(TO_CHAR(s.show_date, 'YYYY-MM-DD') || ' ' || s.show_time, 'YYYY-MM-DD HH24:MI')
    INTO v_show, v_status, v_start
    FROM booking b JOIN shows s ON s.show_id = b.show_id
   WHERE b.booking_id = p_booking_id AND b.user_id = p_user_id FOR UPDATE OF b.booking_status;
  IF v_status = 'CANCELLED' THEN RAISE_APPLICATION_ERROR(-20004, 'Already cancelled'); END IF;
  IF v_start <= SYSDATE THEN RAISE_APPLICATION_ERROR(-20005, 'Cannot cancel after show start'); END IF;
  DELETE FROM booking_seat WHERE booking_id = p_booking_id;            -- frees the seats
  UPDATE booking SET booking_status = 'CANCELLED' WHERE booking_id = p_booking_id;
  UPDATE payment SET payment_status = 'REFUNDED' WHERE booking_id = p_booking_id;
  INSERT INTO notification (user_id, message, notif_type)
  VALUES (p_user_id, 'Booking #' || p_booking_id || ' cancelled and refunded.', 'SYSTEM');
  pkg_dynamic_pricing.recompute_show(v_show);
  COMMIT;
END cancel_booking;
/

-- Scheduled job: reprice every upcoming show every 15 minutes (needs CREATE JOB privilege)
BEGIN
  BEGIN DBMS_SCHEDULER.DROP_JOB('JOB_DYNAMIC_PRICING'); EXCEPTION WHEN OTHERS THEN NULL; END;
  DBMS_SCHEDULER.CREATE_JOB(
    job_name        => 'JOB_DYNAMIC_PRICING',
    job_type        => 'PLSQL_BLOCK',
    job_action      => 'BEGIN pkg_dynamic_pricing.recompute_all; COMMIT; END;',
    repeat_interval => 'FREQ=MINUTELY;INTERVAL=15',
    enabled         => TRUE);
EXCEPTION WHEN OTHERS THEN
  DBMS_OUTPUT.PUT_LINE('Scheduler job not created (missing CREATE JOB privilege?): ' || SQLERRM);
END;
/

SHOW ERRORS
