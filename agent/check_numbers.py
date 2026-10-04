"""The number checker: the AI cannot show a number the engine didn't produce. Owner: Mech A.

    python -m agent.check_numbers --demo                  # the on-camera demo (agent/demo.py)
    python -m agent.check_numbers TEXT.txt SOURCE.json    # check any text against any engine JSON

check(text, source) finds every number in `text` and looks for it in `source` (the engine's JSON).
Any number it can't find is UNSOURCED and the text is rejected: the app regenerates it or shows
the engine's own template instead. The AI never decides what is true; this file does.

The rules (each a deliberate, documented choice):
  1. A number is anything that states a quantity:
       digits             30,126   4.3   $2.40   92%   -30%   12:00
       digits + scale     566k   1.2 million   30 thousand   AUD 2.9M
       number words       seven days   twenty years   thirty-two   a thousand
       ratio words        half   double   twice   triple        (a comparison is a number too)
     so an AI cannot slip a figure past the checker by spelling it out.
  2. It may match any number in the source JSON (booleans don't count). Big hourly arrays
     (week_trace, sweep) and run metadata (meta) are skipped: with ~1,000 values in them almost
     anything would match by accident.
  3. Honest rounding is allowed, dishonest rounding is not. A written number passes if it equals a
     source number to 2 decimals, or that number rounded to the decimals shown / to a whole number,
     or rounded to at least 2 significant figures:
       30,000 for 30,126  -> pass (2 significant figures)      566k for 566,052 -> pass (3)
       300,000 for 314,870 -> FAIL (1 significant figure is a different claim)
     Calendar years are labels, not quantities: they must match exactly ("2,000 islands" can't
     pass by "rounding" the weather's first year, 2005).
  4. A percentage may only match a source that IS a percentage or fraction (its field name says
     percent / share / cut / rate / fraction ...): "20% less diesel" can't borrow the 20 from
     "20 years". It may match a fraction: 80% for 0.8.
  5. Signs are ignored: "30% less" and "-30%" are the same claim.
  6. Identifiers are names, not quantities, and are skipped: P50, P90, CO2, COP31.
  7. One fixed phrase is whitelisted on purpose: "9 (years) out of 10" / "nine in ten". It is the
     definition of P90 that our own template uses, not a fact about any island.
What it cannot do: prove a correct number is used in the right sentence ("62 kW of battery").
That is why every number in the report says which engine field it matched, and why the explainer
is given a small fact sheet with plain names instead of the whole engine output.
"""
import json
import math
import re
import sys
import unicodedata
from dataclasses import dataclass, field

SKIP_KEYS = {"week_trace", "sweep", "meta", "grid"}
# A field holds a percentage or fraction if its name contains one of these (rule 4)
PERCENT_HINTS = ("percent", "pct", "share", "cut", "regret", "fraction", "rate", "derate", "soc", "eff",
                 "om_", "ratio", "min_load", "%")
IDENTIFIERS = {"P50", "P90", "CO2", "COP31"}
FIXED_PHRASES = [
    (r"\b(?:9|nine)\s+(?:years?\s+)?(?:out\s+of|in)\s+(?:10|ten)\b",
     "definition of P90 ('beaten in 9 years out of 10'), our own template wording"),
]
MIN_SIGNIFICANT_FIGURES = 2

_SCALE = {"k": 1e3, "K": 1e3, "thousand": 1e3, "M": 1e6, "million": 1e6, "mn": 1e6, "bn": 1e9, "billion": 1e9}
_UNITS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_BIG = {"hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}
_RATIO = {"half": 0.5, "halve": 0.5, "halves": 0.5, "halved": 0.5, "twice": 2, "double": 2,
          "doubles": 2, "doubled": 2, "triple": 3, "tripled": 3, "treble": 3, "quadruple": 4}

_DIGITS = re.compile(
    r"(?P<pre>[A-Za-z_]*)"                                  # letters glued in front: P90, CO2
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<scale>\s?(?:thousand|million|billion|mn|bn)\b|(?:k|K|M)\b)?"
    r"(?P<pct>\s?%|\s(?:percent|per\s?cent)\b)?")
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_WORD = re.compile(r"[A-Za-z]+")


@dataclass
class Number:
    text: str            # as written, e.g. "566k"
    value: float         # what it means, e.g. 566000
    start: int
    end: int
    kind: str            # number | percent | word | ratio | time
    decimals: int = 0    # decimals shown in the text (rounding rule 3)


@dataclass
class Finding:
    number: Number
    status: str          # sourced | unsourced | exempt
    source: str = ""     # engine field it matched, e.g. "design.solar_kw"
    source_value: object = None
    rule: str = ""       # how it matched, e.g. "rounded to 2 significant figures"
    also: list = field(default_factory=list)   # other engine fields with exactly the same value


