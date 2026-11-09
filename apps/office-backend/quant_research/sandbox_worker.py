"""Only run inside the isolated image; never import generated modules on the host."""
import contextlib
import importlib.util
import json
import sys
import pandas as pd

with open("/input/input.json") as f:
    payload = json.load(f)
rows = payload["dataset"]["rows"]
prices = pd.DataFrame([r[1:] for r in rows], index=pd.to_datetime([r[0] for r in rows]), columns=payload["dataset"]["symbols"])
with contextlib.redirect_stdout(sys.stderr):
    spec = importlib.util.spec_from_file_location("strategy", "/input/strategy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    weights = [module.target_weights(prices.iloc[:i + 1].copy(), payload["parameters"]) for i in range(len(prices))]
print(json.dumps(weights, allow_nan=False))

