# OTP root-cause summary

## What the data says

- Across 7,079,059 scheduled flights in 2024, 20.7% departed more than 15 minutes late; the cancellation rate was 1.4%.
- The gradient-boosted model's SHAP attribution ranks turnaround & inbound aircraft as the largest driver group (56% of total absolute SHAP), followed by airport & network.
- Delay risk climbs through the day: the 15:00-20:00 block averages 28.6% versus 10.7% in the 06:00-09:00 window (2.7x).
- The late-aircraft association is visible in the turnaround data: flights with an inbound aircraft already 60+ minutes late depart late 16.6% of the time, versus 9.3% when the inbound is early. This is associational and is partly confounded by scheduled turnaround buffers.
- Scheduled turnarounds below 40 minutes show a 27.8% delay rate versus 15.5% for 90-149 minute turnarounds, consistent with little recovery buffer.
- Seasonality matters: month 7 is the worst month at 29.5% versus month 10 at 13.6%.
- Carrier spread is material: F9 delays 27.7% of departures versus YX at 11.7%.
- The highest-delay high-volume route is BDL_SJU at 44.5% across 1,000 flights.
- B788 aircraft show the highest delay rate among types with >=500 flights (26.2%). Aircraft type is an enrichment proxy; it should be validated against the operator's fleet registry.

## Model drivers (SHAP)

- prev_dep_delay_min: mean |SHAP| 0.7507 (Turnaround & inbound aircraft)
- sched_turn_min: mean |SHAP| 0.3142 (Turnaround & inbound aircraft)
- prev_arr_delay_obs: mean |SHAP| 0.2909 (Turnaround & inbound aircraft)
- hour_sin: mean |SHAP| 0.1638 (Time of day)
- doy_cos: mean |SHAP| 0.1211 (Season & calendar)
- carrier: mean |SHAP| 0.1205 (Carrier)
- route: mean |SHAP| 0.1128 (Airport & network)
- origin: mean |SHAP| 0.0956 (Airport & network)
- dep_hour: mean |SHAP| 0.0883 (Time of day)
- day_of_week: mean |SHAP| 0.0759 (Season & calendar)
- dest: mean |SHAP| 0.0642 (Airport & network)
- day_of_year: mean |SHAP| 0.0609 (Season & calendar)

## Caveats

- These are associations in observational data, not causal estimates.
- Aircraft type is a deterministic synthetic enrichment because the BTS extract has no equipment field.
- Weather is represented only through season/calendar proxies; no station-level weather feed is joined.
- Delay-cause fields (carrier/weather/NAS/late aircraft) are post-flight attributions and are excluded from model features.