import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from szperacz.config import LocalAiCfg
from szperacz.localai import LocalAI, LocalAiError


class FakeOllama(BaseHTTPRequestHandler):
    models = ["qwen3:14b", "nomic-embed-text:latest", "qwen3-vl:8b"]
    calls = []
    reject_think = False

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send({"models": [{"name": m} for m in self.models]})
        else:
            self._send({}, 404)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        FakeOllama.calls.append((self.path, data))
        if self.path == "/api/show":
            caps = ["completion", "vision"] if "vl" in data["model"] else ["completion"]
            return self._send({"capabilities": caps})
        if self.path == "/api/chat":
            if FakeOllama.reject_think and "think" in data:
                return self._send({"error": "think not supported"}, 400)
            user = data["messages"][-1]
            props = data["format"]["properties"]
            if "items" in props:                      # ocena partii
                items = []
                for n, block in re.findall(r"\[(\d+)\][^\n]*\n([^\[]*)", user["content"]):
                    if "#" in block and len(block.split()) < 6:
                        items.append({"i": int(n), "kategoria": "spam", "jezyk": "en", "tlumaczenie": ""})
                    elif "Market Square" in block:
                        items.append({"i": int(n), "kategoria": "news", "jezyk": "en",
                                      "tlumaczenie": "Rynek Główny w Krakowie: postrzelono cztery osoby."})
                    else:
                        items.append({"i": int(n), "kategoria": "wiadomosc", "jezyk": "pl", "tlumaczenie": "x"})
                out = {"items": items}
            elif "dorosli" in props:                  # zdjęcie
                assert user.get("images")
                out = {"dorosli": user["images"][0] == "TlNGVw==", "przemoc_drastyczna": False, "spam": False,
                       "opis": "Zdjęcie"}
            else:                                     # tłumaczenie
                out = {"jezyk": "en", "tlumaczenie": "Pełne tłumaczenie.", "streszczenie": "Strzelanina w Krakowie."}
            return self._send({"message": {"content": "<think>hmm</think>" + json.dumps(out)}})
        if self.path == "/api/pull":
            body = b'{"status":"pulling","completed":50,"total":100}\n{"status":"success"}\n'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send({}, 404)


@pytest.fixture
def ollama():
    FakeOllama.calls = []
    FakeOllama.reject_think = False
    srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def ai(url, tmp_path, **kw):
    return LocalAI(LocalAiCfg(ollama_url=url, lmstudio_url="http://127.0.0.1:9", **kw), tmp_path / "c.sqlite")


def test_detects_text_and_vision_models(ollama, tmp_path):
    st = ai(ollama, tmp_path).status()
    assert st["backend"] == "ollama" and st["text"] == "qwen3:14b" and st["vision"] == "qwen3-vl:8b"


def test_no_server(tmp_path):
    st = ai("http://127.0.0.1:9", tmp_path).status()
    assert st["backend"] is None and "Ollamy" in st["error"]


def test_classify_batches_and_cache(ollama, tmp_path):
    a = ai(ollama, tmp_path)
    items = [{"key": 1, "text": "#kraków #haddiyan #naporem", "chat": "Eva"},
             {"key": 2, "text": "Market Square in Kraków, Poland. At least four people were hit", "chat": "Faytuks"},
             {"key": 3, "text": "Spotkanie klubowe w czwartek o 19", "chat": "SADARS"}]
    partial = []
    r = a.classify(items, batch=2, on_partial=partial.append)
    assert r[1]["kategoria"] == "spam" and r[2]["kategoria"] == "news"
    assert r[2]["tlumaczenie"].startswith("Rynek Główny")
    assert r[3]["tlumaczenie"] == ""          # już po polsku — bez tłumaczenia
    assert len(partial) == 2                  # dwie partie
    chat_calls = [c for c in FakeOllama.calls if c[0] == "/api/chat"]
    assert chat_calls[0][1]["think"] is False and chat_calls[0][1]["format"]["required"] == ["items"]
    n = len(chat_calls)
    a.classify(items)                         # z pamięci podręcznej
    assert len([c for c in FakeOllama.calls if c[0] == "/api/chat"]) == n


def test_translate_and_model_without_think(ollama, tmp_path):
    FakeOllama.reject_think = True
    r = ai(ollama, tmp_path).translate("Market Square in Kraków…")
    assert r["tlumaczenie"] == "Pełne tłumaczenie." and r["streszczenie"].startswith("Strzelanina")


def test_check_image(ollama, tmp_path):
    a = ai(ollama, tmp_path)
    p = tmp_path / "x.jpg"
    p.write_bytes(b"NSFW")
    assert a.check_image(p)["bezpieczne"] is False
    p.write_bytes(b"ok")
    assert a.check_image(p)["bezpieczne"] is True


def test_no_vision_model(ollama, tmp_path):
    FakeOllama.models = ["qwen3:14b"]
    try:
        a = ai(ollama, tmp_path)
        assert a.status()["vision"] is None
        with pytest.raises(LocalAiError):
            a.check_image(tmp_path / "x.jpg")
    finally:
        FakeOllama.models = ["qwen3:14b", "nomic-embed-text:latest", "qwen3-vl:8b"]


def test_pull_progress(ollama, tmp_path):
    msgs = []
    ai(ollama, tmp_path).pull("qwen3-vl:8b", msgs.append)
    assert msgs[-1] == "gotowe" and any("50%" in m for m in msgs)


def test_disabled(tmp_path):
    st = LocalAI(LocalAiCfg(enabled=False), tmp_path / "c.sqlite").status()
    assert st["backend"] is None and "wyłączone" in st["error"]


def test_auto_starts_ollama_when_not_running(tmp_path, monkeypatch):
    """Ollama nie działa → Szperacz uruchamia `ollama serve` (tu: udawany serwer) i ją wykrywa."""
    import socket
    from szperacz import localai as L
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    started = {}

    def fake_popen(cmd, **kw):
        started["cmd"] = cmd
        srv = HTTPServer(("127.0.0.1", port), FakeOllama)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started["srv"] = srv
        return object()

    monkeypatch.setattr(L, "find_ollama_exe", lambda: "C:/Ollama/ollama.exe")
    monkeypatch.setattr(L.subprocess, "Popen", fake_popen)
    a = ai(f"http://127.0.0.1:{port}", tmp_path)
    st = a.status()
    assert started["cmd"] == ["C:/Ollama/ollama.exe", "serve"] and st["backend"] == "ollama"
    started["srv"].shutdown()


def test_pull_without_ollama_installed_gives_clear_error(tmp_path, monkeypatch):
    from szperacz import localai as L
    monkeypatch.setattr(L, "find_ollama_exe", lambda: None)
    with pytest.raises(LocalAiError, match="ollama.com"):
        ai("http://127.0.0.1:9", tmp_path).pull("qwen3-vl:8b")
