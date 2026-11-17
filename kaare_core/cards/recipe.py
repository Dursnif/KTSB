"""P151 phase 5: a recipe from a web page → one clean card, by code (D10).

Most recipe sites put the recipe next to the page as schema.org `Recipe` JSON-LD. Read from there, the ingredients
and steps are exact — the LLM never rewrites amounts. One renderer, two layouts: `screen` (big type for a TV, sized
by the screen) and `chat` (narrow, follows the chat's width). The card has no scripts and loads nothing but the
recipe's own picture.
"""

import html
import json
import re
from typing import Any

from kaare_core.tools.i18n import t

_LD_RE = re.compile(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
MAX_ITEMS = 80


def _walk(o: Any):
    if isinstance(o, list):
        for x in o:
            yield from _walk(x)
    elif isinstance(o, dict):
        typ = o.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        if "Recipe" in [str(x) for x in types]:
            yield o
        for v in o.values():
            if isinstance(v, (list, dict)):
                yield from _walk(v)


def find_recipe(page_html: str) -> dict | None:
    """The first schema.org Recipe in the page's JSON-LD, or None."""
    for block in _LD_RE.findall(page_html or ""):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        for r in _walk(data):
            return r
    return None


def _clean(s: Any) -> str:
    text = html.unescape(_TAG_RE.sub(" ", str(s or "")))
    return re.sub(r"\s+", " ", text).strip()


def _steps(ins: Any) -> list[str]:
    out: list[str] = []
    for s in ins if isinstance(ins, list) else [ins]:
        if isinstance(s, dict) and "HowToSection" in str(s.get("@type")):
            out += _steps(s.get("itemListElement") or [])
        elif isinstance(s, dict):
            out.append(_clean(s.get("text") or s.get("name")))
        elif isinstance(s, str):
            out += [_clean(p) for p in re.split(r"\n+", s)]
    return [x for x in out if x][:MAX_ITEMS]


def _minutes(iso: Any) -> int:
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?", str(iso or "").strip())
    return (int(m[1] or 0) * 60 + int(m[2] or 0)) if m else 0


def _image(img: Any) -> str:
    if isinstance(img, list):
        img = img[0] if img else ""
    if isinstance(img, dict):
        img = img.get("url") or ""
    img = str(img or "").strip()
    return img if img.startswith(("https://", "http://")) else ""


def normalize(r: dict, source_url: str = "") -> dict:
    yld = r.get("recipeYield")
    if isinstance(yld, list):
        yld = next((y for y in yld if str(y).strip()), "")
    return {
        "name": _clean(r.get("name")) or "?",
        "image": _image(r.get("image")),
        "minutes": _minutes(r.get("totalTime")) or (_minutes(r.get("prepTime")) + _minutes(r.get("cookTime"))),
        "yield": _clean(yld),
        "ingredients": [_clean(i) for i in (r.get("recipeIngredient") or [])][:MAX_ITEMS],
        "steps": _steps(r.get("recipeInstructions") or []),
        "source": source_url,
    }


def source_name(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/:]+)", url or "")
    return m[1] if m else ""


def as_text(rec: dict, lang: str) -> str:
    """The exact recipe for Kåre's context (he answers around it; amounts are not his to change)."""
    lines = [rec["name"]]
    meta = " · ".join(x for x in (t("card_minutes", lang, n=rec["minutes"]) if rec["minutes"] else "", rec["yield"]) if x)
    if meta:
        lines.append(meta)
    lines.append(t("card_ingredients", lang) + ":")
    lines += [f"- {i}" for i in rec["ingredients"]]
    lines.append(t("card_steps", lang) + ":")
    lines += [f"{n}. {s}" for n, s in enumerate(rec["steps"], 1)]
    if rec["source"]:
        lines.append(f"{t('card_source', lang)}: {rec['source']}")
    return "\n".join(lines)


_CSS = {
    "screen": """html,body{margin:0;background:#14110f;color:#f3ede4;font-family:Georgia,serif;height:100%;overflow:hidden}
.wrap{display:grid;grid-template-columns:34% 1fr;gap:3vw;padding:3vh 4vw;height:94vh;box-sizing:border-box}
h1{font-size:5.2vh;margin:0 0 .6vh;line-height:1.1}.meta{color:#c9a46a;font:2.4vh sans-serif;margin-bottom:2vh}
img{width:100%;height:20vh;object-fit:cover;border-radius:1vh;margin-bottom:2vh}
h2{font:600 2.6vh sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#c9a46a;margin:0 0 1.2vh}
ul,ol{margin:0;padding-left:1.9em}ul{columns:2;column-gap:2vw}li{font-size:2.55vh;line-height:1.35;margin-bottom:.8vh}
ol li{font-size:2.75vh;margin-bottom:1.6vh}ol li::marker{color:#c9a46a;font-weight:700}.col{overflow:hidden}
.src{position:fixed;right:2vw;bottom:1.2vh;font:1.8vh sans-serif;color:#7d7368}""",
    "chat": """html,body{margin:0;background:#14110f;color:#f3ede4;font-family:Georgia,serif}
.wrap{padding:16px 18px}h1{font-size:22px;margin:0 0 4px;line-height:1.2}.meta{color:#c9a46a;font:13px sans-serif;margin-bottom:12px}
img{width:100%;max-height:220px;object-fit:cover;border-radius:8px;margin-bottom:12px}
h2{font:600 13px sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#c9a46a;margin:14px 0 6px}
ul,ol{margin:0;padding-left:1.6em}li{font-size:15px;line-height:1.45;margin-bottom:4px}
@media(min-width:560px){ul{columns:2;column-gap:24px}}ol li::marker{color:#c9a46a;font-weight:700}
.src{font:12px sans-serif;color:#7d7368;margin-top:14px}""",
}


def render(rec: dict, mode: str, lang: str) -> str:
    """One self-contained HTML card. `mode`: screen | chat."""
    e = html.escape
    meta = " · ".join(x for x in (t("card_minutes", lang, n=rec["minutes"]) if rec["minutes"] else "", rec["yield"]) if x)
    img = f'<img src="{e(rec["image"])}" alt="">' if rec["image"] else ""
    ing = "".join(f"<li>{e(i)}</li>" for i in rec["ingredients"])
    st = "".join(f"<li>{e(s)}</li>" for s in rec["steps"])
    src = f'<div class="src">{e(t("card_source", lang))}: {e(source_name(rec["source"]))}</div>' if rec["source"] else ""
    head = f"<h1>{e(rec['name'])}</h1>" + (f'<div class="meta">{e(meta)}</div>' if meta else "")
    ing_block = f"<h2>{e(t('card_ingredients', lang))}</h2><ul>{ing}</ul>"
    st_block = f"<h2>{e(t('card_steps', lang))}</h2><ol>{st}</ol>"
    if mode == "screen":
        body = f'<div class="wrap"><div class="col">{img}{ing_block}</div><div class="col">{head}{st_block}</div></div>{src}'
    else:
        body = f'<div class="wrap">{img}{head}{ing_block}{st_block}{src}</div>'
    css = _CSS["screen" if mode == "screen" else "chat"]
    return (f'<!doctype html><html lang="{e(lang)}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(rec["name"])}</title>'
            f"<style>{css}</style></head><body>{body}</body></html>")
