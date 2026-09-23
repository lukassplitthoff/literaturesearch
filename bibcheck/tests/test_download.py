"""IndexClient.download and arxiv_search_title. No socket is opened: the HTTP session is a
stand-in, and the arXiv search is served from a pre-seeded cache."""

from __future__ import annotations

import re

from bibcheck import verify
from bibcheck.verify import IndexClient


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200, content_type: str = "application/pdf"):
        self.content = content
        self.status_code = status
        self.ok = status < 400
        self.headers = {"Content-Type": content_type}


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return self.response


def client_with(response, monkeypatch) -> IndexClient:
    monkeypatch.setattr(verify, "MIN_INTERVAL_S", 0.0)
    client = IndexClient()
    client._session = FakeSession(response)
    return client


def test_a_pdf_is_saved(tmp_path, monkeypatch):
    client = client_with(FakeResponse(b"%PDF-1.5 body"), monkeypatch)
    assert client.download("https://arxiv.org/pdf/1", tmp_path / "a.pdf") == ""
    assert (tmp_path / "a.pdf").read_bytes().startswith(b"%PDF")


def test_an_html_page_posing_as_a_pdf_is_refused(tmp_path, monkeypatch):
    """Two of twenty publisher links on a real run answered 200 with an HTML page."""
    client = client_with(FakeResponse(b"<!DOCTYPE html><html>", content_type="text/html; charset=utf-8"), monkeypatch)
    reason = client.download("https://journals.example/pdf/x", tmp_path / "a.pdf")
    assert reason == "not a PDF (text/html)"
    assert not (tmp_path / "a.pdf").exists()


def test_an_existing_pdf_is_not_fetched_again(tmp_path, monkeypatch):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 cached")
    client = client_with(FakeResponse(b"%PDF-new"), monkeypatch)
    assert client.download("https://arxiv.org/pdf/1", tmp_path / "a.pdf") == ""
    assert client._session.calls == 0


def test_offline_never_downloads(tmp_path):
    assert IndexClient(offline=True).download("https://arxiv.org/pdf/1", tmp_path / "a.pdf") == "offline"


def query_key(title: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title) if w.lower() not in verify._ARXIV_STOPWORDS]
    return "arxiv:query:" + ("ti:" + " AND ti:".join(words[:12])).lower()


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1511.09400v2</id>
    <title>Dynamic transition in Landau-Zener-Stuckelberg interferometry of dissipative systems:
      the case of the flux qubit</title>
    <published>2015-11-30T00:00:00Z</published>
    <author><name>Alejandro Ferron</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/9999.00001v1</id>
    <title>An unrelated paper</title>
    <published>2020-01-01T00:00:00Z</published>
  </entry>
</feed>"""


def test_arxiv_title_search_returns_the_matching_preprint():
    title = "Dynamic transition in Landau-Zener-Stuckelberg interferometry of dissipative systems: the case of the flux qubit"
    client = IndexClient(offline=True)
    client.cache[query_key(title)] = {"payload": ATOM}
    record = client.arxiv_search_title(title)
    assert record is not None and record.url.endswith("1511.09400v2")


def test_arxiv_title_search_refuses_a_poor_match():
    client = IndexClient(offline=True)
    title = "A completely different title about fluxonium"
    client.cache[query_key(title)] = {"payload": ATOM}
    assert client.arxiv_search_title(title) is None


def test_arxiv_title_search_leaves_out_stop_words(monkeypatch):
    """With "of" and "the" in the query arXiv returned nothing for an exact title; without
    them it returned the paper (checked live on arXiv:1607.04892)."""
    sent = {}

    def fake_get(self, cache_key, url, params=None, as_text=False):
        sent.update(params)
        return None

    monkeypatch.setattr(IndexClient, "_get", fake_get)
    IndexClient(offline=True).arxiv_search_title("Observation of the Photon-Blockade Breakdown Phase Transition")
    assert (
        sent["search_query"]
        == "ti:Observation AND ti:Photon AND ti:Blockade AND ti:Breakdown AND ti:Phase AND ti:Transition"
    )
