"""段3-a: 読み取り専用プロキシからのスナップショット取得。

docs/specs/runtime-phase-switching/10-testing.md T-1〜T-10。
実データ（鍵・URL）は使わず、プロキシをモックして検証する（T-50 と同じ流儀）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from event_support_recommend.data.live_tables import LIVE_TABLES, build_select
from event_support_recommend.data.proxy import ProxyError, ReadonlyProxyClient
from event_support_recommend.data.refresher import SnapshotRefresher
from event_support_recommend.data.repository import (
    ProxySnapshotRepository,
    UnavailableRepository,
    build_repository,
)
from event_support_recommend.settings import Settings


# --------------------------------------------------------------------------- #
# フェイクのプロキシクライアント
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, *, configured=True, rows_by_table=None, raises=None):
        self.configured = configured
        self._rows = rows_by_table or {}
        self._raises = raises
        self.calls: list[tuple[str, str, list]] = []

    def select(self, table, sql, params):
        self.calls.append((table, sql, params))
        if self._raises is not None:
            raise self._raises
        return self._rows.get(table, [])


def _settings(**over) -> Settings:
    return Settings(_env_file=None, enabled_attributes=["preference_match", "rating_affinity"], **over)


# --------------------------------------------------------------------------- #
# T-1: URL 未設定なら取得を試みない
# --------------------------------------------------------------------------- #
def test_t1_no_url_returns_unavailable_without_calling():
    assert isinstance(build_repository(_settings()), UnavailableRepository)

    client = FakeClient(configured=False)
    repo = ProxySnapshotRepository(client, default_event_id="ev1")
    snap = repo.fetch()
    assert snap.built is False
    assert snap.decision_table_size is None
    assert client.calls == []


def test_t1_none_client_returns_unavailable():
    assert ProxySnapshotRepository(None, default_event_id="ev1").fetch().built is False


# --------------------------------------------------------------------------- #
# T-2 / T-3: 500・タイムアウトでも例外を投げない
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("err", [ProxyError("proxy request failed for table 'check_ins'")])
def test_t2_t3_proxy_failure_does_not_raise(err):
    repo = ProxySnapshotRepository(FakeClient(raises=err), default_event_id="ev1")
    snap = repo.fetch()  # 例外が出ないこと
    assert snap.built is False
    assert snap.decision_table_size is None


def test_t3_timeout_is_wrapped_not_raised(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    client = ReadonlyProxyClient("https://proxy.example/ro", "secret-key", timeout_sec=1)
    with pytest.raises(ProxyError) as ei:
        client.select("check_ins", "SELECT `id` FROM `check_ins`", [])
    # T-5: SQL 本文・鍵・URL が載らない
    msg = str(ei.value)
    assert "check_ins" in msg
    assert "secret-key" not in msg and "proxy.example" not in msg and "SELECT" not in msg


# --------------------------------------------------------------------------- #
# T-4: rows が無い / JSON でない
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", [b"not json", b"{}", b'{"rows": "nope"}', b"[]"])
def test_t4_broken_response_raises_proxyerror(monkeypatch, raw):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return raw

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
    client = ReadonlyProxyClient("https://p/ro", "k", timeout_sec=1)
    with pytest.raises(ProxyError):
        client.select("booths", "SELECT `id` FROM `booths`", [])


def test_t4_repository_absorbs_broken_response():
    repo = ProxySnapshotRepository(
        FakeClient(raises=ProxyError("proxy returned non-JSON for table 'booths'")),
        default_event_id="ev1",
    )
    assert repo.fetch().built is False


# --------------------------------------------------------------------------- #
# T-6: SELECT で始まらない SQL は送信前に拒否
# --------------------------------------------------------------------------- #
def test_t6_non_select_rejected_before_send(monkeypatch):
    called = False

    def spy(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("should not send")

    monkeypatch.setattr("urllib.request.urlopen", spy)
    client = ReadonlyProxyClient("https://p/ro", "k", timeout_sec=1)
    for bad in ["DROP TABLE users", "DELETE FROM booths", "  UPDATE x SET y=1"]:
        with pytest.raises(ProxyError):
            client.select("check_ins", bad, [])
    assert called is False


# --------------------------------------------------------------------------- #
# T-7: LIVE_TABLES に無いテーブルは拒否
# --------------------------------------------------------------------------- #
def test_t7_unknown_table_rejected():
    with pytest.raises(ProxyError):
        build_select("secret_stuff", None)
    with pytest.raises(ProxyError):
        build_select("users; DROP TABLE users", "ev1")


# --------------------------------------------------------------------------- #
# T-8: users から email / password_hash は構造的に要求できない
# --------------------------------------------------------------------------- #
def test_t8_users_columns_cannot_include_secrets():
    assert set(LIVE_TABLES["users"]) == {"id", "role"}
    assert "email" not in LIVE_TABLES["users"]
    assert "password_hash" not in LIVE_TABLES["users"]
    sql, params = build_select("users", "ev1")
    assert "email" not in sql and "password_hash" not in sql
    # users はイベントで絞らない（出展者・運営が混ざるため）
    assert params == [] and "WHERE" not in sql


def test_event_scoping_written_once_in_data_layer():
    sql, params = build_select("check_ins", "ev-42")
    assert "WHERE `event_id` = ?" in sql and params == ["ev-42"]
    sql2, params2 = build_select("categories", "ev-42")
    assert params2 == ["ev-42"]


# --------------------------------------------------------------------------- #
# T-9: バックグラウンドタスクは起動直後に1回、以後 TTL ごと
# --------------------------------------------------------------------------- #
def test_t9_refresher_runs_once_immediately():
    class CountingRepo:
        def __init__(self):
            self.n = 0

        def fetch(self, event_id=None):
            self.n += 1
            from event_support_recommend.models import EventSnapshot

            return EventSnapshot.unavailable()

    repo = CountingRepo()

    async def main():
        r = SnapshotRefresher(repo, interval_sec=3600)
        r.start()
        await asyncio.sleep(0.1)  # 起動直後の1回が走る時間
        await r.stop()

    asyncio.run(main())
    assert repo.n == 1  # 即時1回。TTL(3600s)は待つのでまだ2回目は来ない


def test_t9_run_once_invokes_build_callback():
    from event_support_recommend.models import EventSnapshot

    seen: list = []
    repo = UnavailableRepository()
    r = SnapshotRefresher(repo, interval_sec=1, on_snapshot=seen.append)
    snap = asyncio.run(r.run_once())
    assert isinstance(snap, EventSnapshot)
    assert len(seen) == 1


def test_t9_build_failure_keeps_previous_cache(capsys):
    def boom(_s):
        raise RuntimeError("build blew up")

    r = SnapshotRefresher(UnavailableRepository(), interval_sec=1, on_snapshot=boom)
    asyncio.run(r.run_once())  # 例外が外に漏れない
    lines = [l for l in capsys.readouterr().out.splitlines() if '"kind":"snapshot"' in l]
    assert any(json.loads(l).get("ok") is False for l in lines)


# --------------------------------------------------------------------------- #
# T-10: 取得中でも /recommend/cells は COVERAGE で 200
# --------------------------------------------------------------------------- #
def test_t10_recommend_stays_200_without_proxy(client):
    body = {
        "user_id": "u1",
        "cell_count": 2,
        "candidate_booths": [
            {"booth_id": "b1", "category_id": "c1", "visitor_count": 3},
            {"booth_id": "b2", "category_id": "c2", "visitor_count": 9},
        ],
        "pre_survey": {"interest_categories": ["c1"]},
    }
    r = client.post("/recommend/cells", json=body)
    assert r.status_code == 200
    assert r.json()["phase"] == "COVERAGE"


def test_t10_recommend_ok_while_refresher_fails():
    from fastapi.testclient import TestClient

    from event_support_recommend.api.app import create_app

    s = _settings(
        readonly_proxy_url="https://proxy.invalid/ro",
        readonly_proxy_key="k",
        snapshot_event_id="ev1",
        snapshot_ttl_sec=3600,
    )
    with TestClient(create_app(s)) as c:
        r = c.post(
            "/recommend/cells",
            json={
                "user_id": "u1",
                "cell_count": 2,
                "candidate_booths": [{"booth_id": "b1", "category_id": "c1", "visitor_count": 1}],
            },
        )
        assert r.status_code == 200
        assert r.json()["phase"] == "COVERAGE"


# --------------------------------------------------------------------------- #
# 正常系: すべてのテーブルを取得できたら built=True
# --------------------------------------------------------------------------- #
def test_happy_path_builds_snapshot_with_all_tables():
    rows = {
        "users": [{"id": "u1", "role": "participant"}, {"id": "u2", "role": "exhibitor"}],
        "booths": [{"id": "b1", "event_id": "ev1", "category_id": "c1", "is_active": 1}],
        "check_ins": [],
        "booth_ratings": [],
        "user_survey_answers": [],
        "categories": [{"id": "c1", "event_id": "ev1", "name": "AI"}],
    }
    repo = ProxySnapshotRepository(FakeClient(rows_by_table=rows), default_event_id="ev1")
    snap = repo.fetch()
    assert snap.built is True
    assert snap.participants == 1  # exhibitor は数えない
    assert snap.booths == 1
    assert set(snap.tables) == set(LIVE_TABLES)
    assert snap.decision_table_size is None  # 決定表の組み立ては段3-b
