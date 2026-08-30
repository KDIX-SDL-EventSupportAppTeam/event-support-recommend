"""デプロイ時の条件付き登録 (docs/specs/11-deployment.md D-2..D-4, §7, §8)。

単体テストでは §7 の確認項目をすべては担保できないが、ルーティングの
条件分岐（存在させる／させない）と /ready の位置づけはここで固定できる。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from event_support_recommend.api.app import create_app
from event_support_recommend.settings import Settings


def _client(**env) -> TestClient:
    base = dict(_env_file=None, enabled_attributes=["preference_match", "rating_affinity"])
    base.update(env)
    settings = Settings(**base)
    return TestClient(create_app(settings))


# --- development（既定）: すべて登録される --------------------------------------
def test_dev_registers_demo_and_ops():
    c = _client(app_env="development", ops_token="")
    assert c.get("/demo").status_code == 200
    assert c.get("/ops/state").status_code == 200  # token 未設定は素通し（開発利便）
    assert c.get("/health").status_code == 200


# --- production: /demo は存在しない (D-3, X-2) --------------------------------
def test_production_hides_demo():
    c = _client(app_env="production", ops_token="secret")
    assert c.get("/demo").status_code == 404
    assert c.post("/demo/run", json={}).status_code == 404


# --- production + OPS_TOKEN あり: /ops/* は登録され token を要求する (D-4) -----
def test_production_with_token_protects_ops():
    c = _client(app_env="production", ops_token="secret")
    assert c.get("/ops/state").status_code == 401  # V-3
    assert c.get("/ops/state", headers={"x-ops-token": "secret"}).status_code == 200  # V-4


# --- production + OPS_TOKEN 未設定: /ops/* は存在しない (D-4, X-3) ------------
def test_production_without_token_hides_ops():
    c = _client(app_env="production", ops_token="")
    assert c.get("/ops/state").status_code == 404
    assert c.post("/ops/rebuild").status_code == 404
    # /health と /recommend/cells は無防備のまま生きている
    assert c.get("/health").status_code == 200


# --- /ready は本番でプローブに使わないことが payload から分かる (D-2, X-1) ----
def test_ready_is_marked_not_a_probe():
    c = _client(app_env="production", ops_token="secret")
    r = c.get("/ready")
    assert r.status_code == 503  # V-2: これが正常
    assert "probe" in r.json()["note"].lower()


# --- 起動ログに ops_protected / strategy / app_env が出る (D-4) ---------------
def test_startup_log_reports_protection_state(capsys):
    with _client(app_env="production", ops_token="secret"):
        pass
    lines = [l for l in capsys.readouterr().out.splitlines() if '"kind":"startup"' in l]
    assert lines, "startup ログが出ていない"
    rec = json.loads(lines[-1])
    assert rec["ops_protected"] is True
    assert rec["ops_registered"] is True
    assert rec["app_env"] == "production"
    assert rec["strategy"] == "auto"


def test_startup_log_flags_unprotected_ops(capsys):
    with _client(app_env="production", ops_token=""):
        pass
    rec = json.loads(
        [l for l in capsys.readouterr().out.splitlines() if '"kind":"startup"' in l][-1]
    )
    assert rec["ops_protected"] is False
    assert rec["ops_registered"] is False
