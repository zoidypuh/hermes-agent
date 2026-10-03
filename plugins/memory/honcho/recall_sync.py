"""Bounded current-query recall; background completion never publishes recall state."""

from __future__ import annotations

import logging
import math
import threading
import time

from plugins.memory.honcho.client import spawn_context_thread

logger = logging.getLogger("plugins.memory.honcho")


def prefetch_sync(provider, query: str) -> str:
    timeout = getattr(provider._config, "timeout", None)
    budget = min(5.0, timeout) if timeout is not None and math.isfinite(timeout) and timeout > 0 else 5.0
    if provider._turn_count <= 1:
        budget = min(budget, provider._first_turn_wait(provider._FIRST_TURN_BASE_TIMEOUT))
    deadline = time.monotonic() + budget
    if provider._is_trivial_prompt(query):
        return ""
    # No lock wait can extend the request budget. Keep the slot occupied even after
    # a caller times out, until the actual worker (including any HTTP call) exits.
    if not provider._recall_sync_lock.acquire(blocking=False):
        return ""
    cancelled = threading.Event()
    try:
        worker = provider._recall_sync_thread
        if worker is not None and worker.is_alive():
            return ""
        generation = provider._recall_generation = object()
        session, turn = provider._session_key, provider._turn_count
        if not provider._session_ready():
            provider._start_session_init_background(blocking=False)
            # A stalled startup only spends a wait budget on turn one.
            if provider._turn_count > 1:
                return provider._pop_auth_notice() or provider._pop_peer_notice()
            if provider._init_thread is not None:
                provider._init_thread.join(timeout=max(0.0, deadline - time.monotonic()))
            if not provider._session_ready():
                return provider._pop_auth_notice() or provider._pop_peer_notice()
        manager = provider._manager
        if getattr(provider, "_last_recall_request", None) == (session, turn, query):
            return ""
        if (generation is not provider._recall_generation or session != provider._session_key
                or turn != provider._turn_count or time.monotonic() >= deadline):
            return ""

        # Never reuse prose across topics. Cadence can suppress optional
        # reasoning, but each eligible request needs its own scoped evidence.
        context_due = not (provider._injection_frequency == "first-turn" and turn > 1)
        if not context_due:
            return ""
        # Resolve all mutable settings before starting the worker. The captured
        # manager/session remain its only I/O owner, even if the provider is reused.
        # Automatic reasoning prose is not source-grounded. Explicit tools retain it.
        holder = {}

        def expired() -> bool:
            return (cancelled.is_set() or generation is not provider._recall_generation
                    or time.monotonic() >= deadline)

        def retrieve() -> None:
            try:
                if expired():
                    return
                from plugins.memory.honcho.evidence import PREFIX, HonchoEvidenceContext, render_packet, select_observations
                import json
                data = manager.get_grounded_context(session, query)
                if expired() or not data:
                    return
                data['observations'] = select_observations(data,
                    semantic=data.get('selection_mode') == 'semantic',
                    timeout=max(0.01, deadline - time.monotonic()))
                # Do not carry the entire candidate corpus through the core spill
                # path or logs. Retain only the evidence for the selected quotes.
                ids = {o.get('source_id') for o in data['observations'] if isinstance(o, dict)}
                data['sources'] = [s for s in data['sources'] if s['id'] in ids]
                raw = HonchoEvidenceContext(PREFIX + json.dumps(data, ensure_ascii=False))
                # Only a packet accepted by the final boundary can be published.
                holder['result'] = (raw if render_packet(raw) else '', '')
            except Exception as exc:
                logger.debug('Honcho grounded recall failed: %s', type(exc).__name__)

        worker = spawn_context_thread(retrieve, name="honcho-recall-sync", owner=provider)
        provider._recall_sync_thread = worker
        worker.start()
        worker.join(timeout=max(0.0, deadline - time.monotonic()))
        if (worker.is_alive() or time.monotonic() >= deadline or "result" not in holder
                or generation is not provider._recall_generation
                or provider._manager is not manager or provider._session_key != session
                or provider._turn_count != turn):
            return ""
        base, dialectic = holder["result"]
        provider._last_recall_request = (session, turn, query)
        if context_due:
            provider._last_context_turn = turn
        return base  # structured packets must never be truncated as prose
    finally:
        # Thread.join may return before a coarse host clock crosses the deadline.
        # Record caller abandonment explicitly before a late HTTP call can resume.
        cancelled.set()
        provider._recall_sync_lock.release()
