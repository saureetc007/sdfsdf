"""SQLite persistence + search. FTS5 when available, LIKE fallback otherwise."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone

from .extractor import dedupe_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (id INTEGER PRIMARY KEY, full_name TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, repo_id INTEGER REFERENCES repositories(id), source_key TEXT UNIQUE NOT NULL,
  source_type TEXT NOT NULL, number INTEGER, url TEXT, author TEXT, created_at_src TEXT, title TEXT,
  text TEXT NOT NULL, text_sha256 TEXT NOT NULL, fetched_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS extraction_runs (
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE, model TEXT,
  started_at TEXT NOT NULL, status TEXT NOT NULL, error TEXT, raw_response TEXT);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  run_id INTEGER REFERENCES extraction_runs(id), decision TEXT NOT NULL, status TEXT NOT NULL,
  reasons TEXT NOT NULL, unresolved TEXT NOT NULL, conflicts TEXT NOT NULL, evidence_state TEXT NOT NULL,
  flags TEXT NOT NULL, dedupe_hash TEXT NOT NULL, UNIQUE(source_id, dedupe_hash));
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY, decision_id INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  excerpt TEXT NOT NULL, state TEXT NOT NULL, start_off INTEGER, end_off INTEGER);
CREATE TABLE IF NOT EXISTS alternatives (
  id INTEGER PRIMARY KEY, decision_id INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  name TEXT NOT NULL, rejected INTEGER NOT NULL, rejection_reason TEXT);
CREATE TABLE IF NOT EXISTS validation_outcomes (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES extraction_runs(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, detail TEXT NOT NULL, decision TEXT);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tokens(q):
    return re.findall(r"[A-Za-z0-9_+#.-]+", q)


class Database:
    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        with self.conn:
            self.conn.executescript(SCHEMA)
        try:
            with self.conn:
                self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS decisions_fts USING fts5(decision, reasons, alternatives)")
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False

    def close(self):
        self.conn.close()

    def upsert_repository(self, full_name):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO repositories(full_name, created_at) VALUES (?,?)", (full_name, _now()))
        return self.conn.execute("SELECT id FROM repositories WHERE full_name=?", (full_name,)).fetchone()["id"]

    def upsert_source(self, doc, repo_id=None):
        """Returns (source_id, 'new'|'unchanged'|'updated'). Changed text invalidates old decisions (offsets would be stale)."""
        sha = hashlib.sha256(doc.text.encode()).hexdigest()
        row = self.conn.execute("SELECT id, text_sha256 FROM sources WHERE source_key=?", (doc.source_key,)).fetchone()
        with self.conn:
            if row is None:
                cur = self.conn.execute(
                    "INSERT INTO sources(repo_id,source_key,source_type,number,url,author,created_at_src,title,text,text_sha256,fetched_at,is_demo)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (repo_id, doc.source_key, doc.source_type, doc.number, doc.url, doc.author, doc.created_at, doc.title, doc.text, sha, _now(), int(doc.is_demo)),
                )
                return cur.lastrowid, "new"
            if row["text_sha256"] == sha:
                return row["id"], "unchanged"
            self._delete_decisions(row["id"])
            self.conn.execute("DELETE FROM extraction_runs WHERE source_id=?", (row["id"],))
            self.conn.execute("UPDATE sources SET text=?, text_sha256=?, title=?, fetched_at=? WHERE id=?", (doc.text, sha, doc.title, _now(), row["id"]))
            return row["id"], "updated"

    def _delete_decisions(self, source_id):
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM decisions WHERE source_id=?", (source_id,))]
        if self.fts:
            for i in ids:
                self.conn.execute("DELETE FROM decisions_fts WHERE rowid=?", (i,))
        self.conn.execute("DELETE FROM decisions WHERE source_id=?", (source_id,))

    def has_successful_run(self, source_id):
        return self.conn.execute("SELECT 1 FROM extraction_runs WHERE source_id=? AND status='ok'", (source_id,)).fetchone() is not None

    def record_failed_run(self, source_id, model, error):
        with self.conn:
            self.conn.execute("INSERT INTO extraction_runs(source_id,model,started_at,status,error) VALUES (?,?,?,?,?)", (source_id, model, _now(), "failed", error[:2000]))

    def save_extraction(self, source_id, result):
        """Atomically persist one extraction result. Returns number of newly inserted decisions."""
        inserted = 0
        with self.conn:
            run_id = self.conn.execute(
                "INSERT INTO extraction_runs(source_id,model,started_at,status,raw_response) VALUES (?,?,?,?,?)",
                (source_id, result.model, _now(), "ok", json.dumps(result.raw_responses)),
            ).lastrowid
            for n in result.notes:
                self.conn.execute("INSERT INTO validation_outcomes(run_id,kind,detail,decision) VALUES (?,?,?,?)", (run_id, n.kind, n.detail, n.decision))
            for d in result.decisions:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO decisions(source_id,run_id,decision,status,reasons,unresolved,conflicts,evidence_state,flags,dedupe_hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (source_id, run_id, d.decision, d.status, json.dumps(d.reasons), json.dumps(d.unresolved_questions),
                     json.dumps(d.conflicting_positions), d.evidence_state, json.dumps(d.flags), dedupe_hash(d.decision)),
                )
                if cur.rowcount == 0:
                    continue
                did = cur.lastrowid
                inserted += 1
                for c in d.checks:
                    self.conn.execute("INSERT INTO evidence(decision_id,excerpt,state,start_off,end_off) VALUES (?,?,?,?,?)", (did, c.excerpt, c.state, c.start, c.end))
                for a in d.alternatives:
                    self.conn.execute("INSERT INTO alternatives(decision_id,name,rejected,rejection_reason) VALUES (?,?,?,?)", (did, a.name, int(a.rejected), a.rejection_reason))
                if self.fts:
                    self.conn.execute(
                        "INSERT INTO decisions_fts(rowid,decision,reasons,alternatives) VALUES (?,?,?,?)",
                        (did, d.decision, " ".join(d.reasons), " ".join(a.name for a in d.alternatives)),
                    )
        return inserted

    def _hydrate(self, row):
        d = dict(row)
        for k in ("reasons", "unresolved", "conflicts", "flags"):
            d[k] = json.loads(d[k])
        d["evidence"] = [dict(r) for r in self.conn.execute("SELECT excerpt,state,start_off,end_off FROM evidence WHERE decision_id=?", (d["id"],))]
        d["alternatives"] = [dict(r) for r in self.conn.execute("SELECT name,rejected,rejection_reason FROM alternatives WHERE decision_id=?", (d["id"],))]
        return d

    _SELECT = (
        "SELECT d.id,d.decision,d.status,d.reasons,d.unresolved,d.conflicts,d.evidence_state,d.flags,d.source_id,"
        " s.source_key,s.source_type,s.url,s.number,s.author,s.is_demo,r.full_name AS repo"
        " FROM decisions d JOIN sources s ON s.id=d.source_id LEFT JOIN repositories r ON r.id=s.repo_id"
    )

    def get_decision(self, decision_id):
        row = self.conn.execute(self._SELECT + " WHERE d.id=?", (decision_id,)).fetchone()
        return self._hydrate(row) if row else None

    def get_source_text(self, source_id):
        r = self.conn.execute("SELECT text FROM sources WHERE id=?", (source_id,)).fetchone()
        return r["text"] if r else None

    def validation_outcomes(self, source_id):
        q = "SELECT v.kind,v.detail,v.decision FROM validation_outcomes v JOIN extraction_runs e ON e.id=v.run_id WHERE e.source_id=?"
        return [dict(r) for r in self.conn.execute(q, (source_id,))]

    def search(self, query="", repo=None, status=None, limit=20, verified_only=False):
        where, params = [], []
        toks = _tokens(query)
        if toks and self.fts:
            match = " ".join('"' + t.replace('"', "") + '"*' for t in toks)
            sql = self._SELECT + " JOIN decisions_fts f ON f.rowid=d.id"
            where.append("decisions_fts MATCH ?")
            params.append(match)
            order = " ORDER BY f.rank"
        else:
            sql, order = self._SELECT, " ORDER BY d.id DESC"
            for t in toks:  # LIKE fallback (also covers FTS-unavailable builds)
                where.append("(d.decision LIKE ? OR d.reasons LIKE ? OR EXISTS(SELECT 1 FROM alternatives a WHERE a.decision_id=d.id AND a.name LIKE ?))")
                params += [f"%{t}%"] * 3
        if repo:
            where.append("r.full_name=?")
            params.append(repo)
        if status:
            where.append("d.status=?")
            params.append(status)
        if verified_only:
            where.append("d.evidence_state IN ('excerpt_verified','partially_verified')")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += order + " LIMIT ?"
        params.append(int(limit))
        return [self._hydrate(r) for r in self.conn.execute(sql, params)]
