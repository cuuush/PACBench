"""Check the actual local embedding dependency before model work can start."""
from __future__ import annotations

import json
import math
import time
from urllib.request import Request, urlopen

from pacbench.reme_memos_setup import local_endpoint


def check(endpoint="http://127.0.0.1:11435/v1", *, model="nomic-embed-text",
          dimensions=768, timeout=30, interface='openai'):
    base = local_endpoint(endpoint)
    if interface not in {'openai','ollama'}:
        raise ValueError('Unknown embedding API interface')
    if not isinstance(dimensions, int) or dimensions < 1:
        raise ValueError("An explicit positive embedding dimension is required")
    started = time.monotonic()
    target=base+'/embeddings' if interface=='openai' else base
    payload={"model":model,"input":"Local embedding dependency readiness check."}
    if interface=='ollama':payload.update(dimensions=dimensions,truncate=False)
    request = Request(target, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        rows = result.get("data") if interface=='openai' else result.get('embeddings')
        vector = (rows[0]["embedding"] if interface=='openai' else rows[0]) if isinstance(rows, list) and len(rows) == 1 else None
        if (not isinstance(vector, list) or len(vector) != dimensions or
                any(isinstance(n, bool) or not isinstance(n, (float, int)) or
                    not math.isfinite(n) for n in vector)):
            raise ValueError("Unexpected local embedding response")
    except Exception as error:
        # Do not include response bodies or environment values in public errors.
        raise RuntimeError("Local embedding preflight failed (" + type(error).__name__ +
                           "); no provider model initialization permitted") from None
    return {"protocol": "local-embedding-preflight-v1", "endpoint": base,
            "model": model, "dimensions": dimensions,
            "interface":interface,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "paid_inference": False, "state": "pass"}
