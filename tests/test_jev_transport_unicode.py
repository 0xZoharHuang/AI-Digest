import json

from ai_digest.jev_probe import request_fits


def test_persistent_worker_request_uses_ascii_safe_json_contract():
    request = {"state": {"text": "A\u2028B\u2029C ☕️🤖"}, "questions": {"q": {"type": "boolean"}}}
    payload = json.dumps(request, ensure_ascii=True, sort_keys=True)
    assert "\\u2028" in payload and "\\u2029" in payload
    assert request_fits(request, persistent=True)
