"""Persistent first-pass Episode annotation tasks and review records."""

import json
import uuid

from dataset_store import db, now

OUTCOMES = {'SUCCESS', 'FAILURE', 'UNCERTAIN'}
PHASES = {'APPROACH', 'CONTACT', 'MANIPULATE', 'RELEASE', 'RETREAT', 'OTHER'}


def init_store():
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS annotation_tasks ('
                    'id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, source_key TEXT NOT NULL, '
                    'qc_run_id TEXT, raw_relpath TEXT NOT NULL, total_episodes INTEGER NOT NULL, '
                    'created_at TEXT NOT NULL, updated_at TEXT NOT NULL, '
                    'UNIQUE(dataset_id, source_key))')
        con.execute('CREATE TABLE IF NOT EXISTS annotation_items ('
                    'task_id TEXT NOT NULL, episode_index INTEGER NOT NULL, '
                    "instruction TEXT NOT NULL DEFAULT '', outcome TEXT, "
                    "phases_json TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '', "
                    "status TEXT NOT NULL DEFAULT 'DRAFT', revision INTEGER NOT NULL DEFAULT 0, "
                    'review_reason TEXT, updated_at TEXT NOT NULL, '
                    'PRIMARY KEY (task_id, episode_index))')
        con.execute('CREATE TABLE IF NOT EXISTS annotation_events ('
                    'id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, '
                    'episode_index INTEGER NOT NULL, action TEXT NOT NULL, '
                    'revision INTEGER NOT NULL, payload_json TEXT, created_at TEXT NOT NULL)')


def task_row(task_id):
    with db() as con:
        row = con.execute('SELECT * FROM annotation_tasks WHERE id=?', (task_id,)).fetchone()
        return dict(row) if row else None


def task_for_dataset(dataset_id, source_key):
    with db() as con:
        row = con.execute('SELECT * FROM annotation_tasks WHERE dataset_id=? AND source_key=?',
                          (dataset_id, source_key)).fetchone()
        return dict(row) if row else None


def list_tasks():
    with db() as con:
        rows = con.execute('SELECT * FROM annotation_tasks ORDER BY created_at DESC').fetchall()
        return [dict(row) for row in rows]


def has_tasks(dataset_id):
    with db() as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='annotation_tasks'").fetchone():
            return False
        return con.execute('SELECT 1 FROM annotation_tasks WHERE dataset_id=? LIMIT 1',
                           (dataset_id,)).fetchone() is not None


def create_task(dataset_id, source_key, raw_relpath, total_episodes, qc_run_id=None):
    if not isinstance(total_episodes, int) or total_episodes < 1:
        raise ValueError('数据集缺少有效的 Episode 数量')
    existing = task_for_dataset(dataset_id, source_key)
    if existing:
        return existing
    task_id = uuid.uuid4().hex
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM annotation_tasks WHERE dataset_id=? AND source_key=?',
                          (dataset_id, source_key)).fetchone()
        if row:
            return dict(row)
        con.execute('INSERT INTO annotation_tasks VALUES (?,?,?,?,?,?,?,?)',
                    (task_id, dataset_id, source_key, qc_run_id, raw_relpath,
                     total_episodes, now(), now()))
    return task_row(task_id)


def item_row(task_id, episode_index):
    with db() as con:
        row = con.execute('SELECT * FROM annotation_items WHERE task_id=? AND episode_index=?',
                          (task_id, episode_index)).fetchone()
    if row is None:
        return {'task_id': task_id, 'episode_index': episode_index, 'instruction': '',
                'outcome': None, 'phases': [], 'notes': '', 'status': 'DRAFT',
                'revision': 0, 'review_reason': None, 'updated_at': None}
    item = dict(row)
    item['phases'] = json.loads(item.pop('phases_json'))
    return item


def list_items(task_id):
    with db() as con:
        rows = con.execute('SELECT episode_index,status,revision,updated_at FROM annotation_items '
                           'WHERE task_id=? ORDER BY episode_index', (task_id,)).fetchall()
    return [dict(row) for row in rows]


def task_counts(task_id, total):
    with db() as con:
        rows = con.execute('SELECT status,COUNT(*) AS count FROM annotation_items '
                           'WHERE task_id=? GROUP BY status', (task_id,)).fetchall()
    counts = {row['status']: row['count'] for row in rows}
    approved = counts.get('APPROVED', 0)
    submitted = counts.get('SUBMITTED', 0)
    rework = counts.get('REWORK', 0)
    return {'total': total, 'approved': approved, 'submitted': submitted,
            'rework': rework, 'draft': total - approved - submitted - rework,
            'status': 'COMPLETED' if approved == total else
                      'IN_REVIEW' if submitted else 'IN_PROGRESS'}


