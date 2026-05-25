"""Phase 4.2 — Face embedding extraction and similarity.

Uses insightface (ArcFace, buffalo_sc model ~80MB) running on CPU. The model
is lazy-loaded on first call to keep server startup time low and to allow
test environments without the dependency installed to import this module.

In tests we monkeypatch `extract_embedding` directly so the heavy model is
never touched.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ArcFace embeddings produced by buffalo_sc are 512-dim float32 vectors.
EMBEDDING_DIM = 512

# Lazy singletons — guarded by lock to avoid double-init under multiple
# concurrent first calls in a worker.
_model = None
_model_lock = threading.Lock()


def _get_model():
    """Load insightface FaceAnalysis lazily on first use.

    Returns None if the dependency is not installed (so the upload endpoint
    can still save the image and quality status without crashing the request).
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            import insightface  # type: ignore
        except ImportError:
            logger.warning(
                "insightface not installed — face embedding extraction disabled"
            )
            return None
        try:
            app = insightface.app.FaceAnalysis(
                name="buffalo_sc",
                providers=["CPUExecutionProvider"],
            )
            app.prepare(ctx_id=0, det_size=(320, 320))
            _model = app
            logger.info("insightface buffalo_sc model loaded")
        except Exception:
            logger.exception("Failed to initialise insightface model")
            return None
        return _model


def extract_embedding(image_bytes: bytes) -> Optional[list[float]]:
    """Return a 512-dim ArcFace embedding for the most confident face.

    Returns None if:
      - the dependency is not available
      - the bytes are not a decodable image
      - no face is detected
    """
    model = _get_model()
    if model is None:
        return None

    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        logger.warning("cv2/numpy not installed — face embedding disabled")
        return None

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None

    try:
        faces = model.get(img)
    except Exception:
        logger.exception("insightface model.get() failed")
        return None
    if not faces:
        return None

    best = max(faces, key=lambda f: getattr(f, "det_score", 0.0))
    embedding = getattr(best, "embedding", None)
    if embedding is None:
        return None
    return [float(x) for x in embedding.tolist()]


def warmup_model() -> bool:
    """Pre-load insightface model eagerly. Returns True when model is ready.

    Call from a startup background thread so the first /face/upload request
    is not slowed down by the ~30-60s ONNX model cold-start on Windows.
    """
    return _get_model() is not None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 on degenerate input.

    Implemented with the stdlib so unit tests do not need numpy.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom < 1e-9:
        return 0.0
    return dot / denom
