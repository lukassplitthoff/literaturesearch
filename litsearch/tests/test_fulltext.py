"""Stage 6, Python side: fetching, converting, and checking quotes against the text."""

from __future__ import annotations

import pytest

from litsearch import extract, fulltext
from litsearch.extract import Column
from litsearch.fulltext import FullText
from litsearch.sources.base import Work

LONG_TEXT = "We report a Floquet-Markov simulation of the driven trans-\nmon. " + "filler " * 300


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF with one line of text, built by hand so no writer library is needed."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


class FakeClient:
    """Stands in for bibcheck's IndexClient: records requests, serves canned answers."""

    def __init__(self, pdfs: dict[str, bytes] | None = None, arxiv_match: str | None = None):
        self.pdfs = pdfs or {}
        self.arxiv_match = arxiv_match
        self.requested = []

    def arxiv_search_title(self, title):
        class Record:
            url = f"http://arxiv.org/abs/{self.arxiv_match}v2"

        return Record() if self.arxiv_match else None

    def download(self, url, path):
        self.requested.append(url)
        if url not in self.pdfs:
            return "request failed"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.pdfs[url])
        return ""


# --------------------------------------------------------------------------- quotes


def test_a_quote_survives_line_breaks_hyphenation_and_case():
    text = "We report a Floquet-Markov simu-\nlation of the driven\n  TRANSMON."
    assert fulltext.quote_found("a Floquet-Markov simulation of the driven transmon", text)
    assert fulltext.quote_found("a FloquetMarkov simu- lation", text)


def test_a_changed_number_or_word_is_not_found():
    text = "The critical photon number is 105 for the ground state."
    assert not fulltext.quote_found("The critical photon number is 150 for the ground state.", text)
    assert not fulltext.quote_found("The threshold photon number is 105", text)


def test_ligatures_are_read_as_letters():
    assert fulltext.quote_found("the first effect", "the \ufb01rst e\ufb00ect")


def test_an_empty_quote_is_never_found():
    assert not fulltext.quote_found("   ", "anything")


# --------------------------------------------------------------------------- conversion


def test_pypdf_is_used_when_pdftotext_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(fulltext.shutil, "which", lambda name: None)
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(minimal_pdf("Quasienergy spectrum of a driven transmon"))
    assert "Quasienergy spectrum of a driven transmon" in fulltext.pdf_to_text(pdf)


def test_pdftotext_runs_in_reading_order_with_utf8(tmp_path, monkeypatch):
    """-layout interleaved two columns and kept 9 of 89 real quotes; reading order kept 65."""
    seen = {}

    class Done:
        returncode = 0
        stdout = "chi / 2\u03c0 = 2.3 MHz".encode("utf-8")

    def fake_run(args, **kwargs):
        seen["args"] = args
        return Done()

    monkeypatch.setattr(fulltext.shutil, "which", lambda name: "pdftotext")
    monkeypatch.setattr(fulltext.subprocess, "run", fake_run)
    assert fulltext.pdf_to_text(tmp_path / "p.pdf") == "chi / 2\u03c0 = 2.3 MHz"
    assert "-enc" in seen["args"] and "UTF-8" in seen["args"] and "-layout" not in seen["args"]


# --------------------------------------------------------------------------- fetching


@pytest.fixture
def plain_text(monkeypatch):
    monkeypatch.setattr(fulltext, "pdf_to_text", lambda path: LONG_TEXT)


def test_arxiv_is_tried_first(tmp_path, plain_text):
    client = FakeClient(pdfs={"https://arxiv.org/pdf/2402.06615": b"%PDF"})
    work = Work(title="T", arxiv_id="2402.06615", oa_pdf_url="https://publisher.example/x.pdf")
    got = fulltext.fetch_text(client, work, "Dumas2024", tmp_path)
    assert (got.source, got.text_path) == ("arxiv", "text/Dumas2024.txt")
    assert client.requested == ["https://arxiv.org/pdf/2402.06615"]
    assert (tmp_path / "text" / "Dumas2024.txt").read_text(encoding="utf-8") == LONG_TEXT


def test_a_journal_record_without_an_arxiv_id_is_found_by_title(tmp_path, plain_text):
    client = FakeClient(pdfs={"https://arxiv.org/pdf/1511.09400": b"%PDF"}, arxiv_match="1511.09400")
    got = fulltext.fetch_text(client, Work(title="Dynamic transition in LZS"), "Ferron2016", tmp_path)
    assert got.source == "arxiv-by-title"


def test_the_open_access_link_is_the_fallback(tmp_path, plain_text):
    client = FakeClient(pdfs={"https://oa.example/p.pdf": b"%PDF"})
    work = Work(title="T", arxiv_id="2101.00001", oa_pdf_url="https://oa.example/p.pdf")
    got = fulltext.fetch_text(client, work, "K", tmp_path)
    assert got.source == "open-access"
    assert client.requested == ["https://arxiv.org/pdf/2101.00001", "https://oa.example/p.pdf"]


