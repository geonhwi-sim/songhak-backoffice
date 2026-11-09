"""하루 1회 동기화: 실패 처리와 원본 보관. Firebase에는 접속하지 않는다."""

from datetime import date, datetime, timedelta

from app.etl import sync


def test_one_source_failing_does_not_stop_the_other(monkeypatch):
    ran, alerts = [], []

    def fake_run(source, job):
        ran.append(source)
        if source == "rtdb":
            raise RuntimeError("Firebase 응답 없음")
        return {}

    monkeypatch.setattr(sync, "run_recorded", fake_run)
    monkeypatch.setattr(sync, "alert", alerts.append)
    assert sync.main() == 1
    assert ran == ["rtdb", "firestore"]
    assert len(alerts) == 1 and "rtdb: RuntimeError" in alerts[0] and "firestore" not in alerts[0]


def test_success_sends_no_alert(monkeypatch):
    alerts = []
    monkeypatch.setattr(sync, "run_recorded", lambda source, job: {})
    monkeypatch.setattr(sync, "alert", alerts.append)
    assert sync.main() == 0
    assert alerts == []


def test_snapshots_keep_two_weeks(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    folder = tmp_path / "snapshots"
    folder.mkdir()
    today = datetime.now(sync.KST).date()
    old = folder / f"rtdb_{today - timedelta(days=sync.KEEP_DAYS + 1)}.json"
    recent = folder / f"rtdb_{today - timedelta(days=3)}.json"
    other = folder / f"firestore_{date(2020, 1, 1)}.json"  # 다른 원본은 건드리지 않는다
    for f in (old, recent, other):
        f.write_text("{}")
    sync.keep_snapshot("rtdb", {"orders": {}})
    names = sorted(f.name for f in folder.iterdir())
    assert names == sorted([recent.name, other.name, f"rtdb_{today}.json"])


def test_missing_key_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNC_KEYS_DIR", str(tmp_path))
    try:
        sync.fetch_rtdb()
    except FileNotFoundError as e:
        assert "서비스 계정 키" in str(e)
    else:
        raise AssertionError("키가 없는데 통과함")


def test_alert_is_silent_without_url(monkeypatch):
    monkeypatch.delenv("ALERT_URL", raising=False)
    sync.alert("x")  # 주소가 없으면 아무것도 보내지 않는다
