"""What if our prices are wrong? Diesel price and solar cost each -30% / as sourced / +30% (brief 3.7).
Owner: Elec B.

    python -m engine.sensitivity             # Funafuti: the 3 x 3 table
    python -m engine.sensitivity kadavu      # any demo island
(python -m engine.precompute saves every island's table plus the slide, engine/precomputed/.)

Each of the 9 cells re-runs the whole design search with those prices and reports the winning
design and its 20-year cost. Two numbers turn the table into an argument:
  regret   if the island builds OUR design (picked at today's prices) and prices then move to this
           cell, how much more does it pay than the best design for this cell would have cost?
           Small everywhere = the decision is robust even though the "best" size moves.
  saving   20-year cost on diesel alone minus with our plan, in each price world. Positive in the
           worst corner (cheap diesel, dear solar) = solar wins even if our prices are 30% off.
Only the solar panel price moves on the solar axis; battery, upkeep and finance stay as sourced.
"""
import numpy as np

from engine import sim
from engine.dispatch import dispatch
from engine.optimise import cost_breakdown

# Brief 3.7: +-30%, wider than data/costs.md's ~10% price uncertainty so the table covers real swings
FACTORS = (0.7, 1.0, 1.3)
# ASSUMPTION: within 2% of the best = the same money (optimise.NEAR_OPTIMAL: prices are only good to
# ~10%); a design no more than 5% over the best in every cell is called robust.
NEAR = 2.0
ROBUST = 5.0


