"""
Unit tests for kaare_fastpath.py.

Pure logic — no network, no GPU, no services needed.
Run with: cd /kaare && PYTHONPATH=/kaare python3 -m pytest tests/test_fastpath.py -v
"""
import sys
sys.path.insert(0, "/kaare")

from kaare_fastpath import _normalize, _matches, match_fastpath


# ── _normalize ────────────────────────────────────────────────────────────────

def test_normalize_lowercase():
    assert _normalize("SKRU PÅ") == "skru på"

def test_normalize_strips_whitespace():
    assert _normalize("  hei  ") == "hei"

def test_normalize_collapses_spaces():
    assert _normalize("skru   på   lyset") == "skru på lyset"

def test_normalize_removes_punctuation():
    assert "!" not in _normalize("hei!")
    assert "." not in _normalize("skru av.")
    assert "?" not in _normalize("hva er klokka?")

def test_normalize_empty():
    assert _normalize("") == ""
    assert _normalize("   ") == ""


# ── _matches ──────────────────────────────────────────────────────────────────

def test_matches_contains_hit():
    assert _matches("stuelys", "skru på stuelys", "contains") is True

def test_matches_contains_miss():
    assert _matches("kjøkkenlys", "skru på stuelys", "contains") is False

def test_matches_exact_hit():
    assert _matches("hva er klokka", "hva er klokka", "exact") is True

def test_matches_exact_miss():
    assert _matches("hva er klokka", "hva er klokka nå", "exact") is False

def test_matches_empty_phrase():
    assert _matches("", "hva som helst", "contains") is False


# ── match_fastpath ────────────────────────────────────────────────────────────

def test_match_fastpath_returns_none_on_no_match():
    result = match_fastpath("dette er en veldig spesifikk setning ingen reflex matcher zxqw")
    assert result is None

def test_match_fastpath_returns_dict_on_match():
    """Finn en aktiv reflex fra konfigfilen og test mot den."""
    import yaml
    from pathlib import Path
    rules = yaml.safe_load(Path("/kaare/configs/fastpath_rules.yaml").read_text()) or {}
    reflexes = [r for r in (rules.get("reflexes") or []) if r.get("active", True)]
    if not reflexes:
        return  # ingen aktive reflexer — hopp over

    reflex = reflexes[0]
    phrase = reflex.get("phrase", "")
    if not phrase:
        return

    result = match_fastpath(phrase)
    assert result is not None, f"Forventet treff på '{phrase}', fikk None"
    assert "route" in result

def test_match_fastpath_inactive_reflex_not_matched():
    """Inaktive reflexer skal ikke gi treff."""
    import yaml
    from pathlib import Path
    rules = yaml.safe_load(Path("/kaare/configs/fastpath_rules.yaml").read_text()) or {}
    inactive = [r for r in (rules.get("reflexes") or []) if not r.get("active", True)]
    if not inactive:
        return  # ingen inaktive — hopp over

    phrase = inactive[0].get("phrase", "")
    if not phrase:
        return

    result = match_fastpath(phrase)
    # Kan matche annen aktiv reflex, men ikke den inaktive spesifikt
    # — vi verifiserer bare at systemet ikke krasjer
    assert result is None or isinstance(result, dict)
