"""维护原始数据集登记、递增短编号及目录可用状态。"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/data')
RAW = ROOT / 'raw'
DB = ROOT / 'system/platform.sqlite'


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=30000')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def raw_path(relative_path):
    relative = Path(relative_path)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('原始数据目录路径无效')
    path = RAW / relative
    if path == RAW or path.resolve() != path.absolute():
        raise ValueError('原始数据目录路径无效')
    return path


def insert_dataset(con, name, profile, relative_path=None, status='UPLOADING'):
    seq = con.execute('INSERT INTO dataset_numbers DEFAULT VALUES').lastrowid
    dataset_id = f'DS{seq:06d}'
    con.execute('INSERT INTO datasets (id,seq,name,raw_relpath,profile,status,raw_state,created_at) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (dataset_id, seq, name, relative_path or dataset_id, profile, status,
                 'UPLOADING' if status == 'UPLOADING' else 'PRESENT', now()))
    return dataset_id


def init_store():
    RAW.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, seq INTEGER UNIQUE, '
                    'name TEXT NOT NULL, raw_relpath TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, '
                    "status TEXT NOT NULL, raw_state TEXT NOT NULL DEFAULT 'MISSING', "
                    'published_path TEXT, created_at TEXT NOT NULL)')
        columns = {row['name'] for row in con.execute('PRAGMA table_info(datasets)')}
        if 'seq' not in columns:
            con.execute('ALTER TABLE datasets ADD COLUMN seq INTEGER')
        if 'raw_state' not in columns:
            con.execute("ALTER TABLE datasets ADD COLUMN raw_state TEXT NOT NULL DEFAULT 'MISSING'")
        for column, statement in {
            'description': "ALTER TABLE datasets ADD COLUMN description TEXT NOT NULL DEFAULT ''",
            'tags_json': "ALTER TABLE datasets ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'",
            'updated_at': 'ALTER TABLE datasets ADD COLUMN updated_at TEXT',
            'uploaded_at': 'ALTER TABLE datasets ADD COLUMN uploaded_at TEXT',
            'last_scanned_at': 'ALTER TABLE datasets ADD COLUMN last_scanned_at TEXT',
            'scan_error': "ALTER TABLE datasets ADD COLUMN scan_error TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in columns:
                con.execute(statement)
        con.execute('UPDATE datasets SET updated_at=created_at WHERE updated_at IS NULL')
        con.execute('CREATE TABLE IF NOT EXISTS dataset_updates ('
                    'id TEXT PRIMARY KEY, target_id TEXT NOT NULL, worker_dataset_id TEXT NOT NULL UNIQUE, '
                    'stage_relpath TEXT NOT NULL UNIQUE, run_id TEXT, status TEXT NOT NULL, '
                    'created_at TEXT NOT NULL, applied_at TEXT)')
        con.execute('CREATE TABLE IF NOT EXISTS dataset_numbers (seq INTEGER PRIMARY KEY AUTOINCREMENT)')
        con.execute('INSERT OR IGNORE INTO dataset_numbers(seq) '
                    'SELECT seq FROM datasets WHERE seq IS NOT NULL')
        for row in con.execute('SELECT id FROM datasets WHERE seq IS NULL ORDER BY created_at, rowid').fetchall():
            seq = con.execute('INSERT INTO dataset_numbers DEFAULT VALUES').lastrowid
            con.execute('UPDATE datasets SET seq=? WHERE id=?', (seq, row['id']))
    for info_path in RAW.rglob('meta/info.json'):
        directory = info_path.parent.parent
        relative = str(directory.relative_to(RAW))
        if Path(relative).parts[0].startswith('.'):
            continue
        try:
            profile = json.loads(info_path.read_text(encoding='utf-8')).get('robot_type', 'zhiyan')
        except (OSError, ValueError):
            profile = 'zhiyan'
        with db() as con:
            if not con.execute('SELECT 1 FROM datasets WHERE raw_relpath=?', (relative,)).fetchone():
                insert_dataset(con, directory.name, profile, relative, 'READY')
    reconcile_datasets()


def reconcile_datasets(dataset_id=None):
    with db() as con:
        query = 'SELECT id,status,raw_relpath,raw_state FROM datasets'
        rows = con.execute(query + (' WHERE id=?' if dataset_id else ''),
                           (dataset_id,) if dataset_id else ()).fetchall()
        for row in rows:
            if row['status'] == 'DELETED':
                state = 'DELETED'
            elif row['status'] == 'UPLOADING':
                state = 'UPLOADING'
            else:
                try:
                    state = 'PRESENT' if raw_path(row['raw_relpath']).is_dir() else 'MISSING'
                except ValueError:
                    state = 'MISSING'
            if state != row['raw_state']:
                con.execute('UPDATE datasets SET raw_state=?,updated_at=? WHERE id=?',
                            (state, now(), row['id']))


def decorate(row):
    item = dict(row)
    item['code'] = f"DS{item['seq']:06d}"
    item['available'] = item['raw_state'] == 'PRESENT'
    try:
        item['tags'] = json.loads(item.get('tags_json') or '[]')
    except ValueError:
        item['tags'] = []
    return item


def list_datasets(available_only=False, include_deleted=False):
    reconcile_datasets()
    with db() as con:
        rows = con.execute('SELECT * FROM datasets ORDER BY seq DESC').fetchall()
    items = [decorate(row) for row in rows]
    if not include_deleted:
        items = [item for item in items if item['status'] != 'DELETED']
    if available_only:
        items = [item for item in items if item['available'] and item['status'] not in {'UPLOADING', 'UPLOAD_ERROR'}]
    return items


def get_dataset(dataset_id):
    reconcile_datasets(dataset_id)
    with db() as con:
        row = con.execute('SELECT * FROM datasets WHERE id=?', (dataset_id,)).fetchone()
    return decorate(row) if row else None


def create_dataset(name, profile):
    with db() as con:
        return insert_dataset(con, name, profile)


def set_status(dataset_id, status, published_path=None):
    with db() as con:
        timestamp = now()
        con.execute('UPDATE datasets SET status=?, published_path=COALESCE(?,published_path),'
                    'updated_at=?,uploaded_at=CASE WHEN ?=\'READY\' AND status=\'UPLOADING\' '
                    'AND uploaded_at IS NULL '
                    'THEN ? ELSE uploaded_at END WHERE id=?',
                    (status, published_path, timestamp, status, timestamp, dataset_id))
    reconcile_datasets(dataset_id)