def sensitivity(inputs, factors=FACTORS):
    """inputs: engine.service.Inputs. Returns the table as plain numbers (rows = diesel, cols = solar)."""
    site, load = inputs.site, inputs.load
    p0 = dict(sim.DEFAULTS, **(inputs.overrides or {}))
    price0 = float(site.get("diesel_price_per_litre") or p0["diesel_price_per_litre"])
    solar0 = p0["solar_cost_per_kw"]
    gen_kw = sim.generator_size(site, load)
    load_kwh = float(load.sum())

    cells, base = {}, None
    for d in factors:
        for s in factors:
            site_ds = dict(site, diesel_price_per_litre=price0 * d)
            over = dict(inputs.overrides or {}, solar_cost_per_kw=solar0 * s)
            res, opt = sim.plan(site_ds, load, inputs.critical, inputs.ghi, inputs.label, over, inputs.grid)
            dz = res["design"]
            cells[(d, s)] = {"diesel_factor": d, "solar_factor": s,
                             "diesel_price_per_litre": round(price0 * d, 3),
                             "solar_cost_per_kw": round(solar0 * s),
                             "pv_kw": dz["solar_kw"], "battery_kwh": dz["battery_kwh"],
                             "capex_aud": dz["purchase_cost"], "lifetime_cost_aud": dz["lifetime_cost"],
                             "diesel_only_lifetime_cost_aud": res["today"]["lifetime_cost"],
                             "lifetime_saving_aud": dz["lifetime_saving"],
                             "percent_diesel_cut_p50": dz["percent_diesel_cut_p50"],
                             "payback_years": dz["payback_years"],
                             "_best_total": opt["winner"]["cost"]["total"],
                             "_p": dict(p0, solar_cost_per_kw=solar0 * s), "_price": price0 * d}
            if d == 1.0 and s == 1.0:
                base = (dz["solar_kw"], dz["battery_kwh"])

    # Our design (picked at sourced prices) run once on every weather year: its fuel and generator
    # use don't depend on prices, so each cell's cost of it is just the cost model with new prices.
    t, _ = dispatch(load, inputs.ghi.T, base[0], base[1], gen_kw, p0)
    fuel, gen_kwh = float(t["fuel_l"].mean()), float(t["gen_kwh"].mean())
    for c in cells.values():
        ours = cost_breakdown(base[0], base[1], fuel, gen_kwh, load_kwh, c.pop("_price"), c.pop("_p"))["total"]
        best = c.pop("_best_total")
        c["our_design_lifetime_cost_aud"] = round(ours)
        # The search is on a grid, so another cell's optimum can very slightly beat this cell's: a
        # negative regret only means "our design is as good as anything the grid found" -> 0.
        c["regret_percent"] = round(max(0.0, 100 * (ours - best) / best), 2)

    grid = [[cells[(d, s)] for s in factors] for d in factors]
    flat = list(cells.values())
    worst_regret = max(flat, key=lambda c: c["regret_percent"])
    worst_saving = min(flat, key=lambda c: c["lifetime_saving_aud"])
    pv = [c["pv_kw"] for c in flat]
    near = sum(c["regret_percent"] <= NEAR for c in flat)
    w = worst_regret
    saves = (f"the island saves at least AUD {worst_saving['lifetime_saving_aud']:,.0f} over 20 years "
             f"even with diesel {_pct(worst_saving['diesel_factor'])} and solar {_pct(worst_saving['solar_factor'])}")
    if w["regret_percent"] <= ROBUST:
        headline = (f"Prices 30% off in any direction move the best size between {min(pv):.0f} and "
                    f"{max(pv):.0f} kW of panels, but building our design costs at most "
                    f"{w['regret_percent']:.1f}% more than the best for those prices, and {saves}.")
    else:
        headline = (f"Our design is within {NEAR:.0f}% of the best in {near} of 9 price worlds. The exception: "
                    f"with diesel {_pct(w['diesel_factor'])} (AUD {w['diesel_price_per_litre']:.2f}/L) and solar "
                    f"{_pct(w['solar_factor'])} the best plan becomes a smaller fuel-saver ({w['pv_kw']:.0f} kW + "
                    f"{w['battery_kwh']:.0f} kWh) and ours would cost {w['regret_percent']:.1f}% more, so lock in "
                    f"the diesel price before ordering the battery. Solar still wins in every cell: {saves}.")
    return {"site": ", ".join(str(x) for x in (site.get("name"), site.get("country")) if x),
            "site_id": site.get("id"), "weather": inputs.label, "factors": list(factors),
            "rows": "diesel price", "columns": "solar panel cost",
            "base": {"pv_kw": base[0], "battery_kwh": base[1], "diesel_price_per_litre": price0,
                     "solar_cost_per_kw": solar0},
            "grid": grid, "max_regret_percent": worst_regret["regret_percent"], "cells_within_2_percent": near,
            "robust": worst_regret["regret_percent"] <= ROBUST,
            "min_lifetime_saving_aud": worst_saving["lifetime_saving_aud"], "headline": headline}


def _pct(f):
    return "as sourced" if f == 1.0 else f"{(f - 1) * 100:+.0f}%"


def table_md(t):
    """The 3 x 3 table as Markdown (README, slide notes)."""
    f = t["factors"]
    head = "| diesel price \\ solar cost | " + " | ".join(
        f"{_pct(s)} (AUD {t['base']['solar_cost_per_kw'] * s:,.0f}/kW)" for s in f) + " |"
    lines = [f"**{t['site']}**: winning design / 20-year cost (regret of our base design)", "",
             head, "|" + " --- |" * (len(f) + 1)]
    for d, row in zip(f, t["grid"]):
        cells = [f"{c['pv_kw']:.0f} kW + {c['battery_kwh']:.0f} kWh / AUD {c['lifetime_cost_aud'] / 1000:,.0f}k "
                 f"({c['regret_percent']:.1f}%)" for c in row]
        lines.append(f"| {_pct(d)} (AUD {t['base']['diesel_price_per_litre'] * d:.2f}/L) | " + " | ".join(cells) + " |")
    lines += ["", t["headline"]]
    return "\n".join(lines)


# Slide colours: one-hue blue ramp (steps 100..550) for regret, dataviz reference palette.
_RAMP = ("#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
         "#256abf", "#1c5cab")
