"""Opt-in local STT recovery, with provider-specific upload preparation per attempt."""

from contextlib import ExitStack
import logging
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Any, Dict, Optional

from tools.transcription_common import CLOUD_STT_PROVIDERS

logger = logging.getLogger("tools.transcription_tools")
LOCAL_STT_PROVIDERS = frozenset({"local", "local_command"})


def local_stt_fallback_provider(stt_config: dict, provider: str) -> Optional[str]:
    """A selected local backend may recover with xAI only when explicitly opted in."""
    selected = stt_config.get("provider")
    is_local = provider in LOCAL_STT_PROVIDERS or (provider == "none" and selected in LOCAL_STT_PROVIDERS)
    return "xai" if is_local and stt_config.get("fallback_provider") == "xai" else None


def available_stt_provider(stt_config: dict, provider: str) -> str:
    """Let voice capture start when the selected local backend is missing but recovery is usable."""
    from tools import transcription_tools as tt

    if (provider == "none" and tt.is_stt_enabled(stt_config)
            and local_stt_fallback_provider(stt_config, provider)
            and tt._has_xai_stt_credentials_quietly()):
        return "xai"
    return provider


def _transcribe_provider_attempt(
    file_path: str, provider: str, stt_config: Dict[str, Any],
    model: Optional[str], source: Optional[str],
) -> Dict[str, Any]:
    # Late binding preserves the facade's provider/codec patch seams without an import cycle.
    from tools import transcription_tools as tt

    # A missing local backend has no attempt-owned conversion; the fallback prepares the original.
    if provider == "none" and local_stt_fallback_provider(stt_config, provider):
        return tt._no_provider_error(provider, stt_config)
    is_local = provider in LOCAL_STT_PROVIDERS
    error = tt._read_block_error(file_path) or tt._validate_audio_file(file_path, enforce_size_limit=not is_local)
    if error:
        return error
    with ExitStack() as cleanup:
        if not is_local and Path(file_path).suffix.lower() == ".caf":
            work_dir = cleanup.enter_context(TemporaryDirectory(prefix="hermes-caf-", ignore_cleanup_errors=True))
            file_path = tt._convert_caf_to_wav(file_path, work_dir)
            if not file_path:
                return tt._error_result("CAF audio could not be converted to WAV.")
        if provider in CLOUD_STT_PROVIDERS:
            trimmed = tt._trim_silence_for_cloud_stt(file_path, stt_config)
            if trimmed:
                file_path = trimmed
                cleanup.callback(shutil.rmtree, str(Path(trimmed).parent), ignore_errors=True)
        if not is_local:
            error = tt._validate_audio_file_size(Path(file_path))
            if error:
                return error
        return tt._dispatch_stt_provider(file_path, provider, stt_config, model, source)


def transcribe_with_local_fallback(
    file_path: str, provider: str, stt_config: Dict[str, Any],
    model: Optional[str] = None, source: Optional[str] = None,
) -> Dict[str, Any]:
    result = _transcribe_provider_attempt(file_path, provider, stt_config, model, source)
    fallback = local_stt_fallback_provider(stt_config, provider)
    if result.get("success") or result.get("no_speech") or not fallback:
        return result

    local_provider = provider if provider in LOCAL_STT_PROVIDERS else stt_config["provider"]
    logger.info("STT provider %s failed; retrying this recording with %s", local_provider, fallback)
    # A local model override must not be carried into the cloud provider's hook/model selection.
    recovered = _transcribe_provider_attempt(file_path, fallback, stt_config, None, source)
    recovered = {**recovered, "fallback_from": local_provider}
    if not recovered.get("success") and not recovered.get("no_speech"):
        recovered["error"] = (
            f"{local_provider} STT failed: {result.get('error', 'unknown error')}; "
            f"xAI STT fallback failed: {recovered.get('error', 'unknown error')}"
        )
    return recovered
