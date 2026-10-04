"""Portfolio Aggregation Engine for Pacific Island Microgrids.

Scales baseline household telemetry across target islands, computes aggregate
metrics, and exports contracts/mock_portfolio.json for Mech B's web map UI.
"""

import json
import os

DIESEL_PRICE_PER_LITER = 1.85  # USD per liter in regional remote islands
KG_CO2_PER_LITER_DIESEL = 2.68  # Standard EPA emissions factor for diesel fuel

ISLANDS = [
    {
        "id": "island_1",
        "name": "Vatoa",
        "nation": "Fiji",
        "lat": -19.82,
        "lng": -178.22,
        "households": 85,
        "capex_usd": 45000,
        "annual_diesel_saved_liters": 28900.0,
    },
    {
        "id": "island_2",
        "name": "Funafuti Outer",
        "nation": "Tuvalu",
        "lat": -8.52,
        "lng": 179.19,
        "households": 120,
        "capex_usd": 62000,
        "annual_diesel_saved_liters": 40800.0,
    },
    {
        "id": "island_3",
        "name": "Eua",
        "nation": "Tonga",
        "lat": -21.37,
        "lng": -174.92,
        "households": 310,
        "capex_usd": 140000,
        "annual_diesel_saved_liters": 105400.0,
    },
    {
        "id": "island_4",
        "name": "Aitutaki",
        "nation": "Cook Islands",
        "lat": -18.85,
        "lng": -159.78,
        "households": 450,
        "capex_usd": 195000,
        "annual_diesel_saved_liters": 153000.0,
    },
    {
        "id": "island_5",
        "name": "Kirimati Outer",
        "nation": "Kiribati",
        "lat": 1.87,
        "lng": -157.36,
        "households": 220,
        "capex_usd": 98000,
        "annual_diesel_saved_liters": 74800.0,
    },
    {
        "id": "island_6",
        "name": "Mitiaro",
        "nation": "Cook Islands",
        "lat": -19.85,
        "lng": -157.70,
        "households": 65,
        "capex_usd": 38000,
        "annual_diesel_saved_liters": 22100.0,
    },
]


def export_portfolio_contract():
    total_households = sum(i["households"] for i in ISLANDS)
    total_capex = sum(i["capex_usd"] for i in ISLANDS)
    total_diesel = sum(i["annual_diesel_saved_liters"] for i in ISLANDS)
    total_co2_kg = total_diesel * KG_CO2_PER_LITER_DIESEL
    total_money_usd = total_diesel * DIESEL_PRICE_PER_LITER

    # Enrich island objects with CO2 and Money savings for map tooltips
    processed_islands = []
    for island in ISLANDS:
        item = island.copy()
        item["annual_co2_saved_kg"] = round(item["annual_diesel_saved_liters"] * KG_CO2_PER_LITER_DIESEL, 2)
        item["annual_money_saved_usd"] = round(item["annual_diesel_saved_liters"] * DIESEL_PRICE_PER_LITER, 2)
        processed_islands.append(item)

    contract_payload = {
        "schema_version": "1.0",
        "summary": {
            "total_islands": len(ISLANDS),
            "total_households": total_households,
            "total_capex_usd": total_capex,
            "total_annual_diesel_saved_liters": total_diesel,
            "total_annual_co2_saved_kg": round(total_co2_kg, 2),
            "total_annual_co2_saved_tonnes": round(total_co2_kg / 1000.0, 2),
            "total_annual_money_saved_usd": round(total_money_usd, 2),
        },
        "islands": processed_islands,
    }

    # Ensure /contracts directory exists
    os.makedirs("contracts", exist_ok=True)
    out_path = os.path.join("contracts", "mock_portfolio.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(contract_payload, f, indent=2)

    print("=" * 65)
    print(f" ✅ Portfolio contract exported successfully to: {out_path}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    export_portfolio_contract()