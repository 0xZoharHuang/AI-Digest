import httpx
import pytest

from ai_digest.link_identity import resolve_one


@pytest.mark.asyncio
@pytest.mark.parametrize("destination,status", [
    ("https://arxiv.org/abs/2609.04661", "resolved"),
    ("http://127.0.0.1/secret", "unsafe_url"),
    ("http://localhost/secret", "unsafe_url"),
    ("file:///secret", "unsafe_url"),
    ("https://ift.tt/abc", "redirect_loop"),
])
async def test_only_shortener_is_contacted(destination, status):
    requests = []
    def respond(request):
        requests.append(request)
        assert request.method == "HEAD"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(302, headers={"Location": destination})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await resolve_one("https://ift.tt/abc", client)
    assert result["status"] == status
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_resolution_is_cached_bounded_and_preserves_originals(monkeypatch, tmp_path):
    from copy import deepcopy

    from ai_digest.link_identity import resolve_documents
    docs = {str(i): {"observations": [{"payload": {"expanded_links": [f"https://ift.tt/{i}"]}}]} for i in range(3)}
    original = deepcopy(docs)
    calls = []
    async def resolve(url, client):
        calls.append(url)
        return {"status": "resolved", "url": "https://arxiv.org/abs/2609.04661"}
    monkeypatch.setattr("ai_digest.link_identity.resolve_one", resolve)
    first = await resolve_documents(docs, tmp_path, limit=2)
    assert len(calls) == 2 and docs == original
    assert first["2"]["resolved_links"] == []
    second = await resolve_documents(docs, tmp_path, limit=2)
    assert len(calls) == 2
    assert second == first
