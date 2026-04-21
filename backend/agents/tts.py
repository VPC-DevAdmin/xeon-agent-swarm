"""
Text-to-speech synthesis using edge-tts (Microsoft neural voices, no API key).

Synthesizes the document executive summary to an MP3 file stored at
AUDIO_DIR/{run_id}.mp3, served by the backend at /audio/{run_id}.mp3.

Falls back silently if edge-tts is not installed or the synthesis fails
(TTS is a bonus feature, not critical to the demo flow).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_DIR = Path(os.getenv("AUDIO_DIR", "/data/audio"))

# Neural voice — clear, professional, good for technical reports
_VOICE = "en-US-AriaNeural"
# Max characters sent to TTS (keeps latency reasonable)
_MAX_CHARS = 600


def _truncate(text: str) -> str:
    """Trim to _MAX_CHARS at a sentence boundary where possible."""
    if len(text) <= _MAX_CHARS:
        return text
    # Try to cut at a period before the limit
    cut = text[:_MAX_CHARS].rfind(".")
    if cut > _MAX_CHARS // 2:
        return text[: cut + 1]
    return text[:_MAX_CHARS] + "…"


async def synthesize_speech(text: str, run_id: str) -> str | None:
    """
    Synthesize *text* to MP3 via edge-tts.

    Returns the relative URL path ("/audio/{run_id}.mp3") on success,
    or None if edge-tts is unavailable or synthesis fails.
    """
    try:
        import edge_tts  # optional dependency
    except ImportError:
        logger.warning(
            "edge-tts not installed — TTS disabled. "
            "Add 'edge-tts>=6.1.9' to requirements.txt and rebuild the backend image."
        )
        return None

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{run_id}.mp3"
    output_path = AUDIO_DIR / filename

    snippet = _truncate(text)
    logger.info("TTS synthesizing %d chars to %s via %s", len(snippet), output_path, _VOICE)
    try:
        communicate = edge_tts.Communicate(snippet, _VOICE)
        await communicate.save(str(output_path))
        logger.info("TTS saved: %s (%d chars)", output_path, len(snippet))
        return f"/audio/{filename}"
    except Exception as exc:
        logger.warning(
            "TTS synthesis failed (%s: %s). "
            "edge-tts requires outbound HTTPS to speech.platform.bing.com — "
            "check firewall / Docker network settings.",
            type(exc).__name__, exc,
        )
        return None
