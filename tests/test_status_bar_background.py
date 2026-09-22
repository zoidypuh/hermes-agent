"""Real HTTP regression: a stalled metrics server must not stall CLI repaint/input."""

import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cli import HermesCLI
from agent import ai_usage, gpu, usage_api


@pytest.mark.parametrize("metric", ["gpu", "ai_usage"])
def test_status_repaint_does_not_wait_for_metrics_http(monkeypatch, metric):
    entered = threading.Event()
    release = threading.Event()
    requests = []
    payload = {"name": "gpu", "online": True, "gpuUsedGb": 4.0, "gpuTotalGb": 32.0}
    if metric == "ai_usage":
        payload = {"chatgpt": {"online": True, "remaining": 75},
                   "grok": {"online": True, "remaining": 51},
                   "openrouter": {"online": True, "remainingCredits": 8.49}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            entered.set()
            release.wait(10)
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    monkeypatch.setenv("USAGE_API_URL", f"http://127.0.0.1:{server.server_port}")
    usage_api.clear_cache()
    gpu.clear_cache()
    ai_usage.clear_cache()
    obj = HermesCLI.__new__(HermesCLI)
    obj.model = "test-model"
    obj.agent = None
    obj.session_start = datetime.now()
    obj.conversation_history = []
    obj._gpu_visible = metric == "gpu"
    obj._ai_usage_visible = metric == "ai_usage"
    result = []
    done = threading.Event()

    def repaint():
        try:
            result.append(obj._get_status_bar_snapshot())
        finally:
            done.set()

    repaint_thread = threading.Thread(target=repaint, daemon=True)
    try:
        repaint_thread.start()
        assert entered.wait(3), "test server did not receive the metrics request"
        assert done.wait(2), "status repaint blocked on the metrics HTTP request"
        field = "gpu_label" if metric == "gpu" else "chatgpt_label"
        assert result[0][field] == ""
        for _ in range(20):
            assert obj._get_status_bar_snapshot()[field] == ""
        assert len(requests) == 1, "repaints started overlapping refreshes"
        release.set()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            snapshot = obj._get_status_bar_snapshot()
            if snapshot[field]:
                break
            time.sleep(0.01)
        assert snapshot[field] == ("4.0/32.0G" if metric == "gpu" else "75%")
        if metric == "ai_usage":
            assert snapshot["grok_label"] == "51%"
            assert snapshot["openrouter_label"] == "8,49$"

        # Expire both layers, then hold the next HTTP response. The previous
        # reading must remain visible while the refresh is pending.
        entered.clear()
        release.clear()
        usage_api.clear_cache()
        gpu.clear_cache()
        ai_usage.clear_cache()
        stamp, reading, pending = obj._status_bar_metric_cache[metric]
        obj._status_bar_metric_cache[metric] = (stamp - 1000, reading, pending)
        assert obj._get_status_bar_snapshot()[field] == snapshot[field]
        assert entered.wait(3)
        for _ in range(20):
            assert obj._get_status_bar_snapshot()[field] == snapshot[field]
        assert len(requests) == 2
    finally:
        release.set()
        repaint_thread.join(5)
        server.shutdown()
        server.server_close()
        server_thread.join(3)
        # Let the daemon reader settle before restoring its environment/cache.
        deadline = time.monotonic() + 4
        while any(entry[2] for entry in getattr(obj, "_status_bar_metric_cache", {}).values()):
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        usage_api.clear_cache()
        gpu.clear_cache()
        ai_usage.clear_cache()
