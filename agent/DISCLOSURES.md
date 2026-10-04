# Tools used (submission field: "Tools used")

The brief makes this a scoring item, not paperwork. Every AI model, AI tool, library and dataset in
Shipless, with a version and a link. **Team: add a row for anything you used that isn't here**,
including AI chat or coding assistants used outside this repository.

## AI inside the product

| What | Version | Where it is used | Link |
| --- | --- | --- | --- |
| Claude Opus 5.5 (Anthropic), through the Claude API | model ID `claude-opus-5-5` (set with `SHIPLESS_MODEL`) | `agent/`: description to form fields, plain-language explanations, SMS wording. It only sees the engine's fact sheet; every number it writes is checked by `agent/check_numbers.py` | https://docs.claude.com |
| Server-side refusal fallback (Claude API beta `server-side-fallback-2026-07-01`) | `fallbacks: "default"` | `agent/llm.py`: if the model declines, the API retries on Anthropic's recommended fallback model | https://docs.claude.com |
| Rules and templates (no AI) | this repo | the same features when there is no API key or `SHIPLESS_AI=off`; also the fallback whenever the AI fails the checker | `agent/intake.py`, `agent/explain.py`, `agent/sms.py` |

No other AI model is called by the product. The engine (`engine/`) uses no AI: it is physics and
arithmetic, checked by 63 tests.

## AI used to build it

| What | Version | Used for |
| --- | --- | --- |
| Claude Code (Anthropic's coding agent, in the terminal) | Claude Code CLI with Claude Opus 5.5 and Claude Sonnet 5.5 | Writing and testing code in `engine/` and `agent/` (Elec B's sessions), research for the Ta'u comparison, the sensitivity slide, these documents. A person reviewed and ran everything. |
| Claude (starter code) | as noted in the root README | "Starter code written with AI assistance (Claude) during the event" |
| *Team: add yours* | | e.g. AI used for the web app, the narrative document, the video script or images |

## Libraries

| Library | Version | Licence | Used for |
| --- | --- | --- | --- |
| Python | 3.11.9 | PSF | everything server-side |
| anthropic (Python SDK) | 1.11.0 | MIT | the Claude API calls in `agent/llm.py` |
| httpx2 | 2.13.1 | BSD-3 | HTTP for the anthropic SDK |
| FastAPI | 0.142.2 | MIT | the server and the engine service |
| Starlette | 1.7.0 | BSD-3 | under FastAPI |
| Uvicorn | 0.54.0 | BSD-3 | web server |
| Pydantic | 2.13.5 | MIT | request checking |
| NumPy | 2.4.6 | BSD-3 | the simulation engine |
| Requests | 2.34.2 | Apache-2.0 | NASA weather download, Twilio SMS |
| pytest | 9.1.1 | MIT | tests |
| Chart.js | 4.4.1 | MIT | charts in the web app |
| Leaflet | 1.9.4 | BSD-2 | the map |

(`requirements.txt` also lists pandas; it isn't imported by the engine or the agent.)

## Data and services

| Dataset / service | Version / date | Used for | Link |
| --- | --- | --- | --- |
| NASA POWER hourly solar irradiance (GHI) | 2005-2024, downloaded 3 Oct 2026 | 20 years of real weather per island | https://power.larc.nasa.gov/ |
| OpenStreetMap map tiles | live | the map | https://www.openstreetmap.org/copyright |
| Cummins C55 D5e generator datasheet | D-6280-EN | the diesel fuel curve | https://yorpower.com/wp-content/uploads/2025/07/C55D5E.pdf |
| Cost sources (ADB, IRENA, NREL ATB, World Bank) | see `data/costs.md` | prices | `data/costs.md`, `agent/fact_check.csv` |
| Ta'u microgrid reports (NREL/DOI 2013, press coverage) | 2013, 2016 | the published-case comparison | `engine/published_case.py` |
| Twilio Programmable SMS | only if `TWILIO_*` is set | real SMS (otherwise a demo notification in the app) | https://www.twilio.com/docs/sms |

Every statistic used anywhere (app, video, pitch, README) has a row in `agent/fact_check.csv` with its
source link. `python -m agent.factcheck` lists any number in our documents that doesn't.
