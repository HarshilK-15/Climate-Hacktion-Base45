# Shipless: power that doesn't wait for the boat

A clip-on sensor measures an island's diesel generator. A simulation engine replays that
demand against 20 years of NASA sunshine data to find the cheapest solar + battery system,
reports typical (P50) and bad-year (P90) diesel savings, bundles islands into one fundable
portfolio, and keeps watching after installation (Guard alerts).
Climate Hack-tion 2026, COP31 priority: Electrification (35% of final energy by 2035).

## Run it
1. Install Python 3.10+ from python.org (Windows: tick "Add Python to PATH").
2. In this folder: `pip install -r requirements.txt`
3. Start: `python -m server.app`, then open http://localhost:8000
4. No sensor yet? Second terminal: `python tools/fake_device.py http://localhost:8000/api/readings`
5. Real Pico W: copy firmware/*.py onto it with Thonny; set API_URL in firmware/config.py to the
   "Pico W API_URL" the server prints. Pico and laptop must be on the same 2.4 GHz Wi-Fi.
6. Real weather (once, needs internet): `python -m data.weather fetch`
7. AI explanations (optional): set ANTHROPIC_API_KEY before step 3.

## No hardware? Use the simulated sensor
`python tools/sim_device.py --site kadavu --backfill-days 7`
Streams a realistic island generator (same village model as the engine, real time of day,
noise, freezer cycling) in the exact Pico format. The app labels it "Simulated sensor".
While it runs, type `night`, `spike`, `offline` or `normal` + Enter to stage Guard alerts.

## Tests
`python -m engine.test_sim` and `python -m guard.test_rules`

## Owners
firmware/ Elec A | engine/ Elec B | data/, guard/ Data Sci | agent/ Mech A | server/, web/, tools/ Mech B

## Data, tools and AI disclosure
NASA POWER (weather), OpenStreetMap (map tiles), Chart.js (MIT), Leaflet (BSD-2), FastAPI, NumPy.
Starter code written with AI assistance (Claude) during the event. List every AI tool you use.
Household counts, loads, costs and diesel prices in data/sites.json are ILLUSTRATIVE until sourced.
