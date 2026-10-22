-- Demonstration queries (portable ANSI SQL: run unchanged on Oracle and SQLite). Table names as in the schema.
-- Q1 Seats currently booked per upcoming-style show with occupancy %
SELECT s.show_id, m.title, s.show_date, s.show_time, sc.total_seats, COUNT(bs.seat_id) AS booked,
       ROUND(100.0 * COUNT(bs.seat_id) / sc.total_seats, 1) AS occupancy_pct
FROM shows s JOIN movie m ON m.movie_id = s.movie_id JOIN screen sc ON sc.screen_id = s.screen_id
LEFT JOIN booking_seat bs ON bs.show_id = s.show_id
GROUP BY s.show_id, m.title, s.show_date, s.show_time, sc.total_seats
ORDER BY occupancy_pct DESC, s.show_id;
-- Q2 Revenue per movie (successful payments only)
SELECT m.title, COUNT(DISTINCT b.booking_id) AS bookings, SUM(p.amount) AS revenue
FROM movie m JOIN shows s ON s.movie_id = m.movie_id JOIN booking b ON b.show_id = s.show_id
JOIN payment p ON p.booking_id = b.booking_id
WHERE p.payment_status = 'SUCCESS' GROUP BY m.title ORDER BY revenue DESC;
-- Q3 Movies with their genres and average rating (join over the M:N bridge)
SELECT m.title, g.genre_name, m.avg_rating FROM movie m
JOIN movie_genre mg ON mg.movie_id = m.movie_id JOIN genre g ON g.genre_id = mg.genre_id
WHERE m.title = 'Inception' ORDER BY g.genre_name;
-- Q4 Heavy customers: more than 250 seats booked in total (HAVING)
SELECT u.full_name, COUNT(bs.seat_id) AS seats_booked FROM users u
JOIN booking b ON b.user_id = u.user_id JOIN booking_seat bs ON bs.booking_id = b.booking_id
GROUP BY u.full_name HAVING COUNT(bs.seat_id) > 250 ORDER BY seats_booked DESC;
-- Q5 Double-booking audit: must return NO rows (a seat appearing twice for one show)
SELECT show_id, seat_id, COUNT(*) FROM booking_seat GROUP BY show_id, seat_id HAVING COUNT(*) > 1;
-- Q6 Shows whose price was moved by the engine, with the largest change first
SELECT ph.show_id, m.title, ph.old_price, ph.new_price, ph.new_price - ph.old_price AS delta, ph.change_reason
FROM price_history ph JOIN shows s ON s.show_id = ph.show_id JOIN movie m ON m.movie_id = s.movie_id
ORDER BY ABS(ph.new_price - ph.old_price) DESC, ph.price_id;
-- Q7 Movies nobody has put on a watchlist (correlated NOT EXISTS)
SELECT m.movie_id, m.title FROM movie m
WHERE NOT EXISTS (SELECT 1 FROM watchlist w WHERE w.movie_id = m.movie_id) ORDER BY m.title;
-- Q8 Users who have never written a review (set difference via NOT IN)
SELECT u.user_id, u.full_name FROM users u
WHERE u.user_id NOT IN (SELECT r.user_id FROM review r) ORDER BY u.user_id;
-- Q9 Payment-method split
SELECT payment_method, COUNT(*) AS payments, SUM(amount) AS amount FROM payment GROUP BY payment_method ORDER BY amount DESC;
-- Q10 Latest demand snapshot per show (subquery on the weak entity)
SELECT d.show_id, d.occupancy_rate, d.booking_speed, d.popularity_score, d.time_remaining, d.demand_score
FROM demand_analysis d
WHERE d.analysis_timestamp = (SELECT MAX(d2.analysis_timestamp) FROM demand_analysis d2 WHERE d2.show_id = d.show_id)
ORDER BY d.demand_score DESC;
-- Q11 Theatre -> screen -> seat inventory
SELECT t.theatre_name, t.city, COUNT(DISTINCT sc.screen_id) AS screens, COUNT(st.seat_id) AS seats
FROM theatre t JOIN screen sc ON sc.theatre_id = t.theatre_id JOIN seat st ON st.screen_id = sc.screen_id
GROUP BY t.theatre_name, t.city ORDER BY seats DESC;
-- Q12 Integrity check: bookings whose total_amount differs from their payment amount (expected: none)
SELECT b.booking_id, b.total_amount, p.amount FROM booking b JOIN payment p ON p.booking_id = b.booking_id WHERE b.total_amount <> p.amount;
