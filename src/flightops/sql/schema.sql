-- FlightOps Insight DuckDB schema and BI-friendly summary tables.
-- The flights view is created by build_database.py with an absolute parquet path.

CREATE OR REPLACE VIEW dim_date AS
SELECT DISTINCT
    flight_date,
    month,
    day_of_week,
    day_of_year,
    is_weekend,
    is_holiday
FROM flights;

CREATE OR REPLACE VIEW dim_carrier AS
SELECT DISTINCT carrier FROM flights;

CREATE OR REPLACE VIEW dim_airport AS
SELECT origin AS airport, origin_state AS state FROM flights
UNION
SELECT dest AS airport, dest_state AS state FROM flights;

CREATE OR REPLACE VIEW dim_aircraft AS
SELECT DISTINCT
    tail_number,
    aircraft_type,
    aircraft_family,
    seats,
    min_turn_min
FROM flights
WHERE tail_number IS NOT NULL;

CREATE OR REPLACE VIEW dim_route AS
SELECT DISTINCT origin, dest, origin || '_' || dest AS route, distance_mi
FROM flights;

CREATE OR REPLACE TABLE otp_overview AS
SELECT
    COUNT(*) AS flights,
    SUM(CASE WHEN dep_del15 = 1 THEN 1 ELSE 0 END) AS delayed_flights,
    ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS delay_rate,
    ROUND(AVG(dep_delay_min), 2) AS avg_dep_delay_min,
    ROUND(AVG(cancelled), 4) AS cancellation_rate,
    ROUND(AVG(CASE WHEN dep_del15 = 0 THEN 1.0 ELSE 0.0 END), 4) AS otp_rate
FROM flights;

CREATE OR REPLACE TABLE otp_daily AS
SELECT
    flight_date,
    COUNT(*) AS flights,
    SUM(CASE WHEN dep_del15 = 1 THEN 1 ELSE 0 END) AS delayed_flights,
    ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS delay_rate,
    ROUND(1 - AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS otp_rate,
    ROUND(AVG(dep_delay_min), 2) AS avg_dep_delay_min,
    ROUND(AVG(cancelled), 4) AS cancellation_rate
FROM flights
GROUP BY 1
ORDER BY 1;

CREATE OR REPLACE TABLE otp_monthly AS
SELECT
    month,
    COUNT(*) AS flights,
    ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS delay_rate,
    ROUND(1 - AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS otp_rate,
    ROUND(AVG(dep_delay_min), 2) AS avg_dep_delay_min,
    ROUND(AVG(cancelled), 4) AS cancellation_rate
FROM flights
GROUP BY 1
ORDER BY 1;

CREATE OR REPLACE TABLE otp_hour AS
SELECT
    dep_hour,
    COUNT(*) AS flights,
    ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS delay_rate,
    ROUND(AVG(dep_delay_min), 2) AS avg_dep_delay_min,
    ROUND(AVG(sched_turn_min), 1) AS avg_scheduled_turn_min,
    ROUND(AVG(prev_arr_delay_obs), 1) AS avg_inbound_delay_min
FROM flights
GROUP BY 1
ORDER BY 1;

CREATE OR REPLACE TABLE otp_carrier AS
SELECT
    carrier,
    COUNT(*) AS flights,
    ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS delay_rate,
    ROUND(1 - AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS otp_rate,
    ROUND(AVG(dep_delay_min), 2) AS avg_dep_delay_min,
    ROUND(AVG(cancelled), 4) AS cancellation_rate,
    ROUND(AVG(distance_mi), 0) AS avg_distance_mi
FROM flights
GROUP BY 1
ORDER BY flights DESC;

CREATE OR REPLACE TABLE otp_aircraft AS
SELECT
    aircraft_type,
    aircraft_family,
    COUNT(*) AS flights,
    ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS delay_rate,
    ROUND(AVG(dep_delay_min), 2) AS avg_dep_delay_min,
    ROUND(AVG(sched_turn_min), 1) AS avg_scheduled_turn_min,
    ROUND(AVG(min_turn_min), 1) AS min_turn_min
FROM flights
GROUP BY 1, 2
ORDER BY flights DESC;

CREATE OR REPLACE TABLE otp_route AS
SELECT
    origin,
    dest,
    origin || '_' || dest AS route,
    COUNT(*) AS flights,
    ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS delay_rate,
    ROUND(AVG(dep_delay_min), 2) AS avg_dep_delay_min,
    ROUND(AVG(distance_mi), 0) AS distance_mi,
    ROUND(AVG(sched_turn_min), 1) AS avg_scheduled_turn_min
FROM flights
GROUP BY 1, 2
ORDER BY flights DESC;

CREATE OR REPLACE TABLE turnaround_effect AS
SELECT
    CASE
        WHEN sched_turn_min IS NULL THEN 'No inbound link'
        WHEN sched_turn_min < 40 THEN '< 40 min'
        WHEN sched_turn_min < 60 THEN '40-59 min'
        WHEN sched_turn_min < 90 THEN '60-89 min'
        WHEN sched_turn_min < 150 THEN '90-149 min'
        ELSE '150+ min'
    END AS scheduled_turn_bucket,
    COUNT(*) AS flights,
    ROUND(AVG(CASE WHEN dep_del15 IS NOT NULL THEN dep_del15 END), 4) AS delay_rate,
    ROUND(AVG(dep_delay_min), 2) AS avg_dep_delay_min,
    ROUND(AVG(prev_arr_delay_obs), 1) AS avg_inbound_delay_min
FROM flights
GROUP BY 1
ORDER BY 1;

CREATE OR REPLACE TABLE delay_cause_mix AS
SELECT 'Carrier' AS cause, ROUND(SUM(carrier_delay_min), 0) AS delay_minutes FROM flights
UNION ALL SELECT 'Weather', ROUND(SUM(weather_delay_min), 0) FROM flights
UNION ALL SELECT 'NAS / airspace', ROUND(SUM(nas_delay_min), 0) FROM flights
UNION ALL SELECT 'Security', ROUND(SUM(security_delay_min), 0) FROM flights
UNION ALL SELECT 'Late aircraft', ROUND(SUM(late_aircraft_delay_min), 0) FROM flights
ORDER BY delay_minutes DESC;

CREATE OR REPLACE VIEW fleet_watchlist AS
SELECT * FROM read_csv_auto('reports/metrics/rul_watchlist.csv', header=true);

CREATE OR REPLACE VIEW maintenance_plan AS
SELECT * FROM read_csv_auto('reports/optimization/maintenance_plan_optimized.csv', header=true);