@dataclass
class Report:
    text: str
    findings: list = field(default_factory=list)

    @property
    def passed(self):
        return not self.unsourced

    @property
    def unsourced(self):
        return [f for f in self.findings if f.status == "unsourced"]

    @property
    def unknown_numbers(self):
        """The numbers that failed, as written (what the web app shows)."""
        return [f.number.text for f in self.unsourced]

    def to_dict(self):
        return {"passed": self.passed, "unknown_numbers": self.unknown_numbers,
                "numbers_checked": len(self.findings),
                "numbers": [{"text": f.number.text, "value": f.number.value, "start": f.number.start,
                             "end": f.number.end, "status": f.status, "source": f.source, "also": f.also,
                             "source_value": f.source_value, "rule": f.rule} for f in self.findings]}

    def summary(self):
        n = len(self.findings)
        if self.passed:
            return f"PASS: all {n} numbers traced to the engine's output"
        return f"FAIL: {len(self.unsourced)} of {n} numbers are not in the engine's output: " + ", ".join(
            self.unknown_numbers)


# ---------------------------------------------------------------------------------------------
# Finding numbers in text
# ---------------------------------------------------------------------------------------------

def _plain(s):
    """Typographic marks -> plain ASCII so the patterns see what a reader sees."""
    s = unicodedata.normalize("NFKC", s)
    return (s.replace(" ", " ").replace(" ", " ").replace(" ", " ")
             .replace("–", "-").replace("—", "-"))


def _word_numbers(text, taken):
    """Spelled-out numbers ('thirty-two thousand') and ratio words ('half'). taken: spans to skip."""
    out = []
    words = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text)]
    i = 0
    while i < len(words):
        w, s, e = words[i]
        low = w.lower()
        if any(a <= s < b for a, b in taken):
            i += 1
            continue
        if low in _RATIO and not text[e:e + 1] == "-":
            out.append(Number(w, _RATIO[low], s, e, "ratio"))
            i += 1
            continue
        # a run of number words ("thirty-two thousand"), with "and" inside ("a hundred and twenty")
        is_num = lambda wd: wd in _UNITS or wd in _TENS or wd in _BIG or wd == "one"
        parts, j = [], i
        while j < len(words):
            wj = words[j][0].lower()
            if is_num(wj):
                parts.append(wj)
                j += 1
            elif wj == "and" and parts and j + 1 < len(words) and is_num(words[j + 1][0].lower()):
                j += 1
            elif wj == "a" and not parts and j + 1 < len(words) and words[j + 1][0].lower() in _BIG:
                parts.append("one")          # "a thousand"
                j += 1
            else:
                break
        # "one" alone is too often a pronoun ("the one that...") to count; "one hundred" counts
        if parts and not (parts == ["one"]):
            total, current = 0, 0
            for p in parts:
                if p in _UNITS:
                    current += _UNITS[p]
                elif p == "one":
                    current += 1
                elif p in _TENS:
                    current += _TENS[p]
                elif p == "hundred":
                    current = max(current, 1) * 100
                else:
                    total += max(current, 1) * _BIG[p]
                    current = 0
            s0, e0 = words[i][1], words[j - 1][2]
            out.append(Number(text[s0:e0], float(total + current), s0, e0, "word"))
            i = j
        else:
            i += 1
    return out


def extract_numbers(text):
    """Every quantity stated in text -> list of Number (identifiers and whitelisted phrases excluded)."""
    text = _plain(text)
    numbers, taken = [], []
    for pat, _why in FIXED_PHRASES:
        taken += [m.span() for m in re.finditer(pat, text, re.I)]
    for m in _TIME.finditer(text):
        if not any(a <= m.start() < b for a, b in taken):
            numbers.append(Number(m.group(0), float(m.group(1)), m.start(), m.end(), "time"))
            taken.append(m.span())
    for m in _DIGITS.finditer(text):
        s = m.start("num")
        if any(a <= s < b for a, b in taken):
            continue
        if m.group("pre") and (m.group("pre") + m.group("num")).upper() in IDENTIFIERS:
            continue                           # P90, CO2: a name, not a quantity
        num = m.group("num")
        value = float(num.replace(",", ""))
        decimals = len(num.split(".")[1]) if "." in num else 0
        scale = (m.group("scale") or "").strip()
        if scale:
            value *= _SCALE[scale]
            decimals = 0
        kind = "percent" if m.group("pct") else "number"
        end = m.end() if (scale or m.group("pct")) else m.end("num")
        numbers.append(Number(text[s:end].strip(), value, s, end, kind, decimals))
        taken.append((s, end))
    numbers += _word_numbers(text, taken)
    return sorted(numbers, key=lambda n: n.start)


# ---------------------------------------------------------------------------------------------
# Matching against the engine's JSON
# ---------------------------------------------------------------------------------------------

