"""Local-to-xAI recovery preserves input guards, opt-in, and profile scope."""

import json
import shlex
import sys
import wave
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock

import pytest
import yaml

from tools import transcription_tools as tt


@pytest.mark.parametrize(
    "xai_result",
    [
        {"success": True, "transcript": "cloud text", "provider": "xai"},
        {"success": False, "transcript": "", "error": "cloud offline"},
        {"success": False, "transcript": "", "error": "silence", "no_speech": True},
    ],
)
@pytest.mark.parametrize(
    "provider, fallback, local_result, expected_provider",
    [
        ("local", "xai", {"success": False, "error": "CUDA out of memory"}, "xai"),
        ("local_command", "xai", {"success": False, "error": "offline"}, "xai"),
        ("none", "xai", {"success": False, "error": "missing backend"}, "xai"),
        ("local_command", "", {"success": False, "error": "offline"}, None),
        ("local", "xai", {"success": True, "transcript": "local text", "provider": "local"}, "local"),
        ("local", "xai", {"success": True, "transcript": "", "provider": "local"}, "local"),
        ("local", "xai", {"success": False, "no_speech": True, "error": "silence"}, None),
        ("openai", "xai", {"success": False, "error": "offline"}, None),
    ],
)
def test_local_recovery_contract(tmp_path, monkeypatch, provider, fallback, local_result, expected_provider, xai_result):
    audio = tmp_path / "clip.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 160)
    config = {"provider": "local_command" if provider == "none" else provider,
              "fallback_provider": fallback, "cloud_trim_silence": False}
    monkeypatch.setattr(tt, "_load_stt_config", lambda: config)
    monkeypatch.setattr(tt, "_get_provider", lambda _: provider)
    for name in ("local", "local_command", "openai"):
        monkeypatch.setattr(tt, f"_transcribe_{name}", lambda *a, **kw: dict(local_result))
    monkeypatch.setattr(tt, "_no_provider_error", lambda *a: dict(local_result))
    xai = Mock(return_value=xai_result)
    monkeypatch.setattr(tt, "_transcribe_xai", xai)
    hooks = []
    def hook(**kwargs):
        hooks.append(kwargs)
        return kwargs["model"], "de", kwargs["prompt"]
    monkeypatch.setattr(tt, "_apply_pre_transcription_hook", hook)

    result = tt.transcribe_audio(str(audio), model="small", source="voice_mode")
    assert result.get("provider") == (xai_result.get("provider") if expected_provider == "xai" else expected_provider)
    if expected_provider == "xai":
        xai.assert_called_once_with(str(audio), "grok-stt", language="de", prompt=None)
        assert result["fallback_from"] == ("local_command" if provider == "none" else provider)
        assert hooks[-1]["provider"] == "xai"
        assert hooks[-1]["source"] == "voice_mode"
        assert result["success"] == xai_result["success"]
        assert result.get("no_speech") == xai_result.get("no_speech")
        if not xai_result["success"] and not xai_result.get("no_speech"):
            assert local_result["error"] in result["error"] and xai_result["error"] in result["error"]
    else:
        xai.assert_not_called()
        assert "fallback_from" not in result

    # A fallback must not turn local success into a cloud upload, nor bypass source guards.
    audio.unlink()
    xai.reset_mock()
    assert not tt.transcribe_audio(str(audio))["success"]
    xai.assert_not_called()
    audio.write_bytes(b"audio")
    config["enabled"] = False
    assert not tt.transcribe_audio(str(audio))["success"]
    xai.assert_not_called()
    config["enabled"] = True
    if expected_provider == "xai":
        # Conversion and trim artifacts belong to this attempt; neighboring files survive.
        caf = tmp_path / "clip.caf"
        caf.write_bytes(b"caf")
        neighbor = tmp_path / "clip.wav"
        neighbor.write_bytes(b"neighbor")
        converted = []
        def convert(path, work_dir):
            target = Path(work_dir) / "converted.wav"
            target.write_bytes(b"wav")
            converted.append(target)
            return str(target)
        trim_dir = tmp_path / "trim"
        trim_dir.mkdir()
        trimmed = trim_dir / "clip.m4a"
        trimmed.write_bytes(b"trimmed")
        monkeypatch.setattr(tt, "_convert_caf_to_wav", convert)
        trim = Mock(return_value=str(trimmed))
        monkeypatch.setattr(tt, "_trim_silence_for_cloud_stt", trim)
        tt.transcribe_audio(str(caf))
        assert not converted[0].exists() and not trim_dir.exists()
        assert neighbor.read_bytes() == b"neighbor"
        trim.assert_called_once_with(str(converted[0]), config)
        assert xai.call_args.args[0] == str(trimmed)
        xai.reset_mock()

        from tools.transcription_common import MAX_FILE_SIZE
        with audio.open("wb") as stream:
            stream.truncate(MAX_FILE_SIZE + 1)
        result = tt.transcribe_audio(str(audio))
        assert not result["success"] and "File too large" in result["error"]
        xai.assert_not_called()


