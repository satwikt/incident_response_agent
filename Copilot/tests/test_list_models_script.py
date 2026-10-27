import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "list_models.py"
spec = importlib.util.spec_from_file_location("list_models", SCRIPT)
lm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lm)


def test_render_highlights_nemotron_and_nvidia_ids():
    text = lm.render([{"id": "meta/llama-x"}, {"id": "nvidia/Nemotron-Y"}, {"id": "nvidia/other"}], "https://h/v1")
    assert "3 total, 2 Nemotron/NVIDIA" in text
    assert "* nvidia/Nemotron-Y" in text and "  meta/llama-x" in text


def test_fetch_models_reads_the_data_list(monkeypatch):
    import io, json
    monkeypatch.setattr(lm.urllib.request, "urlopen",
                        lambda req, timeout=0: io.BytesIO(json.dumps({"data": [{"id": "a"}, {"nope": 1}]}).encode()))
    assert lm.fetch_models("https://h/v1", "k") == [{"id": "a"}]


def test_missing_key_exits_2_and_never_prints_a_key(monkeypatch, capsys):
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    assert lm.main([]) == 2
    assert "NEBIUS_API_KEY is not set" in capsys.readouterr().err


def test_http_error_message_does_not_contain_the_key(monkeypatch, capsys):
    import urllib.error

    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
    monkeypatch.setenv("NEBIUS_API_KEY", "secret-key-123")
    monkeypatch.setattr(lm.urllib.request, "urlopen", boom)
    assert lm.main([]) == 1
    out = capsys.readouterr()
    assert "secret-key-123" not in out.out + out.err and "HTTP 401" in out.err
