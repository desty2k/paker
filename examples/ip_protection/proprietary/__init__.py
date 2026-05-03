"""Stand-in for a proprietary package the vendor wants to protect.

The algorithm is deliberately simple so the demo stays readable. Imagine
this is a multi-thousand-line trading strategy, a model inference pipeline,
a pricing engine, or any other IP-heavy Python code.
"""
from proprietary.signal import recommend, PROPRIETARY_COEFFICIENTS

__all__ = ["recommend", "PROPRIETARY_COEFFICIENTS"]
