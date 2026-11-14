"""Embedders. Voyage when VOYAGE_API_KEY is set; otherwise a deterministic local fallback."""
import hashlib
import json
import math
import os
import re
import urllib.request

from .config import EMBED_DIM, voyage_key


class HashEmbedder:
    """Offline fallback: signed feature hashing of words and bigrams into 1024 dims.
    Good enough to rank near-identical task texts; not a semantic model."""

    name = "local-hash-1024 (offline fallback, not semantic)"

    def embed(self, texts, input_type="document"):
        return [self._one(t) for t in texts]

    def _one(self, text):
        words = re.findall(r"[a-z0-9]+", text.lower())
        feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
        v = [0.0] * EMBED_DIM
        for f in feats:
            h = int(hashlib.sha1(f.encode()).hexdigest(), 16)
            v[h % EMBED_DIM] += 1.0 if (h >> 12) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


class VoyageEmbedder:
    def __init__(self, api_key, base_url=None, model=None):
        self.api_key = api_key
        base = (base_url or os.getenv("VOYAGE_BASE_URL") or "https://api.voyageai.com/v1").rstrip("/")
        if not base.startswith("http"):
            base = "https://" + base
        if not base.endswith("/v1"):
            base += "/v1"
        self.url = base + "/embeddings"
        self.model = model or os.getenv("VOYAGE_MODEL", "voyage-3.5")
        self.name = f"{self.model} (1024-d) via {base}"

    def embed(self, texts, input_type="document"):
        body = json.dumps({"input": texts, "model": self.model,
                           "input_type": input_type, "output_dimension": EMBED_DIM}).encode()
        req = urllib.request.Request(self.url, data=body, headers={
            "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        import certifi, ssl
        ctx = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            data = json.load(r)["data"]
        return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]


def default_embedder():
    key = voyage_key()
    return VoyageEmbedder(key) if key else HashEmbedder()


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))