_INK, _INK2, _MUTED, _SURFACE = "#0b0b0b", "#52514e", "#898781", "#fcfcfb"


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slide_svg(t):
    """The 3 x 3 table as a 1600 x 900 slide for the video (no dependencies, plain SVG)."""
    W, H = 1600, 900
    x0, y0, cw, ch, gap = 290, 268, 350, 158, 4      # grid spans x 290..1340; legend 1375..1580
    f = t["factors"]
    top = max(3.0, max(c["regret_percent"] for row in t["grid"] for c in row))   # 0..3% at least
    shade = lambda r: _RAMP[min(len(_RAMP) - 1, int(round(r / top * (len(_RAMP) - 1))))]
    years = t["weather"].split("(")[-1].split(")")[0] if "(" in t["weather"] else "every year"
    title = ("Prices 30% wrong? The best size moves. The decision doesn't." if t["robust"] else
             "Prices 30% wrong? Where our answer holds, and where it flips.")
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
         f'font-family="Inter, Segoe UI, system-ui, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="{_SURFACE}"/>',
         f'<text x="80" y="88" font-size="42" font-weight="700" fill="{_INK}">{_esc(title)}</text>',
         f'<text x="80" y="134" font-size="23" fill="{_INK2}">{_esc(t["site"])}: diesel price and solar panel cost, '
         f'each 30% lower, as sourced, 30% higher.</text>',
         f'<text x="80" y="166" font-size="23" fill="{_INK2}">Every cell re-runs the whole design search on '
         f'{_esc(years)} of real weather. Cell = the best design for those prices.</text>',
         f'<text x="{x0 + 1.5 * cw}" y="{y0 - 48}" font-size="21" font-weight="600" fill="{_INK2}" '
         f'text-anchor="middle">Solar panel cost (AUD per kW)</text>',
         f'<text x="100" y="{y0 + 1.5 * ch}" font-size="21" font-weight="600" fill="{_INK2}" text-anchor="middle" '
         f'transform="rotate(-90 100 {y0 + 1.5 * ch})">Diesel price (AUD per litre)</text>']
    for j, s in enumerate(f):
        o.append(f'<text x="{x0 + (j + .5) * cw}" y="{y0 - 14}" font-size="20" fill="{_INK2}" text-anchor="middle">'
                 f'{_pct(s)}  ({t["base"]["solar_cost_per_kw"] * s:,.0f})</text>')
    for i, (d, row) in enumerate(zip(f, t["grid"])):
        o.append(f'<text x="{x0 - 16}" y="{y0 + (i + .5) * ch + 7}" font-size="20" fill="{_INK2}" text-anchor="end">'
                 f'{_pct(d)}  ({t["base"]["diesel_price_per_litre"] * d:.2f})</text>')
        for j, c in enumerate(row):
            x, y = x0 + j * cw + gap / 2, y0 + i * ch + gap / 2
            fill = shade(c["regret_percent"])
            dark = _RAMP.index(fill) >= 6
            ink, ink2 = ("#ffffff", "#e8eefa") if dark else (_INK, _INK2)
            cx = x + (cw - gap) / 2
            o += [f'<rect x="{x}" y="{y}" width="{cw - gap}" height="{ch - gap}" rx="4" fill="{fill}"/>',
                  f'<text x="{cx}" y="{y + 44}" font-size="28" font-weight="700" fill="{ink}" text-anchor="middle">'
                  f'{c["pv_kw"]:.0f} kW + {c["battery_kwh"]:.0f} kWh</text>',
                  f'<text x="{cx}" y="{y + 78}" font-size="20" fill="{ink2}" text-anchor="middle">'
                  f'AUD {c["lifetime_cost_aud"] / 1000:,.0f}k over 20 years</text>',
                  f'<text x="{cx}" y="{y + 104}" font-size="20" fill="{ink2}" text-anchor="middle">'
                  f'{c["percent_diesel_cut_p50"]:.0f}% less diesel</text>',
                  f'<text x="{cx}" y="{y + 136}" font-size="20" font-weight="600" fill="{ink}" text-anchor="middle">'
                  f'our design here: +{c["regret_percent"]:.1f}%</text>']
    # the sourced-prices cell: our design
    o.append(f'<rect x="{x0 + cw + 1}" y="{y0 + ch + 1}" width="{cw - 2}" height="{ch - 2}" rx="5" fill="none" '
             f'stroke="{_INK}" stroke-width="3"/>')
    # legend
    lx, ly = x0 + 3 * cw + 35, y0
    for k, line in enumerate(("Shade = how much", "more our design", "costs than the", "best for those prices")):
        o.append(f'<text x="{lx}" y="{ly + 8 + 25 * k}" font-size="18" font-weight="600" fill="{_INK}">{line}</text>')
    for k, col in enumerate(_RAMP):
        o.append(f'<rect x="{lx}" y="{ly + 112 + k * 22}" width="34" height="20" rx="3" fill="{col}"/>')
    o.append(f'<text x="{lx + 46}" y="{ly + 128}" font-size="18" fill="{_INK2}">0%</text>')
    o.append(f'<text x="{lx + 46}" y="{ly + 112 + 9 * 22 + 16}" font-size="18" fill="{_INK2}">{top:.1f}%</text>')
    o.append(f'<rect x="{lx}" y="{ly + 350}" width="34" height="20" rx="4" fill="none" stroke="{_INK}" stroke-width="3"/>')
    o.append(f'<text x="{lx + 46}" y="{ly + 366}" font-size="18" fill="{_INK2}">our design:</text>')
    o.append(f'<text x="{lx + 46}" y="{ly + 388}" font-size="18" fill="{_INK2}">prices as sourced</text>')
    # takeaway
    sv, w = t["min_lifetime_saving_aud"], t["max_regret_percent"]
    yb = y0 + 3 * ch + 56
    o.append(f'<text x="80" y="{yb}" font-size="25" fill="{_INK}">Worst cell: our design costs '
             f'<tspan font-weight="700">{w:.1f}%</tspan> more than that cell\'s best. In every cell solar beats '
             f'diesel by at least <tspan font-weight="700">AUD {sv:,.0f}</tspan> over 20 years.</text>')
    o.append(f'<text x="80" y="{yb + 38}" font-size="19" fill="{_MUTED}">Shipless engine: hourly dispatch on every '
             f'weather year. Every design must never black out and must keep the clinic powered through the '
             f'darkest week on record with no diesel.</text>')
    o.append("</svg>")
    return "\n".join(o)