def source_numbers(obj, path="", skip=SKIP_KEYS):
    """Every number in a JSON value -> list of (path, value). Booleans and skipped keys excluded."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            return []
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) not in skip:
                out += source_numbers(v, f"{path}.{k}" if path else str(k), skip)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out += source_numbers(v, f"{path}[{i}]", skip)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool) and math.isfinite(obj):
        out.append((path, obj))
    return out


def is_percent_field(path):
    """Does this engine field hold a percentage or fraction? (rule 4)"""
    return any(h in str(path).lower() for h in PERCENT_HINTS)


def is_year_field(path, value):
    """A calendar year (2019) is a label, not a quantity: it must match exactly, never 'rounded' (rule 3)."""
    return "year" in str(path).lower() and 1900 <= abs(float(value)) <= 2100


def _round_half_up(x, decimals=0):
    q = 10 ** decimals
    return math.floor(abs(x) * q + 0.5) / q


def _round_sig(x, sig):
    if x == 0:
        return 0.0
    return _round_half_up(x, sig - 1 - int(math.floor(math.log10(abs(x)))))


def _same(a, b):
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def _match(n, a):
    """How written number n can honestly come from source value a -> rule name, or None."""
    x, a = abs(n.value), abs(float(a))
    candidates = [(a, "")]
    if n.kind == "percent" and 0 < a <= 1:
        candidates.append((a * 100, "fraction shown as a percent, "))
    for v, how in candidates:
        if abs(x - v) <= 0.005:
            return how + "exact"
        if n.decimals and _same(_round_half_up(v, n.decimals), x):
            return how + f"rounded to {n.decimals} decimal place{'s' if n.decimals > 1 else ''}"
        if not n.decimals and _same(_round_half_up(v), x) and abs(v) >= 1:
            return how + "rounded to a whole number"
        for sig in range(MIN_SIGNIFICANT_FIGURES, 8):
            if _same(_round_sig(v, sig), x):
                return how + f"rounded to {sig} significant figures"
    return None


def check(text, *sources, skip=SKIP_KEYS):
    """Check every number in text against the source JSON(s) -> Report. report.passed is the verdict."""
    pool = []
    for i, src in enumerate(sources):
        pool += source_numbers(src, "" if len(sources) == 1 else f"source{i + 1}", skip)
    report = Report(text)
    for n in extract_numbers(text):
        hit = None
        if n.kind == "ratio":       # "half", "double": the engine reports percentages, never ratios
            report.findings.append(Finding(n, "unsourced", rule="comparison the engine did not make"))
            continue
        for path, a in pool:
            if n.kind == "percent" and not is_percent_field(path):
                continue
            rule = _match(n, a)
            if not rule or (rule != "exact" and is_year_field(path, a)):
                continue
            if hit is None or (rule == "exact" and hit.rule != "exact"):
                hit = Finding(n, "sourced", path, a, rule)
            elif rule == "exact" and hit.rule == "exact":
                hit.also.append(path)      # e.g. 20 = weather years AND project years: show both
        report.findings.append(hit or Finding(n, "unsourced"))
    return report


def check_list(ai_text, simresult_json):
    """The brief's interface: the list of unsourced numbers; an empty list means PASS."""
    return [f.number.value for f in check(ai_text, simresult_json).unsourced]


# ---------------------------------------------------------------------------------------------
# Showing the result (terminal for the video, HTML for the web app)
# ---------------------------------------------------------------------------------------------

def highlight_html(report):
    """The text as HTML with every number marked: green = traced (hover shows the engine field),
    red = not in the engine's output. Safe to drop into a page (text is escaped)."""
    import html
    text, out, pos = _plain(report.text), [], 0
    for f in report.findings:
        n = f.number
        out.append(html.escape(text[pos:n.start]))
        if f.status == "sourced":
            tip = f"{' / '.join([f.source] + f.also)} = {f.source_value} ({f.rule})"
            out.append(f'<mark class="num ok" title="{html.escape(tip)}">{html.escape(text[n.start:n.end])}</mark>')
        else:
            out.append(f'<mark class="num bad" title="not in the engine output">{html.escape(text[n.start:n.end])}</mark>')
        pos = n.end
    out.append(html.escape(text[pos:]))
    return "".join(out).replace("\n", "<br>")


def ansi(report):
    """The text for a terminal: green numbers are traced, red ones are not."""
    text, out, pos = _plain(report.text), [], 0
    for f in report.findings:
        n = f.number
        colour = "\033[1;32m" if f.status == "sourced" else "\033[1;97;41m"
        out += [text[pos:n.start], colour, text[n.start:n.end], "\033[0m"]
        pos = n.end
    out.append(text[pos:])
    return "".join(out)


def table(report):
    rows = []
    for f in report.findings:
        if f.status == "sourced":
            src = " / ".join([f.source] + f.also)
            rows.append(f"  \033[32mOK  \033[0m {f.number.text:>14s}  <- {src} = {f.source_value}  ({f.rule})")
        else:
            rows.append(f"  \033[1;31mFAIL\033[0m {f.number.text:>14s}  <- NOT IN THE ENGINE OUTPUT")
    return "\n".join(rows)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        from agent import demo
        demo.main(sys.argv[2:])
    elif len(sys.argv) == 3:
        r = check(open(sys.argv[1], encoding="utf-8").read(), open(sys.argv[2], encoding="utf-8").read())
        print(ansi(r) + "\n\n" + table(r) + "\n\n" + r.summary())
        sys.exit(0 if r.passed else 1)
    else:
        print(__doc__)