@pytest.mark.parametrize("initial_mode", ["fail", "timeout", "missing", "cloud_error", "no_key", "silence"])
def test_real_local_command_and_xai_recovery_profiles(tmp_path, monkeypatch, initial_mode):
    from agent import secret_scope as ss
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.voice_mode import check_voice_requirements

    mode = tmp_path / "mode.txt"
    mode.write_text(initial_mode)
    helper = tmp_path / "local.py"
    helper.write_text(
        "import pathlib, sys, time\n"
        "mode = pathlib.Path(sys.argv[1]).read_text()\n"
        "if mode == 'timeout': time.sleep(30)\n"
        "if mode not in ('success', 'silence'): sys.exit('local service offline')\n"
        "pathlib.Path(sys.argv[3], 'transcript.txt').write_text('local recovered' if mode == 'success' else '')\n"
    )
    audio = tmp_path / "clip.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 160)

    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, self.headers["Authorization"], body))
            status = 503 if initial_mode == "cloud_error" else 200
            payload = {"error": {"message": "cloud unavailable"}} if status == 503 else {"text": "cloud recovered"}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(tt, "_HAS_FASTER_WHISPER", False)
    monkeypatch.setattr(tt, "_try_lazy_install_stt", lambda: False)
    monkeypatch.setattr("tools.transcription_local._find_whisper_binary", lambda: None)
    monkeypatch.setattr("tools.voice_mode._audio_available", lambda: True)
    monkeypatch.setattr("tools.voice_mode.detect_audio_environment", lambda: {"available": True, "warnings": []})
    monkeypatch.setenv("XAI_API_KEY", "launch-profile-key-must-not-leak")
    monkeypatch.setenv("HERMES_LOCAL_STT_COMMAND", "launch-profile-command-must-not-run")
    if initial_mode == "no_key":
        monkeypatch.setattr("tools.xai_http.resolve_xai_http_credentials",
                            lambda **kw: {"provider": "xai", "api_key": "", "base_url": "https://api.x.ai/v1"})
    homes = []
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(helper))} {shlex.quote(str(mode))} {{input_path}} {{output_dir}}"
    for name, language in (("a", "en"), ("b", "de")):
        home = tmp_path / name
        home.mkdir()
        (home / "config.yaml").write_text(yaml.safe_dump({"stt": {
            "provider": "local_command", "fallback_provider": "xai", "language": language,
            "local_command": {"timeout_seconds": 0.1}, "cloud_trim_silence": False,
            "xai": {"base_url": f"http://127.0.0.1:{server.server_port}/v1"},
        }}))
        env = {}
        if initial_mode != "no_key":
            env["XAI_API_KEY"] = f"key-{name}"
        if initial_mode != "missing":
            env["HERMES_LOCAL_STT_COMMAND"] = command
        (home / ".env").write_text("".join(f"{key}={value}\n" for key, value in env.items()))
        homes.append(home)

    ss.set_multiplex_active(True)
    try:
        for home in (homes[0], homes[1], homes[0]):
            home_token = set_hermes_home_override(home)
            secret_token = ss.set_secret_scope(ss.build_profile_secret_scope(home), profile_home=str(home))
            try:
                if initial_mode == "missing":
                    assert check_voice_requirements()["stt_available"]
                result = tt.transcribe_audio(str(audio), source="gateway")
                if initial_mode == "silence":
                    assert result["success"] and result["transcript"] == ""
                    assert "fallback_from" not in result
                else:
                    assert result["fallback_from"] == "local_command"
                    if initial_mode in ("cloud_error", "no_key"):
                        assert not result["success"]
                        assert "local" in result["error"].lower() and "xai" in result["error"].lower()
                    else:
                        assert result["success"] and result["provider"] == "xai"
                        assert requests[-1][0] == "/v1/stt"
                        assert requests[-1][1] == f"Bearer key-{home.name}"
                        language = b"en" if home == homes[0] else b"de"
                        assert b'name="language"\r\n\r\n' + language in requests[-1][2]
            finally:
                ss.reset_secret_scope(secret_token)
                reset_hermes_home_override(home_token)

        assert len(requests) == (0 if initial_mode in ("silence", "no_key") else 3)
        # Every new recording starts locally again, without rewriting the selected provider.
        if initial_mode != "missing":
            mode.write_text("success")
            home_token = set_hermes_home_override(homes[0])
            secret_token = ss.set_secret_scope(ss.build_profile_secret_scope(homes[0]), profile_home=str(homes[0]))
            try:
                result = tt.transcribe_audio(str(audio))
                assert result == {"success": True, "transcript": "local recovered", "provider": "local_command"}
            finally:
                ss.reset_secret_scope(secret_token)
                reset_hermes_home_override(home_token)
    finally:
        ss.set_multiplex_active(False)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
