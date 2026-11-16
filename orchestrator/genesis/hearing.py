"""Speech to text for the player's voice, run locally with faster-whisper.

Optional: without the ``faster-whisper`` package the game simply has no 🎤.
The model (``base.en``, ~140 MB, downloaded on first use) runs on the CPU so the
GPU stays free for the villagers' language models. A clip is a few seconds, so
that is fast enough.
"""
from __future__ import annotations

import io
import logging
import threading

log = logging.getLogger("genesis.hearing")

MODEL = "base.en"
MAX_BYTES = 4 * 1024 * 1024  # (about a minute of compressed speech: plenty for a line)

try:
    from faster_whisper import WhisperModel  # type: ignore
except Exception:  # not installed (or its native parts won't load): no hearing
    WhisperModel = None

_model = None
_lock = threading.Lock()


def available() -> bool:
    return WhisperModel is not None


def _load():
    global _model
    if _model is None:
        log.info("loading the speech model %s (first use downloads it)", MODEL)
        _model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe(audio: bytes, names: list[str] | None = None) -> str:
    """The words in a recorded clip (any format the browser records: webm, ogg...).
    ``names`` (the villagers') are given as a hint so they come out spelled right."""
    if not available():
        raise RuntimeError("speech recognition is not installed (pip install faster-whisper)")
    if not audio or len(audio) > MAX_BYTES:
        return ""
    with _lock:  # one at a time: the model is not shared between threads
        model = _load()
        hint = ("Names: " + ", ".join(names[:40]) + ".") if names else None
        segments, _info = model.transcribe(io.BytesIO(audio), language="en", beam_size=1, vad_filter=True,
                                           initial_prompt=hint, condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()
    return text
