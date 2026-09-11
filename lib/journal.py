"""Journal SQLite (WAL). Cache reconstruível — a verdade são os registros no Dropbox."""
from __future__ import annotations
import fcntl, json, os, sqlite3, uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, state TEXT, created TEXT, link TEXT, title TEXT,
  retries INTEGER DEFAULT 0, raw_path TEXT, last_error TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, kind TEXT, item_ids TEXT, started TEXT, finished TEXT, status TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS ops(op_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, item_id INTEGER, path TEXT, op TEXT,
  rev_before TEXT, rev_after TEXT, delta_sha TEXT);
CREATE TABLE IF NOT EXISTS groq(ts TEXT, seconds INTEGER);
CREATE TABLE IF NOT EXISTS requests(rid INTEGER PRIMARY KEY AUTOINCREMENT, cmd TEXT, arg TEXT, ts TEXT);
"""

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

class Journal:
    def __init__(self, db_path: Path):
        self.path = Path(db_path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL"); self.con.executescript(SCHEMA)
        self._lock_fh = None

    def acquire_lock(self) -> bool:
        fh = open(self.path.with_suffix(".lock"), "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close(); return False
        self._lock_fh = fh; fh.seek(0); fh.truncate(); fh.write(str(os.getpid())); fh.flush(); return True

    def release_lock(self) -> None:
        if self._lock_fh:
            fcntl.flock(self._lock_fh, fcntl.LOCK_UN)
            self._lock_fh.close()
            self._lock_fh = None

    def close(self) -> None:
        self.release_lock()
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def upsert_item(self, item_id: int, **cols) -> None:
        cols["updated_at"] = _now()
        cur = self.con.execute("SELECT id FROM items WHERE id=?", (item_id,)).fetchone()
        if cur is None:
            keys = ["id"] + list(cols); vals = [item_id] + list(cols.values())
            self.con.execute(f"INSERT INTO items({','.join(keys)}) VALUES({','.join('?'*len(keys))})", vals)
        else:
            sets = ", ".join(f"{k}=?" for k in cols)
            self.con.execute(f"UPDATE items SET {sets} WHERE id=?", list(cols.values()) + [item_id])

    def get_item(self, item_id: int) -> dict | None:
        r = self.con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return dict(r) if r else None

    def items_in_state(self, state: str) -> list[dict]:
        return [dict(r) for r in self.con.execute("SELECT * FROM items WHERE state=? ORDER BY created", (state,))]

    def start_run(self, kind: str, item_ids: list[int]) -> str:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.con.execute("INSERT INTO runs(run_id,kind,item_ids,started,status) VALUES(?,?,?,?,?)",
                         (run_id, kind, json.dumps(item_ids), _now(), "running"))
        return run_id

    def finish_run(self, run_id: str, status: str, summary: dict) -> None:
        self.con.execute("UPDATE runs SET finished=?, status=?, summary=? WHERE run_id=?",
                         (_now(), status, json.dumps(summary, ensure_ascii=False), run_id))

    def add_op(self, run_id, item_id, path, op, rev_before, delta_sha) -> int:
        cur = self.con.execute("INSERT INTO ops(run_id,item_id,path,op,rev_before,delta_sha) VALUES(?,?,?,?,?,?)",
                               (run_id, item_id, path, op, rev_before, delta_sha))
        return cur.lastrowid

    def confirm_op(self, op_id: int, rev_after: str) -> None:
        self.con.execute("UPDATE ops SET rev_after=? WHERE op_id=?", (rev_after, op_id))

    def ops(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.con.execute("SELECT * FROM ops WHERE run_id=? ORDER BY op_id", (run_id,))]

    def runs_today(self, kind: str, tz: str) -> int:
        day = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")
        rows = self.con.execute("SELECT started FROM runs WHERE kind=?", (kind,)).fetchall()
        return sum(1 for r in rows if datetime.fromisoformat(r["started"]).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d") == day)

    def add_groq_seconds(self, seconds: int) -> None:
        self.con.execute("INSERT INTO groq(ts,seconds) VALUES(?,?)", (_now(), int(seconds)))

    def count_groq_seconds_today(self, tz: str) -> int:
        day = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")
        return sum(r["seconds"] for r in self.con.execute("SELECT ts,seconds FROM groq")
                   if datetime.fromisoformat(r["ts"]).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d") == day)

    def enqueue_request(self, cmd: str, arg: str | None) -> None:
        self.con.execute("INSERT INTO requests(cmd,arg,ts) VALUES(?,?,?)", (cmd, arg, _now()))

    def pop_requests(self) -> list[dict]:
        self.con.execute("BEGIN IMMEDIATE")
        try:
            rows = [dict(r) for r in self.con.execute("SELECT * FROM requests ORDER BY rid")]
            self.con.execute("DELETE FROM requests")
            self.con.execute("COMMIT")
            return rows
        except Exception:
            self.con.execute("ROLLBACK")
            raise
