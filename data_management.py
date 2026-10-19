"""Dataset metadata, replacement candidates and safe record cleanup."""
import json
import os
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path

from dataset_store import DB, RAW, ROOT, db, get_dataset, list_datasets, now, raw_path, reconcile_datasets

QC_DB = ROOT / 'system/qc_jobs.sqlite'
QC_REPORTS = ROOT / 'outputs/qc'
MUTATION_LOCK = threading.RLock()


def update_display(dataset_id, name, description, tags):
    name = name.strip()
    description = description.strip()
    clean_tags = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
    if not 1 <= len(name) <= 100:
        raise ValueError('名称须为 1–100 个字符')
    if len(description) > 500 or len(clean_tags) > 10 or any(len(tag) > 32 for tag in clean_tags):
        raise ValueError('备注最多 500 字；标签最多 10 个、每个最多 32 字')
    with db() as con:
        row = con.execute('SELECT status FROM datasets WHERE id=?', (dataset_id,)).fetchone()
        if not row or row['status'] == 'DELETED':
            raise LookupError('数据集不存在或已删除')
        con.execute('UPDATE datasets SET name=?, description=?, tags_json=?, updated_at=? WHERE id=?',
                    (name, description, json.dumps(clean_tags, ensure_ascii=False), now(), dataset_id))
    return get_dataset(dataset_id)


def scan_dataset(dataset_id):
    item = get_dataset(dataset_id)
    if not item or item['status'] == 'DELETED':
        raise LookupError('数据集不存在或已删除')
    error = ''
    try:
        root = raw_path(item['raw_relpath'])
        if not root.is_dir():
            error = '数据目录不存在'
        else:
            info = json.loads((root / 'meta/info.json').read_text(encoding='utf-8'))
            if not isinstance(info, dict) or 'total_episodes' not in info:
                error = 'meta/info.json 缺少必要元数据'
    except (OSError, ValueError, UnicodeError) as exc:
        error = f'元数据读取失败：{exc}'
    with db() as con:
        con.execute('UPDATE datasets SET last_scanned_at=?, scan_error=? WHERE id=?',
                    (now(), error, dataset_id))
    reconcile_datasets(dataset_id)
    return get_dataset(dataset_id)


def list_updates(target_id=None):
    with db() as con:
        rows = con.execute('SELECT * FROM dataset_updates' +
                           (' WHERE target_id=?' if target_id else '') +
                           ' ORDER BY created_at DESC', (target_id,) if target_id else ()).fetchall()
    return [dict(row) for row in rows]


def get_update(update_id):
    with db() as con:
        row = con.execute('SELECT * FROM dataset_updates WHERE id=?', (update_id,)).fetchone()
    return dict(row) if row else None


def update_target_for_worker(worker_dataset_id):
    with db() as con:
        row = con.execute('SELECT target_id,status FROM dataset_updates WHERE worker_dataset_id=?',
                          (worker_dataset_id,)).fetchone()
    return dict(row) if row else None


def record_exists(dataset_id):
    with db() as con:
        return con.execute('SELECT 1 FROM datasets WHERE id=?', (dataset_id,)).fetchone() is not None


def stage_update(target_id, update_id):
    from annotation_store import has_tasks
    if has_tasks(target_id):
        raise ValueError('数据集已有标注任务，暂不能替换原始数据')
    item = get_dataset(target_id)
    if not item or item['status'] not in {'QC_PASS', 'PUBLISHED'} or not item['available']:
        raise ValueError('仅可更新质检通过且目录存在的 Raw 数据集')
    if any(row['status'] not in {'APPLIED', 'CANCELLED'} for row in list_updates(target_id)):
        raise ValueError('已有待处理的数据更新，请先应用或取消')
    relative = f'.updates/{target_id}/{update_id}'
    worker_id = f'UPD_{update_id}'
    with db() as con:
        con.execute('INSERT INTO dataset_updates '
                    '(id,target_id,worker_dataset_id,stage_relpath,status,created_at) '
                    'VALUES (?,?,?,?,?,?)',
                    (update_id, target_id, worker_id, relative, 'STAGED', now()))
    return {'id': update_id, 'worker_dataset_id': worker_id, 'stage_relpath': relative}


def set_update_run(update_id, run_id):
    with db() as con:
        con.execute("UPDATE dataset_updates SET run_id=?,status='QC_QUEUED' WHERE id=? AND status='STAGED'",
                    (run_id, update_id))


def cancel_update(update_id):
    with MUTATION_LOCK:
        item = get_update(update_id)
        if not item or item['status'] in {'APPLIED', 'CANCELLED'}:
            raise ValueError('更新候选不存在或已处理')
        stage = raw_path(item['stage_relpath'])
        if stage.is_dir():
            shutil.rmtree(stage)
        with db() as con:
            con.execute("UPDATE dataset_updates SET status='CANCELLED' WHERE id=?", (update_id,))


