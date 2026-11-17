"""P144-s17 (BUG-log 2026-10-30): no test may reach the live Qdrant.

P146-s01 found the nightjob fixture letting `semantic_memory._get_client()` build a client on the house's URL, and
`executor_library` builds one at import. The conftest guard turns every URL/host-based `QdrantClient` built during a
test into a loud failure, and one built at import time into a client that refuses every call (never contacts the
server). Local clients (`:memory:`, `path=`) stay allowed.
"""

import conftest
import pytest
from qdrant_client import QdrantClient


def test_a_url_client_built_in_a_test_fails_loudly():
    with pytest.raises(conftest.LiveQdrantBlocked):
        QdrantClient(url="http://127.0.0.1:6333")
    conftest.forget_blocked_qdrant()  # this test provoked it on purpose — the teardown check must not fail it


def test_a_host_client_built_in_a_test_fails_loudly():
    with pytest.raises(conftest.LiveQdrantBlocked):
        QdrantClient(host="127.0.0.1", port=6333)
    conftest.forget_blocked_qdrant()


def test_an_in_memory_client_still_works():
    client = QdrantClient(location=":memory:")
    assert client.get_collections().collections == []


def test_a_client_built_at_import_time_refuses_every_call():
    client = conftest.import_time_client("http://127.0.0.1:6333")
    with pytest.raises(conftest.LiveQdrantBlocked):
        client.get_collections()
    with pytest.raises(conftest.LiveQdrantBlocked):
        client.upsert(collection_name="x", points=[])
    conftest.forget_blocked_qdrant()


def test_the_library_module_client_is_blocked():
    """executor_library builds its client at import (line 71) — under the guard it is the refusing kind."""
    # PLC0415: imported inside the test so the import-time construction happens under the guard, as in a real run
    from kaare_core.tools import executor_library  # noqa: PLC0415

    with pytest.raises(conftest.LiveQdrantBlocked):
        executor_library._qdrant.get_collections()
    conftest.forget_blocked_qdrant()


def test_a_swallowed_attempt_still_fails_the_test(pytester_like_blocked):
    """Code that wraps Qdrant in try/except (most of KTSB) must not hide the attempt: the teardown check sees it."""
    assert pytester_like_blocked == ["http://127.0.0.1:6333"]


@pytest.fixture
def pytester_like_blocked():
    try:
        QdrantClient(url="http://127.0.0.1:6333")
    except Exception:
        pass  # what semantic_memory etc. do
    seen = conftest.blocked_qdrant()
    conftest.forget_blocked_qdrant()
    return seen