def test_a_malformed_arxiv_id_is_ignored_and_the_title_searched(tmp_path, plain_text):
    """A real record carried "2026.10043" -- month 26 -- and got no fallback."""
    client = FakeClient(pdfs={"https://arxiv.org/pdf/2510.12345": b"%PDF"}, arxiv_match="2510.12345")
    got = fulltext.fetch_text(client, Work(title="Frozonium", arxiv_id="2026.10043"), "K", tmp_path)
    assert got.source == "arxiv-by-title"
    assert client.requested == ["https://arxiv.org/pdf/2510.12345"]


def test_a_failed_arxiv_download_falls_back_to_the_title_search(tmp_path, plain_text):
    client = FakeClient(pdfs={"https://arxiv.org/pdf/2510.12345": b"%PDF"}, arxiv_match="2510.12345")
    got = fulltext.fetch_text(client, Work(title="T", arxiv_id="2501.00001"), "K", tmp_path)
    assert got.source == "arxiv-by-title"


def test_valid_arxiv_ids():
    assert fulltext.valid_arxiv_id("2402.06615") == "2402.06615"
    assert fulltext.valid_arxiv_id("http://arxiv.org/abs/1511.09400v2") == "1511.09400"
    assert fulltext.valid_arxiv_id("2026.10043") is None
    assert fulltext.valid_arxiv_id(None) is None


def test_no_text_is_a_note_not_an_error(tmp_path, plain_text):
    got = fulltext.fetch_text(FakeClient(), Work(title="Paywalled", oa_pdf_url="https://x/y.pdf"), "K", tmp_path)
    assert got.text_path == "" and "request failed" in got.note


def test_an_image_only_pdf_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(fulltext, "pdf_to_text", lambda path: "Figure 1")
    client = FakeClient(pdfs={"https://arxiv.org/pdf/2101.00001": b"%PDF"})
    got = fulltext.fetch_text(client, Work(title="T", arxiv_id="2101.00001"), "K", tmp_path)
    assert got.text_path == "" and "too little text" in got.note


def test_existing_text_is_reused_without_a_request(tmp_path, plain_text):
    (tmp_path / "text").mkdir()
    (tmp_path / "text" / "K.txt").write_text(LONG_TEXT, encoding="utf-8")
    client = FakeClient()
    assert fulltext.fetch_text(client, Work(title="T", arxiv_id="2101.00001"), "K", tmp_path).text_path
    assert client.requested == []


# --------------------------------------------------------------------------- checking rows


SCHEMA = (
    Column("critical_photon_number", "number", "photon number where ionization sets in"),
    Column("compared_to_experiment", "choice", "", ("yes", "qualitative", "no")),
    "method",
)


def row(**fields):
    base = {"cite_key": "K", "confidence": "full_text", "source_quote": "Ionization sets in at 105 photons."}
    base.update(fields)
    return base


def test_a_quote_found_in_the_text_is_accepted():
    accepted, complaints = extract.validate_rows(
        [row(critical_photon_number=105)], SCHEMA, texts={"K": "... Ionization sets\nin at 105 pho-\ntons. ..."}
    )
    assert len(accepted) == 1 and complaints == []


def test_an_edited_quote_is_dropped():
    accepted, complaints = extract.validate_rows(
        [row(critical_photon_number=105)], SCHEMA, texts={"K": "Ionization sets in at 150 photons."}
    )
    assert accepted == [] and "quote not found" in complaints[0]


def test_an_abstract_only_row_is_checked_against_the_abstract():
    accepted, _ = extract.validate_rows(
        [row(confidence="abstract_only", critical_photon_number=105)],
        SCHEMA,
        texts={"K": "unrelated"},
        abstracts={"K": "Ionization sets in at 105 photons."},
    )
    assert len(accepted) == 1


def test_rows_from_before_python_fetching_are_kept_but_flagged():
    accepted, complaints = extract.validate_rows([row(critical_photon_number=105)], SCHEMA, texts={"Other": "x"})
    assert len(accepted) == 1 and "unverifiable" in complaints[0]


def test_a_word_in_a_number_column_is_nulled():
    accepted, complaints = extract.validate_rows(
        [row(critical_photon_number="about a hundred")], SCHEMA, texts={"K": "Ionization sets in at 105 photons."}
    )
    assert accepted[0]["critical_photon_number"] is None and "is not a number" in complaints[0]


def test_an_unlisted_choice_is_nulled_and_listed_ones_pass_in_any_case():
    accepted, complaints = extract.validate_rows(
        [row(compared_to_experiment="partly: parameters only"), row(compared_to_experiment="Yes")],
        SCHEMA,
        texts={"K": "Ionization sets in at 105 photons."},
    )
    assert accepted[0]["compared_to_experiment"] is None and "is not one of yes, qualitative, no" in complaints[0]
    assert accepted[1]["compared_to_experiment"] == "Yes"


def test_the_task_carries_types_definitions_and_choices(tmp_path):
    texts = {"K": FullText("K", "text/K.txt", "arxiv")}
    path = extract.prepare_tasks([Work(title="T")], tmp_path, schema=SCHEMA, cite_keys={0: "K"}, texts=texts)[0]
    import json

    columns = json.loads(path.read_text(encoding="utf-8"))["columns"]
    assert columns[0] == {
        "name": "critical_photon_number",
        "type": "number",
        "definition": "photon number where ionization sets in",
    }
    assert columns[1]["choices"] == ["yes", "qualitative", "no"]
    assert columns[2] == {"name": "method", "type": "text", "definition": ""}