def validate_payload(body, frame_count=None):
    instruction = body.instruction.strip()
    notes = body.notes.strip()
    if len(instruction) > 500 or len(notes) > 2000:
        raise ValueError('任务描述最多 500 字，备注最多 2000 字')
    if body.outcome not in OUTCOMES | {None}:
        raise ValueError('执行结果无效')
    if len(body.phases) > 100:
        raise ValueError('动作阶段最多 100 段')
    phases = []
    for phase in body.phases:
        if phase.code not in PHASES:
            raise ValueError('动作阶段无效')
        if phase.start_frame < 0 or phase.end_frame <= phase.start_frame:
            raise ValueError('动作阶段帧范围无效')
        if frame_count is not None and phase.end_frame > frame_count:
            raise ValueError('动作阶段超出 Episode 帧数')
        phases.append(phase.model_dump())
    phases.sort(key=lambda entry: (entry['start_frame'], entry['end_frame']))
    for previous, current in zip(phases, phases[1:]):
        if current['start_frame'] < previous['end_frame']:
            raise ValueError('动作阶段不能重叠')
    return instruction, body.outcome, phases, notes


def save_item(task_id, episode_index, body, frame_count=None):
    instruction, outcome, phases, notes = validate_payload(body, frame_count)
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT status,revision FROM annotation_items WHERE task_id=? AND episode_index=?',
                          (task_id, episode_index)).fetchone()
        if row and row['status'] in {'SUBMITTED', 'APPROVED'}:
            raise ValueError('已提交或已通过的标注不能修改')
        status = 'REWORK' if row and row['status'] == 'REWORK' else 'DRAFT'
        revision = row['revision'] if row else 0
        con.execute('INSERT INTO annotation_items '
                    '(task_id,episode_index,instruction,outcome,phases_json,notes,status,revision,updated_at) '
                    'VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id,episode_index) DO UPDATE SET '
                    'instruction=excluded.instruction,outcome=excluded.outcome,phases_json=excluded.phases_json,'
                    'notes=excluded.notes,updated_at=excluded.updated_at',
                    (task_id, episode_index, instruction, outcome, json.dumps(phases), notes,
                     status, revision, now()))
        con.execute('UPDATE annotation_tasks SET updated_at=? WHERE id=?', (now(), task_id))
    return item_row(task_id, episode_index)


def submit_item(task_id, episode_index):
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM annotation_items WHERE task_id=? AND episode_index=?',
                          (task_id, episode_index)).fetchone()
        if not row or row['status'] not in {'DRAFT', 'REWORK'}:
            raise ValueError('请先保存可提交的标注草稿')
        if not row['instruction'].strip() or row['outcome'] not in OUTCOMES:
            raise ValueError('提交前须填写任务描述和执行结果')
        revision = row['revision'] + 1
        timestamp = now()
        con.execute("UPDATE annotation_items SET status='SUBMITTED',revision=?,review_reason=NULL,updated_at=? "
                    'WHERE task_id=? AND episode_index=?',
                    (revision, timestamp, task_id, episode_index))
        payload = {key: row[key] for key in ('instruction', 'outcome', 'phases_json', 'notes')}
        con.execute('INSERT INTO annotation_events '
                    '(task_id,episode_index,action,revision,payload_json,created_at) VALUES (?,?,?,?,?,?)',
                    (task_id, episode_index, 'SUBMIT', revision,
                     json.dumps(payload, ensure_ascii=False), timestamp))
        con.execute('UPDATE annotation_tasks SET updated_at=? WHERE id=?', (timestamp, task_id))
    return item_row(task_id, episode_index)


def review_item(task_id, episode_index, decision, reason=''):
    if decision not in {'APPROVE', 'REJECT'}:
        raise ValueError('审核结论无效')
    reason = reason.strip()
    if decision == 'REJECT' and not reason:
        raise ValueError('驳回时须填写原因')
    if len(reason) > 1000:
        raise ValueError('审核原因最多 1000 字')
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT status,revision FROM annotation_items '
                          'WHERE task_id=? AND episode_index=?', (task_id, episode_index)).fetchone()
        if not row or row['status'] != 'SUBMITTED':
            raise ValueError('只有待审核标注可以审核')
        status = 'APPROVED' if decision == 'APPROVE' else 'REWORK'
        timestamp = now()
        con.execute('UPDATE annotation_items SET status=?,review_reason=?,updated_at=? '
                    'WHERE task_id=? AND episode_index=?',
                    (status, reason or None, timestamp, task_id, episode_index))
        con.execute('INSERT INTO annotation_events '
                    '(task_id,episode_index,action,revision,payload_json,created_at) VALUES (?,?,?,?,?,?)',
                    (task_id, episode_index, decision, row['revision'],
                     json.dumps({'reason': reason}, ensure_ascii=False), timestamp))
        con.execute('UPDATE annotation_tasks SET updated_at=? WHERE id=?', (timestamp, task_id))
    return item_row(task_id, episode_index)
