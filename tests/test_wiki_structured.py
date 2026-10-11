"""Tests for kaare_core/tools/wiki_structured.py (P111 Phase 4) against a tiny temp DB with
the P110 `wiki_collections` schema — no dependency on a real wiki database."""

import json
import sqlite3

import pytest

from kaare_core.tools import wiki_structured as S

_SCHEMA = """CREATE TABLE wiki_collections(
  article_id INTEGER NOT NULL, ordinal INTEGER NOT NULL, article_title TEXT, lang TEXT, rev_id INTEGER,
  kind TEXT NOT NULL, section_path TEXT, caption TEXT, columns TEXT, rows TEXT, sort_values TEXT,
  row_flags TEXT, n_rows INTEGER, n_cols INTEGER, header_rows INTEGER, header_source TEXT,
  css_classes TEXT, template TEXT, confidence REAL, in_infobox INTEGER, reason TEXT, typed TEXT,
  PRIMARY KEY(article_id, ordinal))"""


def _add(c, aid, ordinal, kind, section, caption, columns, rows):
    c.execute(
        "INSERT INTO wiki_collections(article_id, ordinal, article_title, lang, kind, section_path, "
        "caption, columns, rows, n_rows, n_cols) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            aid,
            ordinal,
            "Testartikkel",
            "no",
            kind,
            json.dumps(section),
            caption,
            json.dumps(columns),
            json.dumps(rows),
            len(rows),
            len(columns),
        ),
    )


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "wiki_test.db")
    c = sqlite3.connect(path)
    c.execute(_SCHEMA)
    _add(c, 1, 1, "data", ["Karriere"], "Ungdomsklubb", ["År", "Klubb"], [["2007–2009", "Inter"]])
    _add(
        c,
        1,
        2,
        "data",
        ["Karriere"],
        "Klubber",
        ["År", "Klubb", "Kamper"],
        [["2009–2010", "Inter", "5"], ["2010–2014", "Bologna", ""]],
    )
    _add(
        c,
        1,
        3,
        "succession",
        ["Eksterne lenker"],
        "",
        ["col_1", "col_2"],
        [["Forgjenger A", "Konge 715"]],
    )
    _add(
        c,
        1,
        10001,
        "list_data",
        ["Diskografi"],
        "",
        ["level", "text", "links"],
        [[0, "Album én", []], [1, "Spor to", []]],
    )
    _add(c, 1, 10002, "list_terms", ["Resultater"], "", ["term", "definition"], [["2001", "Oslo"]])
    _add(
        c,
        1,
        10003,
        "list_links",
        ["Deltakerland"],
        "",
        ["level", "text", "links"],
        [[0, "Norge (80)", []]],
    )
    _add(
        c,
        1,
        10004,
        "list_links",
        ["Eksterne lenker"],
        "",
        ["level", "text", "links"],
        [[0, "Offisiell side", []]],
    )
    _add(
        c, 1, 10005, "references", ["Referanser"], "", ["level", "text", "links"], [[0, "Ref", []]]
    )
    _add(
        c,
        1,
        10006,
        "list_other",
        ["Diverse"],
        "",
        ["level", "text", "links"],
        [[0, "Står i prosaen", []]],
    )
    c.commit()
    c.close()
    return path


def test_parse_markers_no_and_en_dedup():
    text = "a [se liste: Klubber #2] b [see list: Squad #7] c [se liste: Klubber #2]"
    assert S.parse_markers(text) == [("Klubber", 2), ("Squad", 7)]


def test_strip_markers_keeps_label():
    assert S.strip_markers("Rekorder [se liste: Rekorder #1] slutt") == "Rekorder [Rekorder] slutt"


def test_fetch_keeps_content_skips_noise(db):
    rows = S.fetch(db, 1)
    assert sorted(rows) == [
        1,
        2,
        3,
        10001,
        10002,
        10003,
    ]  # no ext. links list, references, list_other


def test_fetch_only_requested_ordinals(db):
    assert sorted(S.fetch(db, 1, [2, 10005])) == [2]


def test_every_row_carries_its_table_name(db):
    rows = S.fetch(db, 1)
    youth = S.render_lines(rows[1], S.label_of(rows[1]))
    senior = S.render_lines(rows[2], S.label_of(rows[2]))
    assert youth == ["Ungdomsklubb — År: 2007–2009 | Klubb: Inter"]
    assert senior == [
        "Klubber — År: 2009–2010 | Klubb: Inter | Kamper: 5",
        "Klubber — År: 2010–2014 | Klubb: Bologna",
    ]  # empty cell left out


def test_generic_columns_have_no_names(db):
    row = S.fetch(db, 1, [3])[3]
    assert S.render_lines(row, "Arvefølge") == ["Arvefølge — Forgjenger A | Konge 715"]


def test_lists_levels_and_terms(db):
    rows = S.fetch(db, 1)
    assert S.render_lines(rows[10001], "Diskografi") == [
        "Diskografi — Album én",
        "Diskografi —   Spor to",
    ]
    assert S.render_lines(rows[10002], "Resultater") == ["Resultater — 2001: Oslo"]


def test_label_prefers_marker_then_caption_then_section(db):
    rows = S.fetch(db, 1)
    assert S.label_of(rows[2], "Fra markør") == "Fra markør"
    assert S.label_of(rows[2]) == "Klubber"
    assert S.label_of(rows[10001]) == "Diskografi"
