# Engine (owner: Elec B)

"We replay the island's real usage against 20 years of real satellite weather, test hundreds of
designs, and report the savings as a P90, the same certainty number banks ask for."

**Funafuti, the island in the video** (`engine/precomputed/funafuti.json`): 62.0 kW of solar +
166.2 kWh of battery, AUD 314,870 to buy, AUD 566,052 over 20 years vs AUD 1,170,178 staying on
diesel. P50 saves 30,547 L/yr; P90 saves 30,126 L/yr (*"In 9 years out of 10, this island saves at
least 30,000 litres of diesel"*). 0 blackout hours in 20 years. The clinic survives the darkest week on
record with no fuel.

## Quick start

    python -m engine.service                # the engine as a web service: http://localhost:8001/docs
    python -m engine.precompute             # every demo answer saved to engine/precomputed/ (~5 min)
    python -m engine.precompute --check     # are those files still up to date? (exit 1 if not)
    python -m pytest engine                 # all 63 engine tests (~3 min)

## How do you know it's right? (rehearse this answer)

> "Four ways. **Physics:** 63 automated tests check energy balance every hour, a battery that never
> leaves 20–100%, a hand-calculated diesel-only case, and that more solar never burns more diesel,
> on 224 random islands, not just ours. **Two independent engines:** a readable loop and a fast numpy
> version agree to a millionth of a litre. **Real data:** the fuel curve matches a Cummins datasheet
> to 0.3 L/h, and the sun is 20 years of NASA satellite data. **A real island:** Ta'u in American
> Samoa runs on a Tesla microgrid. Push its reported diesel through our fuel curve and you get its
> reported electricity use to within 3%. Run its as-built system through our engine and we get 99.7%
> solar; they report almost 100%. Where our numbers differ from their marketing, we say so."

Follow-ups and the honest answers:
- *"Your prices could be wrong."* → The sensitivity table (below). At Funafuti our design is within
  2.7% of the best in every ±30% price world. At Kadavu cheap diesel flips the answer, and we show that too.
- *"Is 20 years enough?"* → Drop any one year and the P90 moves < 1%; the bootstrap range is within ~4%.
- *"What's not modelled?"* → Load growth, panel ageing, diesel price changes over time, generator
  replacement. All of them are in the assumptions table, and none of them is hidden.

## Method: the 5-step hourly dispatch (brief 3.2, `dispatch.py`)

Every hour of every weather year, for every design:

1. **Solar serves the load first.** PV output = kW × sunshine (kW/m²) × 0.80 performance ratio.
2. **Spare solar charges the battery** (95% in, up to 0.5 × capacity per hour, never above 100%).
3. **If solar is short, the battery discharges** (95% out), never below 20% charge.
4. **If still short, the diesel starts**, never below 30% of its rating. When that floor makes more
   than is needed, the extra first replaces battery discharge, then charges the battery; the rest is dumped.
5. **Anything still unserved is a blackout hour.** Every design must have no more than the
   diesel-only island has today, in *every* weather year.

Fuel: `litres = 0.276 × kWh produced + 0.025 × rated kW × hours running`.

Written twice: `simulate_year()` (a plain loop, read this one) and `dispatch()` (numpy, runs
600 designs × 20 years at once). `test_engine.py` checks every total agrees.

Then (`optimise.py`): a 15 × 10 grid of panel × battery sizes scaled to the site, refined 7 × 7 around the
winner. The cheapest 20-year cost wins among designs that never black out and that keep the clinic
alive through the darkest 7 days on record with the generator off. P50 = median yearly saving; P90 =
10th percentile (`p90.py`).

## Assumptions

| What | Value | Status / source |
| --- | --- | --- |
| PV performance ratio | 0.80 | ASSUMPTION (brief 3.2). Replace with the installer's PV design report |
| Battery efficiency | 0.95 in × 0.95 out (~90% round trip) | ASSUMPTION, LiFePO4 typical. Replace with the battery datasheet |
| Battery minimum charge | 20% | ASSUMPTION (brief says 15%; 20% is kinder to battery life) |
| Battery power limit | 0.5 × capacity per hour | ASSUMPTION. Replace with the inverter rating |
| Battery charge at 1 Jan | 50% | ASSUMPTION; changes yearly totals < 0.1% |
| Generator minimum load | 30% of rating | Brief 3.2 (wet stacking). TODO Data Sci: manufacturer link |
| Fuel curve A, B | 0.276 L/kWh, 0.025 L/h per rated kW | SOURCED: least-squares fit to the [Cummins C55 D5e datasheet](https://yorpower.com/wp-content/uploads/2025/07/C55D5E.pdf) (3.8/6.4/9.4/12.0 L/h at ¼–full load); cross-checked with the [FW Power chart](https://fwpower.co.uk/wp-content/uploads/2018/12/Diesel-Generator-Fuel-Consumption-Chart-in-Litres.pdf) |
| **No generator-to-battery charging on purpose** | The generator only charges the battery with output forced by its 30% floor | Design choice: running diesel to fill batteries rarely pays on a small island |
| Generator size if unknown | 1.25 × peak load, rounded up to 10 kW | ASSUMPTION |
| Diesel price | per site (Funafuti 2.35 AUD/L), else 2.10 AUD/L | SOURCED: `data/costs.md` (ADB Pacific Energy Update; utility tariffs) |
| Solar, installed | 2,800 AUD/kW | SOURCED: `data/costs.md` (IRENA 2022) |
| Battery, installed | 850 AUD/kWh | SOURCED: `data/costs.md` (NREL ATB 2023) |
| Solar + battery upkeep | 1.5% of purchase cost a year | SOURCED: `data/costs.md` (NREL) |
| Generator upkeep | 0.08 AUD/kWh made | SOURCED: `data/costs.md` (World Bank mini-grid toolkit) |
| Finance | 20 years at 4% | SOURCED: `data/costs.md` (GCF / EU concessionary baseline) |
| CO2 | 2.68 kg per litre | SOURCED: `data/costs.md` |
| Battery life | 10 years (one replacement) | ASSUMPTION. Replace with the supplier's warranty |
| Clinic must-never-fail load | 0.4 kW (vaccine fridge + emergency light) | ASSUMPTION (`data/loads.py`) |
| Weather | NASA POWER hourly GHI, 2005–2024, at each site | SOURCED: `data/weather.py`, nearest `data/out` file as fallback |
| Not modelled | load growth, panel ageing, diesel price trend, generator replacement, resale value | Declared; generator replacement left out is conservative for solar |

## Validation

**Tests: 63 in the engine** (all pass; `python -m pytest engine`, or each file with `python -m engine.<file>`,
which prints the sentence to say to a judge for each test).

| File | Tests | What |
| --- | --- | --- |
| `test_engine.py` | 19 | fuel curve vs datasheet, dispatch rules, energy balance, loop = numpy engine, 224 random islands |
| `test_optimise.py` | 11 | cost by hand, clinic survival by hand, brute-force cross-check of the search |
| `test_weather.py` | 11 | bad weather files refused, real-weather physics, P90 stability |
| `test_service.py` | 10 | input checks, precomputed / fallback, "your design" = optimiser, sensitivity consistency, Ta'u, real HTTP |
| `test_sim.py` | 6 | the CLAUDE.md suite: physics + the SimResult shape matches `contracts/mock_simresult.json` |
| `test_p90.py` | 6 | percentile maths, rounding down, the brief's literal method = fast engine |

**Published case: Ta'u, American Samoa** (`python -m engine.published_case`, `precomputed/published_case.json`).
In Nov 2016 SolarCity/Tesla replaced Ta'u's diesel with 1.41 MW of solar + 6 MWh of batteries. No engine number
was tuned to fit.

| Check | Published | Our engine |
| --- | --- | --- |
| Electricity used, from the reported 109,500 gal/yr of diesel through our fuel curve | ~1,300,000 kWh/yr | 1,263,778 kWh/yr (−2.8%) |
| Solar share of the as-built system | "almost 100%" | 99.7% typical year, 98.9% worst of 20 |
| Diesel avoided | ~109,500 gal/yr | 109,114 gal/yr |
| Blackouts | none reported | 0 hours in 20 years |
| "3 days without sun" | 3 days | battery alone = 1.3 days of average load; darkest 3 days in 20 years, generator off: 46 of 72 h |
| "Recharges in 7 hours" | 7 h | 9 h from sunrise on a clear day, from 20% |
| What we'd build | 1.41 MW + 6 MWh | 942 kW + 2,673 kWh, 91% diesel cut; as-built costs 66% more over 20 years to remove the last 9% |

Honest limits: no public hourly data for Ta'u, so the daily shape is Data Sci's village pattern flattened
to the reported 229 kW peak, and the weather is Savai'i's 20-year NASA record (324 km away). The diesel
match mostly restates the solar share; the independent checks are the fuel curve (row 1) and the solar
share (row 2). Sources (also in the JSON): [NREL/DOI American Samoa Energy Strategies 2013](https://www.doi.gov/sites/default/files/uploads/American-Samoa-Final-Strategic-Energy-Plan.pdf) (grid ~300 kW),
[Energy-Storage.news](https://energy-storage.news/solarcity-tesla-completes-diesel-replacing-utility-scale-microgrid-in-american-samoa),
[pv magazine](https://pv-magazine-usa.com/?p=1950), [Utility Dive](https://www.utilitydive.com/news/tesla-solarcity-ready-diesel-reducing-microgrid-in-american-samoa/431053/),
[Microgrid Projects](https://microgridprojects.com/projects/tau-solarcity-tesla-microgrid-american-samoa) (diesel backup kept),
[Euan Mearns](https://euanmearns.com/solar-power-on-the-island-of-tau-a-preliminary-appraisal/) (1.3 GWh, 229 kW peak; secondary source).

**Sensitivity: diesel price ±30% × solar cost ±30%** (`python -m engine.sensitivity [island]`,
`precomputed/sensitivity.md`, slide `precomputed/sensitivity_funafuti.svg`). Each of the 9 cells re-runs the whole
search. *Regret* = how much more our design (picked at sourced prices) costs than that cell's best.

| Funafuti | solar −30% | solar as sourced | solar +30% |
| --- | --- | --- | --- |
| **diesel −30%** | 62 kW + 166 kWh / AUD 478k (0.0%) | 56 kW + 166 kWh / AUD 538k (0.5%) | 28 kW + 33 kWh / AUD 588k (2.7%) |
| **diesel as sourced** | 68 kW + 166 kWh / AUD 500k (0.6%) | **62 kW + 166 kWh / AUD 566k (0%)** | 59 kW + 166 kWh / AUD 627k (0.3%) |
| **diesel +30%** | 74 kW + 166 kWh / AUD 519k (1.9%) | 68 kW + 166 kWh / AUD 589k (0.3%) | 62 kW + 166 kWh / AUD 654k (0.0%) |

The best size moves (28–74 kW), but our design is never more than 2.7% off, and solar beats diesel by at least
AUD 263,060 over 20 years in every cell. **Across the portfolio:** Funafuti (2.7%) and Abaiang (4.5%) are robust, Nanumea nearly so (6.0%).
At Kadavu, 'Eua, Savai'i and Malekula (cheap diesel, AUD 1.6–2.0/L) a 30% fall in diesel flips the best plan to a
small "fuel-saver" with a little battery, and our design would cost up to 19% more. **The takeaway: lock in the
diesel price before ordering the battery.** Solar still wins in all 63 cells.

## Web service (brief 3.7, `service.py`)

    python -m engine.service           # port 8001 (or $PORT)
    uvicorn engine.service:app --port 8001

**Port 8001**, because Mech B's `server.app` already uses 8000. To serve the engine from Mech B's server
instead (one process, one port), add one line there: `app.include_router(engine.service.router, prefix="/engine")`.

| Endpoint | Returns |
| --- | --- |
| `POST /simulate` | SiteInput → the full plan; the brief's flat SimResult is under `"simresult"`. `?view=simresult` returns only that |
| `GET /simulate/{island}` | the same for a demo island (open it in a browser) |
| `GET /islands` | every island's SimResult + portfolio totals (the map), instant |
| `GET /sensitivity/{island}` | the 3 × 3 table; `/sensitivity/{island}/slide.svg` is the slide |
| `GET /published-case` | the Ta'u comparison |
| `GET /health` | engine version, which precomputed files are up to date |

**SiteInput** (both contract spellings work: `site_id`/`island_id`, `diesel_price_per_litre`/`diesel_cost_per_liter`,
`generator_kw`/`generator_capacity_kw`):

    {"site_id": "kadavu"}                                          a demo island as sourced
    {"site_id": "kadavu", "households": 140, "diesel_price_per_litre": 1.85}     with changes
    {"name": "My village", "lat": -17.7, "lon": 168.3, "households": 60, "has_clinic": true}   a new place
    {"site_id": "eua", "load_kw": [24 or 168 or 8760 hourly kW]}   measured demand
    {"site_id": "eua", "overrides": {"solar_cost_per_kw": 2400}}   any setting in sim.DEFAULTS
    {"site_id": "kadavu", "pv_kw": 80, "battery_kwh": 150}         also check THIS design ("your_design")

The answer is the server's plan (`contracts/FORMATS.md`: `site, weather, load, today, design, no_fuel_ship,
week_trace, sweep, assumptions`) plus `simresult`, `bank` (the P90 sentence + evidence + every year's saving),
`options` (cheapest to buy / cheapest overall / most diesel-free / ignoring the clinic), `price_of_resilience_aud`,
`whole_island_no_fuel`, `cost_breakdown_aud`, `cost_per_kwh_aud`, `search`, `your_design` (when asked) and `meta`.
Bad input → HTTP 422 with a sentence saying what to fix. `your_design.verdict` is plain English. Real answers for Kadavu:
40 kW + 20 kWh → *"Not safe: the clinic loses power after 155 of 168 hours when the fuel ship is late."*;
140 kW + 300 kWh → *"Works, but costs AUD 63,452 more over 20 years than our design (6.6%)."*

**The demo never depends on live computation.** `meta.source` says where each answer came from:

| `meta.source` | When |
| --- | --- |
| `precomputed` | inputs match a file in `engine/precomputed/` exactly (all inputs + engine code version): instant |
| `cache` | asked before in this run: instant |
| `live` | computed now, 2–9 s |
| `precomputed-fallback` | live took > `ENGINE_TIMEOUT_S` (default 20) or crashed: the island's default plan + a warning in `meta.warnings`; the live run continues into the cache |

At start-up the service also starts background runs for any island whose file is stale, so the first click is fast.

**Deploying** (decide with Mech B): *ngrok* is simplest (`python -m engine.service`, then `ngrok http 8001`). It is
the same laptop, so the same speed and the precomputed files are already there. *Render / Railway*: start command
`uvicorn engine.service:app --host 0.0.0.0 --port $PORT`, with `engine/precomputed/` and `data/cache/` committed. Free
tiers sleep and have slow CPUs, so precomputed answers carry the demo. CORS is open (`ENGINE_CORS_ORIGINS` to narrow it).

Environment: `ENGINE_TIMEOUT_S` (20), `ENGINE_WARMUP` (1; 0 = off), `ENGINE_PRECOMPUTED_DIR`, `ENGINE_CORS_ORIGINS` (*), `PORT`.

## Precompute (`precompute.py`)

`python -m engine.precompute` writes `engine/precomputed/`: `{island}.json` (exactly what `POST /simulate` returns),
`index.json` (map + totals), `sensitivity.json` / `.md` / `_{island}.svg`, `published_case.json`. Every file carries the
engine version (a hash of `dispatch.py`, `optimise.py`, `sim.py`, `p90.py`, `service.py`), so a stale file is never
served as current.

## Sunday: freeze

1. Only bug fixes. After any engine edit: `python -m pytest engine guard`, then `python -m engine.precompute`,
   then `python -m engine.precompute --check` must say *all up to date*. Commit `engine/precomputed/`.
2. On the demo machine: `python -m engine.service`, open `/health` (all `fresh`), click every island once.
3. Rehearse *"How do you know it's right?"* (above), out loud, under 40 seconds.

## Background detail

**Fuel curve.** Cummins C55 D5e (prime 40 kW) fuel use 3.8/6.4/9.4/12.0 L/h at ¼–full load; the least-squares fit gives
A = 0.276, B = 0.025 (standby column: 0.278 / 0.023). The brief suggests B ≈ 0.03–0.08; real datasheets for 25–45 kW
sets give 0.02–0.025, so "idle generator" waste is real but smaller than that.

**Honest finding.** A bigger battery is not strictly monotone: with the 30% floor and the "engine on" fuel cost it can
shift a generator start. Fuzzing found at most +0.09% diesel, which is why the search tries battery sizes rather than
assuming bigger is better.

**Real weather.** `weather_files.py` loads Data Sci's NASA files, refusing gaps, −999 codes, impossible sunshine and
part-years. The app prefers the 20-year file at each site's own coordinates (`data/cache/`), then the nearest
`data/out` file within 500 km, then synthetic sun (labelled, and the service adds a warning). Real sun is ~19%
stronger than the synthetic pattern.

**P50 / P90.** P90 = `numpy.percentile(savings, 10)`. With 20 years it sits between the 2nd- and 3rd-worst year. The
sentence rounds **down** to 2 significant figures, so the promise is never bigger than the evidence. 10-year P90 of the
average (bank view): Funafuti 30,516 L. Covers weather only.

**Search upgrades on the brief.** The grid is scaled to each site, not the brief's fixed 100–1500 kW. Every design runs on
all 20 years at once, not year one only. Clinic survival is a rule every design must pass. The near-optimal band and the
"same money, your choice" options are reported because the cost curve is flat: at Kadavu every design from 59 to 125 kW
is within 2%. Battery replacement and generator upkeep are counted. Late fuel ship: the darkest 7 days in 20 years, the
battery starting from the charge normal running left it, generator off, clinic only.

**Timing.** 600 designs × 20 years = 12,000 island-years (105 million hourly decisions) in ~4 s on the dev laptop
(`python -m engine.optimise`). A plan for the app (15 × 10 grid + refine) takes 2–5 s.

## Files

| File | Job |
| --- | --- |
| `dispatch.py` | fuel curve, technical settings, `simulate_year()` (readable) and `dispatch()` (fast) |
| `optimise.py` | cost model, design search on all weather years, late-fuel-ship test, CLI |
| `sim.py` | `simulate()` / `plan()`: the plan Mech B's server uses today, with the flat SimResult |
| `p90.py` | P50 / P90, the bank sentence, 10-year P90, confidence ranges |
| `service.py` | the web service: input checks, precomputed / cache / live / fallback, "your design" check |
| `precompute.py` | builds `engine/precomputed/`; `--check` for the freeze |
| `sensitivity.py` | diesel × solar ±30% table, regret, slide SVG |
| `published_case.py` | our engine vs the Ta'u microgrid |
| `weather_files.py` | load and check NASA weather files; nearest file for a site |
| `test_*.py` | 63 tests (table above) |
