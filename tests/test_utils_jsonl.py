import json

from ai_digest.utils import atomic_write_jsonl


def test_jsonl_escapes_unicode_line_separators_without_changing_content(tmp_path):
    path = tmp_path / "records.jsonl"
    value = {"text": "A\u2028B\u2029C ☕️🤖"}
    atomic_write_jsonl(path, [value])
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == value
    assert "\\u2028" in lines[0] and "\\u2029" in lines[0]
