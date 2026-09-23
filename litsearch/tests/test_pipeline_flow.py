"""The whole stage sequence, run by run: screen, overview, wave 1, wave 2.

Retrieval, snowballing and the gate are replaced with fixed stand-ins, so no socket is
opened; everything from screening onwards is the real pipeline reading and writing real
files, exactly as a user's successive invocations would.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from litsearch import pipeline, screen
from litsearch.corpus import Corpus
from litsearch.gate import VERIFIED, Verdict
from litsearch.sources.base import Work

PAPERS = 25


def fixed_corpus() -> Corpus:
    corpus = Corpus()
    corpus.add_all(
        [
            Work(
                title=f"Coherence study number {i}",
                doi=f"10.1000/paper{i}",
                year=str(2010 + i % 12),
                authors=[f"Author{chr(65 + i % 26)}, Alex"],
                venue="Physical Review Test",
                abstract=f"We measure an average T1 of {100 + i} us in a tantalum transmon.",
                cited_by_count=i,
            )
            for i in range(PAPERS)
        ]
    )
    return corpus


def fake_validate(works, client):
    for work in works:
        work.validation = VERIFIED
    return list(works), [Verdict(work=work, status=VERIFIED) for work in works]


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LITSEARCH_OUT_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline.retrieve, "run", lambda fetcher, cfg: fixed_corpus())
    monkeypatch.setattr(pipeline.snowball, "expand", lambda fetcher, corpus, cfg: [])
    monkeypatch.setattr(pipeline, "validate_all", fake_validate)
    return tmp_path / "flow"


SPEC = pipeline.SearchSpec(
    name="flow",
    question="What T1 do tantalum transmons reach?",
    queries=["tantalum transmon T1"],
    inclusion_criteria="reports a measured T1",
    exclusion_criteria="theory only",
    extraction_schema=("T1_us", "material"),
    summary_focus="materials",
    offline=True,
)


def include_everything(out):
    corpus = fixed_corpus()
    lines = [
        json.dumps({"index": i, "t": screen.checksum(w.title), "verdict": "include", "role": "primary", "reason": "r"})
        for i, w in enumerate(corpus.works)
    ]
    (out / "screen").mkdir(parents=True, exist_ok=True)
    (out / "screen" / "verdicts.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def task_keys(out) -> list[str]:
    return [
        json.loads(path.read_text(encoding="utf-8"))["cite_key"]
        for path in sorted((out / "extract").glob("task_*.json"))
    ]


def packet_works(out) -> list[dict]:
    works = []
    for path in sorted((out / "overview").glob("packet_*.json")):
        works += json.loads(path.read_text(encoding="utf-8"))["works"]
    return works


def test_the_full_sequence(run_dir, capsys):
    out = run_dir

    # Run 1: nothing screened. Neither model stage may start.
    pipeline.run(SPEC)
    printed = capsys.readouterr().out
    assert "[6/8] overview" in printed and "[7/8] extract" in printed
    assert printed.count("[BLOCKED]") == 2
    assert "\nDone in " in printed, "a run says when it is over"
    assert task_keys(out) == []
    assert not list(out.glob("overview/packet_*.json"))

    # Run 2: everything screened in. Packets for all 25, tasks for wave 1 only.
    include_everything(out)
    pipeline.run(SPEC)
    printed = capsys.readouterr().out
    assert len(packet_works(out)) == PAPERS
    assert "no draft yet" in printed
    first_wave = task_keys(out)
    assert len(first_wave) == 20
    priority = (out / "priority.md").read_text(encoding="utf-8")
    assert "Read in full: 0 of 25 screened-in papers." in priority

    # Run 3: the summarizer wrote a draft that obeys the rules. It is published.
    entry = packet_works(out)[0]
    quote = entry["abstract"].split(" in a ")[0]
    draft = f'## Results\n\nEvery paper studies tantalum [@{entry["key"]}].\n\n- "{quote}" [@{entry["key"]}].\n'
    (out / "overview" / "draft.md").write_text(draft, encoding="utf-8")
    pipeline.run(SPEC)
    printed = capsys.readouterr().out
    overview_text = (out / "overview.md").read_text(encoding="utf-8")
    assert "**Built from abstracts only**" in overview_text
    assert "Not discussed above (24)" in overview_text
    assert "overview.md written" in printed

    # Run 4: wave 1 answered, one paper with nothing to report. No new tasks until the
    # user raises extraction_waves.
    rows = [
        {"cite_key": key, "T1_us": 100, "material": "tantalum", "source_quote": "T1 of 100 us"} for key in first_wave
    ]
    rows[0] = {"cite_key": first_wave[0], "T1_us": None, "material": None, "source_quote": "", "note": "none"}
    (out / "extract" / "rows.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    pipeline.run(SPEC)
    printed = capsys.readouterr().out
    assert task_keys(out) == []
    assert "set extraction_waves=2" in printed
    assert "read in full: 20 of 25" in printed
    assert "19/20 rows accepted" in printed

    # Run 5: the next wave. Only the five papers not yet read.
    pipeline.run(dataclasses.replace(SPEC, extraction_waves=2))
    capsys.readouterr()
    second_wave = task_keys(out)
    assert len(second_wave) == PAPERS - 20
    assert set(second_wave).isdisjoint(first_wave)

    # Run 6: a draft that breaks the rules takes the published overview down with it.
    (out / "overview" / "draft.md").write_text("T1 reaches 999 us everywhere.\n", encoding="utf-8")
    pipeline.run(SPEC)
    printed = capsys.readouterr().out
    assert not (out / "overview.md").exists()
    assert (out / "overview" / "problems.txt").exists()
    assert "the draft breaks" in printed


def test_every_printed_line_is_ascii(run_dir, capsys):
    """Windows consoles are cp1252; a non-ASCII print is a crash there."""
    include_everything(run_dir)
    (run_dir / "overview").mkdir(parents=True)
    greek = "".join(chr(code) for code in (0x03C7, 0x03C0, 0x03BC))
    (run_dir / "overview" / "draft.md").write_text(f"{greek} reaches 5 {greek}s.\n", encoding="utf-8")
    pipeline.run(SPEC)
    printed = capsys.readouterr().out
    assert "the draft breaks" in printed
    printed.encode("ascii")
