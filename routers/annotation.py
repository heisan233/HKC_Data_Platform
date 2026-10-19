"""First-pass annotation workspace for datasets that passed QC."""

import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import annotation_store as store
from data_catalog import dataset_root, read_json
from data_management import QC_DB, list_updates
from dataset_store import get_dataset, list_datasets

router = APIRouter()
templates = Jinja2Templates(directory='templates')
store.init_store()


class TaskCreate(BaseModel):
    dataset_id: str


class Phase(BaseModel):
    code: str
    start_frame: int
    end_frame: int


class AnnotationDraft(BaseModel):
    instruction: str = ''
    outcome: str | None = None
    phases: list[Phase] = Field(default_factory=list)
    notes: str = ''


class ReviewDecision(BaseModel):
    decision: str
    reason: str = ''


def latest_pass_run(dataset_id):
    if not QC_DB.is_file():
        return None
    try:
        with sqlite3.connect(f'file:{QC_DB}?mode=ro', uri=True) as con:
            row = con.execute("SELECT run_id,created_at FROM jobs WHERE dataset_id=? AND status='COMPLETED' "
                              "AND decision='PASS' ORDER BY created_at DESC LIMIT 1", (dataset_id,)).fetchone()
            latest = (row[0], row[1]) if row else None
            updates = [item for item in list_updates(dataset_id)
                       if item['status'] == 'APPLIED' and item['run_id']]
            if updates:
                applied = max(updates, key=lambda item: item['applied_at'] or '')
                if not latest or (applied['applied_at'] or '') > latest[1]:
                    return applied['run_id']
            return latest[0] if latest else None
    except sqlite3.Error:
        return None


def eligible_dataset(dataset_id):
    item = get_dataset(dataset_id)
    if not item or item['status'] != 'QC_PASS' or not item['available']:
        raise HTTPException(409, '仅质检通过且原始目录可用的数据集可进入标注')
    root = dataset_root(item)
    info = read_json(root / 'meta/info.json') if root else None
    if not isinstance(info, dict) or not isinstance(info.get('total_episodes'), int) or info['total_episodes'] < 1:
        raise HTTPException(422, '数据集缺少有效的 Episode 元数据')
    return item, info


def task_detail(task_id):
    task = store.task_row(task_id)
    if not task:
        raise HTTPException(404, '标注任务不存在')
    dataset = get_dataset(task['dataset_id'])
    task['dataset_name'] = dataset['name'] if dataset else task['dataset_id']
    source_key = latest_pass_run(task['dataset_id']) or f"legacy:{task['dataset_id']}"
    task['writable'] = bool(dataset and dataset['available'] and dataset['status'] == 'QC_PASS'
                            and dataset['raw_relpath'] == task['raw_relpath']
                            and source_key == task['source_key'])
    task['counts'] = store.task_counts(task_id, task['total_episodes'])
    return task


def episode_detail(task, episode_index):
    if not 0 <= episode_index < task['total_episodes']:
        raise HTTPException(404, 'Episode 不在标注任务范围内')
    if not task['writable']:
        raise HTTPException(409, '数据集当前不可标注，请先确认质检状态和原始数据')
    from routers.qc import worker
    result = worker('POST', '/inspect/episodes',
                    {'relative_path': task['raw_relpath'], 'offset': episode_index, 'limit': 1})
    items = result.get('items') or []
    if items and items[0].get('episode_index') == episode_index:
        return items[0]
    for offset in range(0, task['total_episodes'], 50):
        result = worker('POST', '/inspect/episodes',
                        {'relative_path': task['raw_relpath'], 'offset': offset, 'limit': 50})
        for item in result.get('items') or []:
            if item.get('episode_index') == episode_index:
                return item
    raise HTTPException(404, 'Episode 元数据不存在')


@router.get('/')
def annotation_page(request: Request, dataset_id: str | None = None):
    # Refresh QC decisions so direct navigation reflects a finished worker job.
    qc_notice = None
    try:
        from routers.qc import jobs
        jobs()
    except HTTPException:
        qc_notice = '质检服务暂不可用，以下显示平台已保存的最近一次质检状态。'
    datasets = []
    for item in list_datasets():
        if item['status'] != 'QC_PASS' or not item['available']:
            continue
        root = dataset_root(item)
        info = read_json(root / 'meta/info.json') if root else None
        source_key = latest_pass_run(item['id']) or f"legacy:{item['id']}"
        datasets.append({'id': item['id'], 'name': item['name'], 'profile': item['profile'],
                         'episodes': info.get('total_episodes') if isinstance(info, dict) else None,
                         'task_id': (store.task_for_dataset(item['id'], source_key) or {}).get('id')})
    tasks = [task for row in store.list_tasks() if (task := task_detail(row['id']))['writable']]
    counts = {'eligible': len(datasets), 'active': sum(t['counts']['status'] != 'COMPLETED' for t in tasks),
              'approved': sum(t['counts']['approved'] for t in tasks)}
    return templates.TemplateResponse(request=request, name='annotation.html', context={
        'datasets': datasets, 'tasks': tasks, 'counts': counts, 'selected_dataset_id': dataset_id,
        'qc_notice': qc_notice,
    })


@router.post('/api/tasks', status_code=201)
def create_task(body: TaskCreate):
    from routers.qc import jobs
    jobs()
    dataset, info = eligible_dataset(body.dataset_id)
    try:
        qc_run_id = latest_pass_run(dataset['id'])
        task = store.create_task(dataset['id'], qc_run_id or f"legacy:{dataset['id']}",
                                 dataset['raw_relpath'], info['total_episodes'], qc_run_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'id': task['id'], 'url': f"/annotation/tasks/{task['id']}"}


@router.get('/tasks/{task_id}')
def task_page(request: Request, task_id: str):
    task = task_detail(task_id)
    return templates.TemplateResponse(request=request, name='annotation_task.html', context={
        'task': task, 'phase_labels': [
            {'code': 'APPROACH', 'name': '接近'}, {'code': 'CONTACT', 'name': '接触'},
            {'code': 'MANIPULATE', 'name': '操作'}, {'code': 'RELEASE', 'name': '释放'},
            {'code': 'RETREAT', 'name': '撤离'}, {'code': 'OTHER', 'name': '其他'},
        ],
    })


@router.get('/api/tasks/{task_id}')
def task_api(task_id: str):
    return {'task': task_detail(task_id), 'items': store.list_items(task_id)}


@router.get('/api/tasks/{task_id}/items/{episode_index}')
def get_item(task_id: str, episode_index: int):
    task = task_detail(task_id)
    episode = episode_detail(task, episode_index)
    return {'item': store.item_row(task_id, episode_index), 'episode': episode}


@router.put('/api/tasks/{task_id}/items/{episode_index}')
def save_item(task_id: str, episode_index: int, body: AnnotationDraft):
    task = task_detail(task_id)
    episode = episode_detail(task, episode_index)
    try:
        return store.save_item(task_id, episode_index, body, episode.get('num_frames'))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/api/tasks/{task_id}/items/{episode_index}/submit')
def submit_item(task_id: str, episode_index: int):
    task = task_detail(task_id)
    episode_detail(task, episode_index)
    try:
        return store.submit_item(task_id, episode_index)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/api/tasks/{task_id}/items/{episode_index}/review')
def review_item(task_id: str, episode_index: int, body: ReviewDecision):
    task = task_detail(task_id)
    episode_detail(task, episode_index)
    try:
        return store.review_item(task_id, episode_index, body.decision, body.reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
