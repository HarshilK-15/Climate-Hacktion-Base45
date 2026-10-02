# Pacific Microgrid Financial Sourcing & Cost Benchmarks

This document contains real-world financial figures, unit economics, and official source links for microgrid simulation inputs across target Pacific island nations.

---

## 1. Unit Cost Summary Table

| Parameter | Value | Unit | Target Region | Primary Source |
| :--- | :--- | :--- | :--- | :--- |
| **Landed Diesel Cost** | `$2.10` | AUD / Liter | Pacific Remote Islands | [ADB Pacific Energy Report](https://www.adb.org/documents/pacific-energy-update-2023) |
| **Installed Microgrid Solar** | `$2,800.00` | AUD / kWp | Remote Off-Grid Pacific | [IRENA Renewable Energy Costs](https://www.irena.org/Publications/2023/Jul/Renewable-Power-Generation-Costs-in-2022) |
| **Installed BESS (Battery)** | `$850.00` | AUD / kWh | Commercial LiFePO4 Utility | [NREL Annual Technology Baseline](https://atb.nrel.gov/electricity/2023/utility-scale_battery_storage) |
| **Diesel Generator O&M** | `$0.08` | AUD / kWh | Remote Microgrids | [World Bank Mini-Grid Toolkit](https://www.worldbank.org/en/topic/energy/publication/mini-grids-for-half-a-billion-people) |
| **Solar & Battery O&M** | `1.5%` | % of CAPEX / Year | Remote Pacific Sites | [NREL Microgrid Cost Analysis](https://www.nrel.gov/docs/fy23osti/84120.pdf) |

---

## 2. Regional Landed Diesel Price Breakdown

Because fuel must be shipped via marine tankers to isolated island nations, landed fuel prices include heavy maritime logistics overhead.

| Island / Nation | Estimated Landed Diesel Cost (AUD/L) | Source / Reference |
| :--- | :--- | :--- |
| **Tuvalu (Funafuti)** | `$2.35` | [Tuvalu Electricity Corporation Tariff Review](https://www.adb.org/projects/52322-001/main) |
| **Fiji (Suva)** | `$1.85` | [Fiji Competition & Consumer Commission Fuel Review](https://fccc.gov.fj/) |
| **Tonga (Nuku'alofa)** | `$2.15` | [Tonga Power Limited Energy Report](https://www.tongapower.to/) |
| **Vanuatu (Port Vila)** | `$2.20` | [Utilities Regulatory Authority Vanuatu](https://www.ura.gov.vu/) |
| **Kiribati (Tarawa)** | `$2.30` | [Public Utilities Board Kiribati ADB Report](https://www.adb.org/projects/49450-001/main) |
| **Samoa (Apia)** | `$1.95` | [Electric Power Corporation Samoa](https://www.epc.ws/) |
| **Portfolio Average** | **`$2.10`** | **Weighted Regional Baseline** |

---

## 3. Financial Assumptions for Simulation Engine (Elec B Handoff)

* **Currency:** Australian Dollars (AUD)
* **Emission Factor:** `2.68 kg CO2` saved per liter of diesel offset.
* **CAPEX Financing Term:** 20 Years @ 4% concessionary interest rate (Green Climate Fund / EU Pacific Alliance baseline).