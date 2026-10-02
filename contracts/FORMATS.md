# Data formats (change only with Mech B + the owner's approval)

## Reading batch: Pico / fake device -> POST /api/readings   (owner: Elec A)
Header: Authorization: Bearer <DEVICE_TOKEN>
{"device_id": "pico-01", "site_id": "demo-home",
 "readings": [{"ts": "2026-10-03T02:15:00Z" or null, "amps": 8.71, "watts": 2003, "seq": 118}]}

## Plan request: web -> POST /api/plan   (owner: Mech B)
{"site_id": "kadavu", "households": 120, "has_clinic": true, "has_school": true, "other_kw": 6,
 "generator_kw": 45, "diesel_litres_per_month": null, "diesel_price_per_litre": 1.7, "use_live_shape": false}

## Plan result: engine/sim.py simulate()   (owner: Elec B)
site, weather, years_of_weather, load{...},
today{diesel_litres_per_year, diesel_cost_per_year, lifetime_cost},
design{solar_kw, battery_kwh, purchase_cost, lifetime_cost, litres_saved_p50, litres_saved_p90,
       percent_diesel_cut_p50, money_saved_per_year_p50, solar_share_percent, generator_hours_per_year, payback_years},
no_fuel_ship{hours_clinic_powered, of_hours, days_clinic_powered, first_outage_hour},
week_trace{load, solar, battery, diesel, soc_pct, unserved} (168 hourly values each),
sweep, assumptions, explanation{text, source, check{passed, unknown_numbers}}

## Alert: guard/rules.py   (owner: Data Sci)
{"type": "low_load", "severity": "medium", "title": "...", "facts": {...}, "at": "...Z", "sms": "..."}
