"""하루 1회 동기화: Firebase에서 내려받기 → Postgres로 가져오기(급여 다시 계산 포함) → 실패하면 알림.

운영 서버에서 cron이 실행한다 (docs/deploy.md):
  docker compose -f docker-compose.prod.yml exec -T api python -m app.etl.sync

- 서비스 계정은 읽기 전용 역할만 가진다 (정산: Realtime Database 뷰어, 발주: Cloud Datastore 뷰어).
  키 파일은 서버의 secrets/ 폴더에만 있고 저장소에는 없다.
- 두 원본을 따로 가져온다. 하나가 실패해도 다른 하나는 진행하고, 실패는 sync_runs에 남는다.
- 내려받은 원본은 DATA_DIR/snapshots/에 날짜별로 남긴다(14일치). DB를 잃어도 다시 가져올 수 있게.
"""

import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

from app.etl import firestore, rtdb
from app.etl.common import run_recorded

KST = timezone(timedelta(hours=9))
RTDB_URL = "https://songhak-payment-default-rtdb.firebaseio.com/songhak_data.json"
FIRESTORE = "https://firestore.googleapis.com/v1/projects/songhak-inventory/databases/(default)/documents"
KEEP_DAYS = 14


def keys_dir() -> Path:
    return Path(os.environ.get("SYNC_KEYS_DIR", "/secrets"))


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data"))


def authed(key_file: str, scopes: list[str]) -> AuthorizedSession:
    path = keys_dir() / key_file
    if not path.exists():
        raise FileNotFoundError(f"서비스 계정 키가 없습니다: {path}")
    return AuthorizedSession(service_account.Credentials.from_service_account_file(str(path), scopes=scopes))


def fetch_rtdb() -> dict:
    s = authed("songhak-payment-sync.json", ["https://www.googleapis.com/auth/firebase.database",
                                              "https://www.googleapis.com/auth/userinfo.email"])
    r = s.get(RTDB_URL, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or "orders" not in data:
        raise ValueError("정산 앱 데이터 형식이 예상과 다릅니다 (orders 없음)")
    return data


def fetch_firestore() -> dict[str, dict]:
    """컬렉션별 Firestore REST 응답 {"documents": [...]}."""
    s = authed("songhak-inventory-sync.json", ["https://www.googleapis.com/auth/datastore"])
    out = {}
    for col in firestore.COLLECTIONS:
        docs, page = [], ""
        while True:
            r = s.get(f"{FIRESTORE}/{col}", params={"pageSize": 300, **({"pageToken": page} if page else {})}, timeout=60)
            r.raise_for_status()
            j = r.json()
            docs += j.get("documents", [])
            page = j.get("nextPageToken", "")
            if not page:
                break
        out[col] = {"documents": docs}
    return out


def keep_snapshot(name: str, data) -> None:
    """원본을 남기고 오래된 것은 지운다. 저장에 실패해도 동기화는 계속한다."""
    try:
        folder = data_dir() / "snapshots"
        folder.mkdir(parents=True, exist_ok=True)
        day = datetime.now(KST).date()
        (folder / f"{name}_{day}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        for f in folder.glob(f"{name}_*.json"):
            stamp = f.stem.rsplit("_", 1)[-1]
            if stamp < (day - timedelta(days=KEEP_DAYS)).isoformat():
                f.unlink()
    except OSError as e:
        print(f"원본 저장 실패 (계속 진행): {e}", file=sys.stderr)


def sync_rtdb(session) -> dict:
    data = fetch_rtdb()
    keep_snapshot("rtdb", data)
    history_file = data_dir() / "price_history.json"
    history = json.loads(history_file.read_text(encoding="utf-8")) if history_file.exists() else {}
    return rtdb.import_snapshot(session, data, history)


def sync_firestore(session) -> dict:
    raw = fetch_firestore()
    keep_snapshot("firestore", raw)
    return firestore.import_export(session, {col: firestore.documents(v) for col, v in raw.items()})


def alert(text: str) -> None:
    """ALERT_URL(예: ntfy 주제 주소)이 있으면 실패를 알린다."""
    url = os.environ.get("ALERT_URL")
    if not url:
        return
    try:
        requests.post(url, data=text.encode("utf-8"), headers={"Title": "songhak backoffice sync failed"}, timeout=10)
    except requests.RequestException as e:
        print(f"알림 전송 실패: {e}", file=sys.stderr)


def main() -> int:
    failed = []
    for source, job in (("rtdb", sync_rtdb), ("firestore", sync_firestore)):
        try:
            run_recorded(source, job)
        except Exception as e:
            traceback.print_exc()
            failed.append(f"{source}: {type(e).__name__}: {str(e)[:200]}")
    if failed:
        alert("백오피스 동기화 실패\n" + "\n".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
