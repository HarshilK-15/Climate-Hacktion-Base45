"""Guard tests:  python -m guard.test_rules   Owner: Data Sci."""
from datetime import datetime, timedelta, timezone

from guard.rules import evaluate

NOW = datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc)


def series(watts_list, step_s=10, end=NOW):
    n = len(watts_list)
    return [{"ts": (end - timedelta(seconds=step_s * (n - 1 - i))).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "watts": w} for i, w in enumerate(watts_list)]


def kinds(alerts):
    return {a["type"] for a in alerts}


def test_offline():
    r = series([100] * 5, end=NOW - timedelta(minutes=5))
    assert "sensor_offline" in kinds(evaluate(r, NOW, local_hour=20))


def test_spike():
    r = series([150] * 30 + [2400])
    assert "spike" in kinds(evaluate(r, NOW, local_hour=20))


def test_spike_hidden_inside_a_batch():
    """A spike followed by normal readings (same upload) must still be caught."""
    r = series([150] * 30 + [2400, 2400, 150, 150])
    assert "spike" in kinds(evaluate(r, NOW, local_hour=20))


def test_low_load():
    r = series([200] * 40)
    assert "low_load" in kinds(evaluate(r, NOW, local_hour=20))


def test_no_low_load_when_busy():
    r = series([2000] * 40)
    assert "low_load" not in kinds(evaluate(r, NOW, local_hour=20))


def test_diesel_in_sunshine():
    r = series([500] * 40)
    assert "diesel_in_sunshine" in kinds(evaluate(r, NOW, local_hour=12))
    assert "diesel_in_sunshine" not in kinds(evaluate(r, NOW, local_hour=22))


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS ", t.__name__)
    print(f"\nAll {len(tests)} guard tests passed.")
