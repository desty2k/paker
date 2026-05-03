"""Load numpy from an encrypted paker bundle and run computations.

Requires numpy installed for the dump step: pip install numpy
"""

import json
import os
import sys
import paker

KEY = os.urandom(32)

print("Dumping numpy (encrypted, includes native .so extensions)...")
bundle = paker.dumps("numpy", key=KEY)
blob = json.dumps(bundle)
print(f"Bundle: {len(blob):,} bytes\n")

for name in list(sys.modules):
    if name.startswith("numpy"):
        del sys.modules[name]

with paker.loads(blob, key=KEY) as imp:
    import numpy as np

    a = np.array([1, 2, 3, 4, 5])
    print(f"Array: {a}")
    print(f"Sum: {a.sum()}")
    print(f"Mean: {a.mean()}")
    print(f"Std: {a.std():.4f}")

    m = np.random.rand(3, 3)
    print(f"\nRandom 3x3 matrix:\n{m.round(3)}")
    print(f"Determinant: {np.linalg.det(m):.6f}")

    x = np.linspace(0, 2 * np.pi, 8)
    print(f"\nsin(0..2π): {np.sin(x).round(3)}")