def _print(t):
    f = t["factors"]
    print(f"\nSENSITIVITY - {t['site']}   ({t['weather']})")
    print(f"  base design {t['base']['pv_kw']:.1f} kW + {t['base']['battery_kwh']:.1f} kWh at "
          f"{t['base']['diesel_price_per_litre']:.2f} AUD/L diesel, {t['base']['solar_cost_per_kw']:,.0f} AUD/kW solar\n")
    print("  rows: diesel price, columns: solar cost.  cell = winning design / 20-yr cost / regret of base design")
    print("  " + " " * 12 + "".join(f"{'solar ' + _pct(s):>30s}" for s in f))
    for d, row in zip(f, t["grid"]):
        a = "".join(f"{c['pv_kw']:>14.1f} kW + {c['battery_kwh']:>6.1f} kWh" for c in row)
        b = "".join(f"{'AUD ' + format(c['lifetime_cost_aud'], ','):>20s}  regret {c['regret_percent']:4.1f}%" for c in row)
        print(f"  {'diesel ' + _pct(d):>12s}{a}\n  {'':>12s}{b}")
    print(f"\n  {t['headline']}\n")


if __name__ == "__main__":
    import sys
    from engine.service import build_inputs
    _print(sensitivity(build_inputs({"site_id": sys.argv[1] if len(sys.argv) > 1 else "funafuti"})))