def apply_update(update_id):
    with MUTATION_LOCK:
        update = get_update(update_id)
        if not update or update['status'] != 'QC_QUEUED':
            raise ValueError('更新候选不存在或状态不允许应用')
        item = get_dataset(update['target_id'])
        if not item or item['status'] in {'DELETED', 'UPLOADING', 'UPLOAD_ERROR'} or not item['available']:
            raise ValueError('原数据集已不可用')
        current = raw_path(item['raw_relpath'])
        stage = raw_path(update['stage_relpath'])
        if not stage.is_dir():
            raise ValueError('更新候选目录缺失')
        backup = ROOT / 'upload_tmp/update_backups' / update_id
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            raise ValueError('更新备份路径已存在，请先检查上次操作')
        with db() as con:
            con.execute("UPDATE dataset_updates SET status='SWAPPING' WHERE id=?", (update_id,))
        try:
            os.replace(current, backup)
            os.replace(stage, current)
            with db() as con:
                con.execute("UPDATE datasets SET status='QC_PASS',raw_state='PRESENT',updated_at=?,last_scanned_at=NULL,scan_error='' WHERE id=?",
                            (now(), item['id']))
                con.execute("UPDATE dataset_updates SET status='APPLIED',applied_at=? WHERE id=?",
                            (now(), update_id))
        except Exception:
            if current.exists() and not stage.exists():
                os.replace(current, stage)
            if backup.exists():
                os.replace(backup, current)
            with db() as con:
                con.execute("UPDATE dataset_updates SET status='QC_QUEUED' WHERE id=?", (update_id,))
            raise
        shutil.rmtree(backup, ignore_errors=True)
        return get_dataset(item['id'])


def recover_interrupted_updates():
    """Restore the previous dataset when a process stopped during a swap."""
    with MUTATION_LOCK:
        for update in list_updates():
            backup = ROOT / 'upload_tmp/update_backups' / update['id']
            if update['status'] == 'APPLIED':
                if backup.is_dir():
                    shutil.rmtree(backup, ignore_errors=True)
                continue
            if update['status'] != 'SWAPPING':
                continue
            item = get_dataset(update['target_id'])
            if item is None:
                continue
            try:
                current = raw_path(item['raw_relpath'])
                stage = raw_path(update['stage_relpath'])
                if backup.is_dir():
                    if current.is_dir() and not stage.exists():
                        stage.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(current, stage)
                    if not current.exists():
                        os.replace(backup, current)
                if current.is_dir() and stage.is_dir():
                    with db() as con:
                        con.execute("UPDATE dataset_updates SET status='QC_QUEUED' WHERE id=?", (update['id'],))
                    reconcile_datasets(item['id'])
            except (OSError, ValueError):
                continue


def record_overview():
    items = list_datasets(include_deleted=True)
    updates = list_updates()
    by_target = {}
    worker_ids = {}
    for update in updates:
        by_target.setdefault(update['target_id'], []).append(update)
        worker_ids[update['worker_dataset_id']] = update['target_id']
    counts = {}
    if QC_DB.is_file():
        try:
            with sqlite3.connect(f'file:{QC_DB}?mode=ro', uri=True) as con:
                for dataset_id, count in con.execute('SELECT dataset_id,COUNT(*) FROM jobs GROUP BY dataset_id'):
                    key = worker_ids.get(dataset_id, dataset_id)
                    counts[key] = counts.get(key, 0) + count
        except sqlite3.Error:
            pass
    for item in items:
        item['job_count'] = counts.get(item['id'], 0)
        ids = [item['id']] + [row['worker_dataset_id'] for row in by_target.get(item['id'], [])]
        item['report_count'] = sum(sum(1 for path in (QC_REPORTS / key).rglob('*') if path.is_file())
                                   for key in ids if (QC_REPORTS / key).is_dir())
        item['update_count'] = len(by_target.get(item['id'], []))
        item['purge_allowed'] = purge_allowed(item)
    return items


def purge_allowed(item):
    if item['status'] != 'DELETED' and item['raw_state'] != 'MISSING':
        return False
    try:
        path = raw_path(item['raw_relpath'])
        return not path.exists() and not path.is_symlink()
    except ValueError:
        return False


def purge_record(dataset_id, purge_reports=False):
    """Remove a missing/deleted registration and its QC jobs; keep ID numbers."""
    with MUTATION_LOCK:
        item = get_dataset(dataset_id)
        if not item:
            raise LookupError('登记记录不存在')
        from annotation_store import has_tasks
        if has_tasks(dataset_id):
            raise ValueError('数据集已有标注任务，不能清理登记记录')
        if not purge_allowed(item):
            raise ValueError('仅可清理实际目录不存在的缺失或已删除记录')
        updates = list_updates(dataset_id)
        if any(row['status'] not in {'APPLIED', 'CANCELLED'} for row in updates):
            raise ValueError('存在未结束的数据更新，请先处理')
        ids = [dataset_id] + [row['worker_dataset_id'] for row in updates]
        if not QC_DB.is_file():
            raise ValueError('质检任务数据库不存在，无法核对关联记录')
        stash = ROOT / 'upload_tmp/purge_reports' / uuid.uuid4().hex
        moved = []
        try:
            with db() as con:
                con.execute('ATTACH DATABASE ? AS qcdb', (str(QC_DB),))
                con.execute('BEGIN IMMEDIATE')
                placeholders = ','.join('?' for _ in ids)
                active = con.execute(f"SELECT COUNT(*) FROM qcdb.jobs WHERE dataset_id IN ({placeholders}) AND status IN ('QUEUED','RUNNING')", ids).fetchone()[0]
                if active:
                    raise ValueError('关联质检任务仍在运行，无法清理记录')
                if purge_reports:
                    stash.mkdir(parents=True, exist_ok=True)
                    for key in ids:
                        source = QC_REPORTS / key
                        if source.is_dir() and not source.is_symlink():
                            target = stash / key
                            os.replace(source, target)
                            moved.append((target, source))
                con.execute(f'DELETE FROM qcdb.jobs WHERE dataset_id IN ({placeholders})', ids)
                con.execute('DELETE FROM dataset_updates WHERE target_id=?', (dataset_id,))
                con.execute('DELETE FROM datasets WHERE id=?', (dataset_id,))
        except Exception:
            for source, target in reversed(moved):
                os.replace(source, target)
            raise
        if purge_reports:
            shutil.rmtree(stash, ignore_errors=True)
        return {'deleted': True, 'reports_deleted': purge_reports, 'code': item['code']}
