from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import sys
import wave

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != SCRIPT_DIR]

from supertonic import TTS


def _float_audio_to_wav(audio: np.ndarray, *, sample_rate: int) -> bytes:
    pcm = np.asarray(audio, dtype=np.float32)
    if pcm.ndim > 1:
        pcm = pcm[0]
    pcm = np.clip(pcm, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm16.tobytes())
        return buffer.getvalue()


def main() -> int:
    tts = TTS(auto_download=True)
    voice_styles: dict[str, object] = {}
    sample_rate = int(getattr(tts, "sample_rate", 44_100))

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            payload = json.loads(line)
            text = str(payload["text"])
            voice = str(payload["voice"])
            language = str(payload["language"])
            total_steps = int(payload["total_steps"])
            speed = float(payload["speed"])

            style = voice_styles.get(voice)
            if style is None:
                style = tts.get_voice_style(voice_name=voice)
                voice_styles[voice] = style

            wav, duration = tts.synthesize(
                text,
                voice_style=style,
                total_steps=total_steps,
                speed=speed,
                lang=language,
            )
            audio = _float_audio_to_wav(wav, sample_rate=sample_rate)
            response = {
                "ok": True,
                "audio": base64.b64encode(audio).decode("ascii"),
                "duration": float(duration[0]) if len(duration) else 0.0,
            }
        except Exception as exc:  # pragma: no cover - exercised via parent process integration
            response = {"ok": False, "error": str(exc) or exc.__class__.__name__}

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
