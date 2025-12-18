# db.py
# -- coding: utf-8 --

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

# Base en carpeta data/ al nivel del repo
FILE = Path(__file__).resolve()
DBPATH = FILE.resolve().parents[2] / "data" / "watcherstate.sqlite3"
DBPATH.parent.mkdir(parents=True, exist_ok=True)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DBPATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initdb() -> None:
    """Crea tablas e índices si no existen."""
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                firstseen TIMESTAMP NOT NULL,
                lastseen TIMESTAMP NOT NULL,
                UNIQUE(sha256)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fileid INTEGER,
                subject TEXT NOT NULL,
                toemail TEXT NOT NULL,
                senttime TIMESTAMP NOT NULL,
                category TEXT NOT NULL DEFAULT 'nuevo_pdf',
                FOREIGN KEY(fileid) REFERENCES files(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha256)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_file ON alerts(fileid)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(senttime)")


def upsert_file(path: Path, sha256: str) -> int:
    """Inserta/actualiza por sha256 y retorna fileid."""
    size = path.stat().st_size
    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_conn() as conn:
        try:
            conn.execute(
                """
                INSERT INTO files (path, name, size, sha256, firstseen, lastseen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(path), path.name, size, sha256, now, now),
            )
        except sqlite3.IntegrityError:
            conn.execute(
                """
                UPDATE files
                SET path = ?, name = ?, size = ?, lastseen = ?
                WHERE sha256 = ?
                """,
                (str(path), path.name, size, now, sha256),
            )
        cur = conn.execute("SELECT id FROM files WHERE sha256 = ?", (sha256,))
        return int(cur.fetchone()[0])


def alert_exists_for_file(fileid: int, category: Optional[str] = None) -> bool:
    """Indica si ya hay alerta para un fileid (y categoría opcional)."""
    with get_conn() as conn:
        if category:
            cur = conn.execute(
                "SELECT 1 FROM alerts WHERE fileid = ? AND category = ? LIMIT 1",
                (fileid, category),
            )
        else:
            cur = conn.execute(
                "SELECT 1 FROM alerts WHERE fileid = ? LIMIT 1", (fileid,)
            )
        return cur.fetchone() is not None


def add_alert(fileid: int, subject: str, toemail: str, category: str) -> None:
    """Registra una alerta enviada."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO alerts (fileid, subject, toemail, senttime, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (fileid, subject, toemail, now, category),
        )


def get_last_alerts(limit: int = 20) -> List[Dict]:
    """Devuelve últimas alertas para mostrar como notificaciones."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT senttime, subject, toemail, category
            FROM alerts
            ORDER BY senttime DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [
        {
            "senttime": r[0],
            "subject": r[1],
            "toemail": r[2],
            "category": r[3],
        }
        for r in rows
    ]
