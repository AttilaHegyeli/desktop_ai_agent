"""Text embeddings from the local Ollama, on the CPU only (used by the site maps).

Why: the user asks in English ("settings") while a site may answer in Hungarian
("Beállítások"); a keyword match cannot connect the two, and the owner does not want
Gemma to do extra translation work. An embedding model turns each text into a vector,
and texts with the same meaning get similar vectors in any language.

Model: paraphrase-multilingual:278m won the owner's test on 2026-11-25 (45 English
requests against ~180 Hungarian/English labels of YouTube, Google Account, Gmail and
GitHub): 25/45 right first, 32/45 in the top 3, ~17 ms per request, ~13 s per 1,000
labels, ~560 MB RAM. ``options.num_gpu = 0`` keeps it on the CPU (0 VRAM), so it never
takes video memory from Gemma.

Vectors are normalized here, so the similarity of two of them is their dot product.
"""
from __future__ import annotations

import base64
import json
import math
import urllib.error
import urllib.request
from array import array
from typing import Any, Callable, Iterable, Sequence

DEFAULT_MODEL = "paraphrase-multilingual:278m"
DEFAULT_HOST = "http://127.0.0.1:11434"
BATCH = 64
TIMEOUT_SECONDS = 120
KEEP_ALIVE = "2m"  # long enough for a map and a few finds, short enough to free the RAM


class EmbeddingError(RuntimeError):
    """Embeddings are not available (model missing, Ollama down, stopped). The message says why."""


def settings() -> tuple[str, str]:
    """(model, Ollama address) from config/setup.json's "ollama" block.

    ``embed_model`` is optional; the address is the same ``host`` the chat uses.
    """
    try:
        from desktop_ai_agent.core.config_loader import load_setup

        block = load_setup().get("ollama") or {}
    except (OSError, ValueError, AttributeError):
        block = {}
    model = str(block.get("embed_model") or DEFAULT_MODEL)
    host = str(block.get("host") or DEFAULT_HOST).rstrip("/")
    return model, host


def _post(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:  # pragma: no cover - real network
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def embed(
    texts: Sequence[str],
    model: str | None = None,
    host: str | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[list[float]]:
    """One normalized vector per text, in order. Raises EmbeddingError when unavailable."""
    if not texts:
        return []
    if model is None or host is None:
        default_model, default_host = settings()
        model, host = model or default_model, host or default_host
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH):
        if should_stop is not None and should_stop():
            raise EmbeddingError("stopped")
        chunk = list(texts[start : start + BATCH])
        body = {"model": model, "input": chunk, "options": {"num_gpu": 0}, "keep_alive": KEEP_ALIVE}
        try:
            data = _post(f"{host}/api/embed", body, TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise EmbeddingError(f"the embedding model is not installed (run: ollama pull {model})") from None
            raise EmbeddingError(f"Ollama refused the embedding request (HTTP {exc.code})") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise EmbeddingError(f"Ollama is not reachable at {host} ({exc})") from None
        got = data.get("embeddings") if isinstance(data, dict) else None
        if not isinstance(got, list) or len(got) != len(chunk):
            raise EmbeddingError("Ollama returned no embeddings")
        vectors.extend(normalize(v) for v in got)
    return vectors


def normalize(vector: Iterable[float]) -> list[float]:
    values = [float(v) for v in vector]
    length = math.sqrt(sum(v * v for v in values))
    return [v / length for v in values] if length else values


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def pack(vectors: Sequence[Sequence[float]]) -> str:
    """All vectors as one base64 text of 32-bit floats (about 4 KB per 768-number vector)."""
    data = array("f")
    for vector in vectors:
        data.extend(vector)
    return base64.b64encode(data.tobytes()).decode("ascii")


def unpack(blob: str, dim: int) -> list[array]:
    """The vectors `pack` stored, `dim` numbers each."""
    data = array("f")
    data.frombytes(base64.b64decode(blob.encode("ascii")))
    if dim <= 0 or len(data) % dim:
        raise ValueError("stored vectors do not match their size")
    return [data[i : i + dim] for i in range(0, len(data), dim)]
