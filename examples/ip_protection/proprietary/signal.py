"""Fake trading signal. The 'secret sauce' the vendor doesn't want leaked.

Every string, coefficient and identifier in this file is what an attacker
would hope to pull out of a shipped bundle. The demo shows that after
packing, none of this is recoverable without a live key.
"""

# These would be real calibrated parameters in a production version.
PROPRIETARY_COEFFICIENTS = {
    "momentum_window": 21,
    "volatility_cap": 0.045,
    "mean_reversion_weight": 0.318,
    "breakout_z_threshold": 2.7,
    "kelly_fraction": 0.25,
}

_SECRET_MAGIC_STRING = "paker-demo-proprietary-alpha-v4"


def _normalize(prices: list[float]) -> list[float]:
    mean = sum(prices) / len(prices)
    return [p - mean for p in prices]


def _z_score(x: float, series: list[float]) -> float:
    mean = sum(series) / len(series)
    var = sum((v - mean) ** 2 for v in series) / len(series)
    return 0.0 if var == 0 else (x - mean) / (var ** 0.5)


def recommend(prices: list[float]) -> dict:
    """Return a trading recommendation for a price series.

    Toy logic. The point of the demo isn't that this is a good strategy,
    it's that a competitor who gets their hands on the shipped bundle
    can't read these coefficients, function names, or the magic string.
    """
    if len(prices) < PROPRIETARY_COEFFICIENTS["momentum_window"]:
        return {"action": "HOLD", "reason": "insufficient history", "confidence": 0.0}

    normalized = _normalize(prices)
    z = _z_score(prices[-1], prices[-PROPRIETARY_COEFFICIENTS["momentum_window"]:])
    vol = (sum(x * x for x in normalized) / len(normalized)) ** 0.5

    if vol > PROPRIETARY_COEFFICIENTS["volatility_cap"]:
        return {"action": "HOLD", "reason": "volatility gate", "confidence": 0.0}
    if z > PROPRIETARY_COEFFICIENTS["breakout_z_threshold"]:
        confidence = min(1.0, z / 4.0) * PROPRIETARY_COEFFICIENTS["kelly_fraction"]
        return {"action": "BUY", "reason": f"breakout z={z:.2f}", "confidence": confidence}
    if z < -PROPRIETARY_COEFFICIENTS["breakout_z_threshold"]:
        confidence = min(1.0, -z / 4.0) * PROPRIETARY_COEFFICIENTS["mean_reversion_weight"]
        return {"action": "SELL", "reason": f"breakdown z={z:.2f}", "confidence": confidence}
    return {"action": "HOLD", "reason": f"z={z:.2f} within band", "confidence": 0.0}
