"""Security (BUG-log 2026-11-16, code reading): a user's pictures are theirs — the model never chooses whose folder.

`view_images` took a model-filled `user_id` and `find_image` searched every user's folder, and `/api/image/<id>` served any
picture to any logged-in user who knew its id. Now (fail closed):
- `view_images` lists and shows ONLY the logged-in user's own folders (the router's `_user_id`); a model `user_id` for
  someone else is ignored, said honestly and logged; no user → nothing; admin gets no one else's pictures through Kåre;
- `find_image(id, owner)` looks only in that owner's folders; no owner → nothing;
- `/api/image/<id>`: the owner or an admin; anyone else → 404 (no hint that it exists);
- `announce display` with another user's image_id shows no picture.
Tmp folders only. Placeholders only."""

import asyncio

import pytest
from fastapi import HTTPException

from kaare_core import image_store
from kaare_core.routers import router_media
from kaare_core.tools import definitions, executor
from kaare_core.tools.i18n import t

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def pics(tmp_path, monkeypatch):
    monkeypatch.setattr(image_store, "_IMAGES_ROOT", tmp_path / "images")
    a = image_store.save_image(PNG, "user_a", "output", ext="png")
    b = image_store.save_image(PNG, "user_b", "input", ext="png")
    return a, b


def _view(args):
    return asyncio.run(executor._dispatch("view_images", args))


def test_find_image_is_scoped_to_its_owner(pics):
    a, _b = pics
    assert image_store.find_image(a, owner="user_a") is not None
    assert image_store.find_image(a, owner="user_b") is None
    assert image_store.find_image(a, owner=None) is None  # no owner → nothing (fail closed)


def test_another_users_picture_by_id_is_refused(pics):
    a, _b = pics
    out = _view({"_user_id": "user_b", "image_id": a})
    assert a not in out.replace(t("exec_image_not_found", "nb", image_id=a), "")
    assert "/api/image/" not in out


def test_a_model_user_id_never_opens_another_folder(pics):
    a, b = pics
    out = _view({"_user_id": "user_b", "user_id": "user_a"})
    assert a not in out and b in out  # the caller's own list
    assert t("img_other_user_refused", "nb") in out


def test_no_user_sees_nothing(pics):
    a, _b = pics
    assert a not in _view({"image_id": a}) and "/api/image/" not in _view({"image_id": a})


def test_the_tool_schema_has_no_user_id():
    for tool in definitions.get_tools("nb"):
        if tool["function"]["name"] == "view_images":
            assert "user_id" not in tool["function"]["parameters"]["properties"]


def test_image_route_owner_or_admin_only(pics):
    a, _b = pics
    assert str(asyncio.run(router_media.api_serve_image(a, _u={"sub": "user_a", "role": "adult"})).path).endswith(".png")
    assert str(asyncio.run(router_media.api_serve_image(a, _u={"sub": "boss", "role": "admin"})).path).endswith(".png")
    with pytest.raises(HTTPException) as e:
        asyncio.run(router_media.api_serve_image(a, _u={"sub": "user_b", "role": "adult"}))
    assert e.value.status_code == 404
