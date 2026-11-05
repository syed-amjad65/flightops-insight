-- Reusable analytical queries used by notebooks and the Streamlit app.
-- Named with :name markers for simple loader support.

-- name: otp_overview
SELECT * FROM otp_overview;

-- name: monthly_otp
SELECT * FROM otp_monthly ORDER BY month;

-- name: hourly_delay
SELECT * FROM otp_hour ORDER BY dep_hour;

-- name: carrier_performance
SELECT * FROM otp_carrier ORDER BY delay_rate ASC;

-- name: aircraft_performance
SELECT * FROM otp_aircraft ORDER BY delay_rate ASC;

-- name: top_delay_routes
SELECT * FROM otp_route WHERE flights >= 500 ORDER BY delay_rate DESC, flights DESC LIMIT 25;

-- name: busiest_routes
SELECT * FROM otp_route ORDER BY flights DESC LIMIT 25;

-- name: turnaround_effect
SELECT * FROM turnaround_effect;

-- name: delay_causes
SELECT * FROM delay_cause_mix;

-- name: daily_otp
SELECT * FROM otp_daily ORDER BY flight_date;

-- name: fleet_watchlist
SELECT * FROM fleet_watchlist ORDER BY predicted_rul_cycles ASC;

-- name: maintenance_plan
SELECT * FROM maintenance_plan ORDER BY slot_start_day, hangar;
