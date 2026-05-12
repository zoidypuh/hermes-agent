from __future__ import annotations

import base64
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Dict, Optional

SUPERTONIC_SUPPORTED_VOICES = {
    "F1": "Female 1",
    "F2": "Female 2",
    "F3": "Female 3",
    "F4": "Female 4",
    "F5": "Female 5",
    "M1": "Male 1",
    "M2": "Male 2",
    "M3": "Male 3",
    "M4": "Male 4",
    "M5": "Male 5",
}
SUPERTONIC_SUPPORTED_LANGUAGES = {
    "en": "English",
    "ko": "Korean",
    "es": "Spanish",
    "pt": "Portuguese",
    "fr": "French",
}
SUPERTONIC_DEFAULT_VOICE = "M4"
SUPERTONIC_DEFAULT_LANGUAGE = "en"
SUPERTONIC_DEFAULT_TOTAL_STEPS = 1
SUPERTONIC_DEFAULT_SPEED = 1.2
_WORKER_CACHE: dict[str, "_SupertonicWorkerClient"] = {}
_WORKER_CACHE_LOCK = threading.Lock()


def _module_available(module_name: str, *, python_path: Optional[str] = None) -> bool:
    if python_path:
        try:
            result = subprocess.run(
                [python_path, "-c", f"import {module_name}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


def detect_supertonic_python_path() -> str:
    candidates: list[Path] = []
    current_python = Path(sys.executable).expanduser()
    if current_python.is_file() and _module_available("supertonic", python_path=str(current_python)):
        candidates.append(current_python)

    for root in (
        Path.home() / "coding" / "supertonic",
        Path.cwd().resolve().parent / "supertonic",
    ):
        for env_dir in (".venv312", ".venv", "venv"):
            candidates.extend(
                [
                    root / env_dir / "bin" / "python",
                    root / env_dir / "bin" / "python3",
                ]
            )

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return os.path.abspath(str(candidate))
    return ""


def resolve_supertonic_python_path(value: str | None) -> str:
    text = str(value or "").strip()
    if text:
        candidate = Path(text).expanduser()
    else:
        detected = detect_supertonic_python_path()
        candidate = Path(detected) if detected else Path()

    if not str(candidate):
        raise ValueError("Enter a Python executable that has the supertonic package installed.")
    if not candidate.is_file():
        raise ValueError(f"Supertonic Python executable was not found: {candidate}")
    if not os.access(candidate, os.X_OK):
        raise ValueError(f"Supertonic Python executable is not runnable: {candidate}")
    return os.path.abspath(str(candidate))


def normalize_supertonic_voice(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return SUPERTONIC_DEFAULT_VOICE
    if normalized not in SUPERTONIC_SUPPORTED_VOICES:
        raise ValueError(
            f"Unsupported Supertonic voice '{value}'. Choose one of: {', '.join(SUPERTONIC_SUPPORTED_VOICES)}."
        )
    return normalized


def normalize_supertonic_language(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return SUPERTONIC_DEFAULT_LANGUAGE
    if normalized not in SUPERTONIC_SUPPORTED_LANGUAGES:
        raise ValueError(
            f"Unsupported Supertonic language '{value}'. Choose one of: {', '.join(SUPERTONIC_SUPPORTED_LANGUAGES)}."
        )
    return normalized


def normalize_supertonic_total_steps(value: int | str | None) -> int:
    text = str(SUPERTONIC_DEFAULT_TOTAL_STEPS if value in (None, "") else value).strip()
    try:
        steps = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("Supertonic total steps must be a whole number.") from exc
    if steps < 1:
        raise ValueError("Supertonic total steps must be at least 1.")
    return steps


def normalize_supertonic_speed(value: float | str | None) -> float:
    text = str(SUPERTONIC_DEFAULT_SPEED if value in (None, "") else value).strip()
    try:
        speed = float(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("Supertonic speed must be a number.") from exc
    if speed <= 0:
        raise ValueError("Supertonic speed must be greater than 0.")
    return speed


def _worker_script_path() -> str:
    path = Path(__file__).with_name("supertonic_worker.py")
    if not path.is_file():
        raise ValueError(f"Supertonic worker script was not found: {path}")
    return str(path.resolve())


class _SupertonicWorkerClient:
    def __init__(self, python_path: str):
        self.python_path = python_path
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None

    def _drain_stderr(self, stream) -> None:
        try:
            for line in stream:
                text = line.rstrip()
                if not text:
                    continue
                self._stderr_lines.append(text)
                if len(self._stderr_lines) > 40:
                    self._stderr_lines[:] = self._stderr_lines[-40:]
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    def _stderr_detail(self) -> str:
        if not self._stderr_lines:
            return ""
        return " ".join(self._stderr_lines[-3:])

    def _terminate(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        with contextlib.suppress(Exception):
            if process.stdin:
                process.stdin.close()
        with contextlib.suppress(Exception):
            process.terminate()
        with contextlib.suppress(Exception):
            process.wait(timeout=1)

    def _start(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            return

        self._terminate()
        env = dict(os.environ)
        for key in list(env):
            if key.startswith("PYTHON") or key == "VIRTUAL_ENV":
                env.pop(key, None)
        env["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            [self.python_path, _worker_script_path()],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._process = process
        self._stderr_lines.clear()
        if process.stderr is not None:
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(process.stderr,),
                daemon=True,
            )
            self._stderr_thread.start()

    def _request(self, payload: dict[str, object]) -> dict[str, object]:
        last_error = ""
        for _attempt in range(2):
            with self._lock:
                self._start()
                process = self._process
                if process is None or process.stdin is None or process.stdout is None:
                    raise ValueError("Supertonic worker failed to start.")
                try:
                    process.stdin.write(json.dumps(payload) + "\n")
                    process.stdin.flush()
                    line = process.stdout.readline()
                except (BrokenPipeError, OSError) as exc:
                    last_error = str(exc)
                    self._terminate()
                    continue

                if not line:
                    last_error = self._stderr_detail() or "Supertonic worker exited unexpectedly."
                    self._terminate()
                    continue

                try:
                    response = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Supertonic worker returned invalid JSON: {exc}") from exc
                if isinstance(response, dict):
                    return response
                raise ValueError("Supertonic worker returned an unexpected response.")

        detail = last_error or self._stderr_detail() or "Supertonic worker exited unexpectedly."
        raise ValueError(detail)

    def synthesize(
        self,
        *,
        text: str,
        voice: str,
        language: str,
        total_steps: int,
        speed: float,
    ) -> bytes:
        response = self._request(
            {
                "text": text,
                "voice": voice,
                "language": language,
                "total_steps": total_steps,
                "speed": speed,
            }
        )
        if not response.get("ok"):
            raise ValueError(str(response.get("error") or "Supertonic synthesis failed."))

        audio = str(response.get("audio") or "")
        if not audio:
            raise ValueError("Supertonic returned no audio.")
        try:
            return base64.b64decode(audio)
        except (ValueError, TypeError) as exc:
            raise ValueError("Supertonic returned invalid audio data.") from exc


def _worker_client(python_path: str) -> _SupertonicWorkerClient:
    with _WORKER_CACHE_LOCK:
        client = _WORKER_CACHE.get(python_path)
        if client is None:
            client = _SupertonicWorkerClient(python_path)
            _WORKER_CACHE[python_path] = client
        return client


def synthesize_supertonic(
    text: str,
    *,
    python_path: str | None = None,
    voice: str | None = None,
    language: str | None = None,
    total_steps: int | str | None = None,
    speed: float | str | None = None,
) -> bytes:
    if not text.strip():
        return b""
    resolved_python_path = resolve_supertonic_python_path(python_path)
    normalized_voice = normalize_supertonic_voice(voice)
    normalized_language = normalize_supertonic_language(language)
    normalized_total_steps = normalize_supertonic_total_steps(total_steps)
    normalized_speed = normalize_supertonic_speed(speed)
    return _worker_client(resolved_python_path).synthesize(
        text=text,
        voice=normalized_voice,
        language=normalized_language,
        total_steps=normalized_total_steps,
        speed=normalized_speed,
    )


def generate_supertonic_tts(text: str, output_path: str, tts_config: Dict[str, Any]) -> str:
    cfg = tts_config.get("supertonic") if isinstance(tts_config.get("supertonic"), dict) else {}
    python_path = (
        cfg.get("python_path")
        or cfg.get("supertonic_python_path")
        or tts_config.get("supertonic_python_path")
    )
    voice = cfg.get("voice") or cfg.get("supertonic_voice") or tts_config.get("supertonic_voice")
    language = cfg.get("language") or cfg.get("supertonic_language") or tts_config.get("supertonic_language")
    total_steps = cfg.get("total_steps", cfg.get("supertonic_total_steps", tts_config.get("supertonic_total_steps")))
    speed = cfg.get("speed", cfg.get("supertonic_speed", tts_config.get("supertonic_speed")))

    audio = synthesize_supertonic(
        text,
        python_path=python_path,
        voice=voice,
        language=language,
        total_steps=total_steps,
        speed=speed,
    )
    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(audio)
    return str(output)


def register(ctx) -> None:
    """Plugin marker.

    Hermes's current TTS path is a built-in tool, so tools.tts_tool imports this
    provider directly. The register hook intentionally has no side effects until
    Hermes grows a first-class TTS provider registry.
    """
    return None
