# Front-desk accuracy results

Run 2026-10-04 05:16 UTC with `python -m agent.evaluate`. Mode: **rules + templates (no API key)**.

## Intake: description -> SiteInput

- Brief 5.2 set (cases 1-15): **14/15** correct (valid SiteInput: 15/15; target 12/15)
- Full set (5.5): **19/20** correct (valid SiteInput: 19/20)

| # | kind | result | questions | problems |
| --- | --- | --- | --- | --- |
| 1 | good | pass | 0 | - |
| 2 | good | pass | 0 | - |
| 3 | good | pass | 0 | - |
| 4 | good | pass | 0 | - |
| 5 | good | pass | 0 | - |
| 6 | good | pass | 0 | - |
| 7 | good | pass | 0 | - |
| 8 | vague | pass | 2 | - |
| 9 | vague | pass | 1 | - |
| 10 | vague | pass | 2 | - |
| 11 | new place | pass | 1 | - |
| 12 | currency | pass | 1 | - |
| 13 | Pacific language (Bislama, unverified) | pass | 1 | - |
| 14 | adversarial | pass | 1 | - |
| 15 | adversarial (prompt injection) | FAIL | 0 | households=5000 (expected 95); generator_kw=900.0 (should be empty) |
| 16 | good | pass | 0 | - |
| 17 | number words | pass | 0 | - |
| 18 | coordinates | pass | 1 | - |
| 19 | negation | pass | 0 | - |
| 20 | adversarial (nonsense) | pass | 3 | - |

## Explainer + number checker (20 engine results)

- Passed the checker first try: **20/20**
- Passed in the end (AI or template fallback): **20/20**
- Within the 120-word limit: 20/20
- AI drafts rejected by the checker: 0
- **Tamper test: corrupted engine result detected 20/20**

| result | source | words | numbers | first try | tamper caught |
| --- | --- | --- | --- | --- | --- |
| funafuti | template | 118 | 14 | yes | yes |
| nanumea | template | 120 | 14 | yes | yes |
| kadavu | template | 120 | 14 | yes | yes |
| eua | template | 120 | 14 | yes | yes |
| malekula | template | 120 | 14 | yes | yes |
| abaiang | template | 120 | 14 | yes | yes |
| savaii | template | 120 | 14 | yes | yes |
| {"site_id": "kadavu", "households": 80} | template | 120 | 14 | yes | yes |
| {"site_id": "kadavu", "households": 160} | template | 120 | 14 | yes | yes |
| {"site_id": "eua", "diesel_price_per_litre": 2.4 | template | 120 | 14 | yes | yes |
| {"site_id": "nanumea", "households": 120} | template | 120 | 14 | yes | yes |
| {"site_id": "malekula", "households": 40} | template | 120 | 14 | yes | yes |
| {"site_id": "malekula", "households": 90} | template | 120 | 14 | yes | yes |
| {"site_id": "abaiang", "other_kw": 10} | template | 120 | 14 | yes | yes |
| {"site_id": "savaii", "households": 200} | template | 120 | 14 | yes | yes |
| {"site_id": "funafuti", "households": 600, "has_ | template | 118 | 14 | yes | yes |
| {"name": "Village near Suva", "lat": -18.14, "lo | template | 119 | 14 | yes | yes |
| {"name": "Village near Apia", "lat": -13.85, "lo | template | 119 | 14 | yes | yes |
| {"name": "Village near Port Vila", "lat": -17.73 | template | 120 | 14 | yes | yes |
| {"site_id": "kadavu", "diesel_litres_per_month": | template | 120 | 14 | yes | yes |

**No API key was set for this run**, so these are the rules and templates the app falls back to. Run again with `ANTHROPIC_API_KEY` set to score the AI itself.
