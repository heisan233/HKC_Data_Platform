"""Browse and manage the platform's data assets and stored reports."""
import json
import os
import shutil
import sqlite3
import uuid
import zipfile

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from data_catalog import (
    RAW, category_entries, category_root, category_summary, dataset_detail,
    dataset_summary, readable_file, report_detail, report_summary,
)
from data_management import (
    MUTATION_LOCK, apply_update, cancel_update, get_update, list_updates,
    purge_record, record_overview, recover_interrupted_updates, scan_dataset, set_update_run, stage_update,
    update_display,
)
from dataset_store import ROOT, get_dataset, init_store, list_datasets

router = APIRouter()
templates = Jinja2Templates(directory='templates')
init_store()
recover_interrupted_updates()


def page(request, name, **context):
    return templates.TemplateResponse(request=request, name=name, context=context)


def raw_items():
    return [dataset_summary(item) for item in list_datasets()]


def accepted(item):
    return item['raw_state'] == 'PRESENT' and item['status'] in {'QC_PASS', 'PUBLISHED'}


class MetadataChange(BaseModel):
    name: str
    description: str = ''
    tags: list[str] = []


class PurgeRequest(BaseModel):
    confirmed: bool = False
    purge_reports: bool = False


@router.get('/')
def data_page(request: Request):
    items = raw_items()
    categories = category_summary()
    records = record_overview()
    return page(request, 'data.html', raw_count=sum(accepted(item) for item in items),
                other_count=sum(not accepted(item) for item in items),
                report_count=sum(item['count'] for item in categories),
                categories=categories, recent_raw=[item for item in items if accepted(item)][:4],
                record_count=len(records), purge_count=sum(item['purge_allowed'] for item in records))


@router.get('/manage/')
def manage_page(request: Request, q: str = '', section: str = 'raw', deleted: str = ''):
    items = [dataset_summary(item) for item in record_overview() if item['status'] != 'DELETED']
    query = q.strip().casefold()
    if query:
        items = [item for item in items if query in ' '.join(
            str(item.get(key, '')) for key in ('code', 'name', 'profile', 'status')).casefold()]
    deleted_item = get_dataset(deleted) if deleted else None
    deleted_message = (f"{deleted_item['code']} 的 Raw 数据已删除，登记记录仍保留。"
                       if deleted_item and deleted_item['status'] == 'DELETED' else None)
    return page(request, 'data_manage.html', items=items, query=q.strip(),
                deleted_message=deleted_message,
                section='datasets' if section == 'datasets' else 'raw')


@router.get('/manage/raw/{dataset_id}')
def manage_detail(request: Request, dataset_id: str):
    item = get_dataset(dataset_id)
    if not item or item['status'] == 'DELETED':
        raise HTTPException(404, '数据集不存在')
    updates = list_updates(dataset_id)
    from routers.qc import worker
    for update in updates:
        update['job'] = None
        if update['run_id']:
            try:
                update['job'] = worker('GET', f"/jobs/{update['run_id']}")
            except HTTPException:
                pass
    return page(request, 'data_manage_detail.html', item=dataset_summary(item), updates=updates)


@router.post('/manage/raw/{dataset_id}/metadata')
def change_metadata(dataset_id: str, body: MetadataChange):
    try:
        return update_display(dataset_id, body.name, body.description, body.tags)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/manage/raw/{dataset_id}/scan')
def rescan(dataset_id: str):
    try:
        return scan_dataset(dataset_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/manage/raw/{dataset_id}/updates', status_code=202)
async def upload_update(dataset_id: str, file: UploadFile = File(...)):
    from routers.qc import unpack_dataset, worker
    item = get_dataset(dataset_id)
    if not item or item['status'] not in {'QC_PASS', 'PUBLISHED'} or not item['available']:
        raise HTTPException(409, '仅可更新质检通过且目录存在的数据集')
    if not file.filename or not file.filename.lower().endswith('.zip'):
        raise HTTPException(400, '请选择 ZIP 数据集压缩包')
    update_id = uuid.uuid4().hex
    incoming = ROOT / 'upload_tmp'
    incoming.mkdir(parents=True, exist_ok=True)
    archive = incoming / f'update-{update_id}.zip'
    unpacked = incoming / f'update-{update_id}.part'
    stage = RAW / '.updates' / dataset_id / update_id
    created = False
    try:
        with archive.open('wb') as output:
            while block := await file.read(8 * 1024 * 1024):
                output.write(block)
        unpacked.mkdir()
        dataset_root = unpack_dataset(archive, unpacked)
        info = json.loads((dataset_root / 'meta/info.json').read_text(encoding='utf-8'))
        if info.get('robot_type') != item['profile']:
            raise ValueError('更新包的机器人类型与原数据集不一致')
        with MUTATION_LOCK:
            stage.parent.mkdir(parents=True, exist_ok=True)
            os.replace(dataset_root, stage)
            candidate = stage_update(dataset_id, update_id)
            created = True
        job = worker('POST', '/jobs', {'dataset_id': candidate['worker_dataset_id'],
                                       'relative_path': candidate['stage_relpath'],
                                       'profile': item['profile']})
        set_update_run(update_id, job['run_id'])
        return {'update_id': update_id, 'run_id': job['run_id'], 'status': 'QC_QUEUED'}
    except (OSError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        if created:
            cancel_update(update_id)
        elif stage.is_dir():
            shutil.rmtree(stage)
        raise HTTPException(400, f'更新包处理失败：{exc}') from exc
    except HTTPException:
        if created:
            cancel_update(update_id)
        raise
    finally:
        await file.close()
        archive.unlink(missing_ok=True)
        shutil.rmtree(unpacked, ignore_errors=True)


@router.post('/manage/raw/{dataset_id}/updates/{update_id}/apply')
def publish_update(dataset_id: str, update_id: str):
    from routers.qc import worker
    with MUTATION_LOCK:
        update = get_update(update_id)
        if not update or update['target_id'] != dataset_id or not update['run_id']:
            raise HTTPException(404, '更新候选不存在')
        job = worker('GET', f"/jobs/{update['run_id']}")
        if job['status'] != 'COMPLETED' or job['decision'] != 'PASS' or job['report_deleted']:
            raise HTTPException(409, '更新候选尚未通过质检或报告已删除')
        jobs = worker('GET', '/jobs')
        if any(row['dataset_id'] == dataset_id and row['status'] in {'QUEUED', 'RUNNING'} for row in jobs):
            raise HTTPException(409, '原数据集仍有质检任务运行，完成后才能应用更新')
        try:
            return apply_update(update_id)
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc


@router.post('/manage/raw/{dataset_id}/updates/{update_id}/cancel')
def discard_update(dataset_id: str, update_id: str):
    from routers.qc import worker
    with MUTATION_LOCK:
        update = get_update(update_id)
        if not update or update['target_id'] != dataset_id:
            raise HTTPException(404, '更新候选不存在')
        if update['run_id']:
            job = worker('GET', f"/jobs/{update['run_id']}")
            if job['status'] in {'QUEUED', 'RUNNING'}:
                raise HTTPException(409, '质检仍在运行，完成后才能取消候选')
        try:
            cancel_update(update_id)
            return {'cancelled': True}
        except (OSError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc


@router.delete('/manage/raw/{dataset_id}')
def delete_managed_dataset(dataset_id: str):
    from routers.qc import delete_dataset
    return delete_dataset(dataset_id)


@router.get('/records/')
def records_page(request: Request, q: str = '', state: str = 'all'):
    items = [dataset_summary(item) for item in record_overview()]
    if state == 'missing':
        items = [item for item in items if item['raw_state'] == 'MISSING']
    elif state == 'deleted':
        items = [item for item in items if item['status'] == 'DELETED']
    query = q.strip().casefold()
    if query:
        items = [item for item in items if query in ' '.join(
            str(item.get(key, '')) for key in ('code', 'name', 'raw_relpath')).casefold()]
    return page(request, 'data_records.html', items=items, query=q.strip(),
                state=state if state in {'missing', 'deleted'} else 'all')


@router.delete('/records/{dataset_id}')
def delete_record(dataset_id: str, body: PurgeRequest):
    item = get_dataset(dataset_id)
    if not item:
        raise HTTPException(404, '登记记录不存在')
    if not body.confirmed:
        raise HTTPException(400, '请确认清理记录')
    try:
        return purge_record(dataset_id, body.purge_reports)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/datasets/')
def datasets_reserved(request: Request):
    return page(request, 'data_reserved.html', kind='datasets', title='Datasets',
                description='面向模型需求的 LeRobot 3.0 数据集转换功能预留中。')


@router.get('/models/')
def models_reserved(request: Request):
    return page(request, 'data_reserved.html', kind='models', title='Models',
                description='模型文件的归档与浏览功能预留中。')


@router.get('/raw/')
def raw_list(request: Request, q: str = '', view: str = 'ready'):
    items = raw_items()
    selected = [item for item in items if accepted(item) == (view != 'other')]
    query = q.strip().casefold()
    if query:
        selected = [item for item in selected if query in ' '.join(
            str(item.get(key, '')) for key in ('code', 'name', 'profile', 'raw_relpath')).casefold()]
    return page(request, 'data_raw.html', items=selected, query=q.strip(),
                view='other' if view == 'other' else 'ready',
                ready_count=sum(accepted(item) for item in items),
                other_count=sum(not accepted(item) for item in items))


@router.get('/raw/{dataset_id}')
def raw_detail(request: Request, dataset_id: str):
    item = get_dataset(dataset_id)
    if not item or item['status'] == 'DELETED':
        raise HTTPException(404, '数据集不存在')
    summary = dataset_summary(item)
    detail = dataset_detail(item) if item['available'] else None
    return page(request, 'data_raw_detail.html', item=summary, detail=detail)


def inspect_dataset(dataset_id: str, endpoint: str, **options):
    from routers.qc import worker
    item = get_dataset(dataset_id)
    if not item or item['status'] == 'DELETED' or not item['available']:
        raise HTTPException(404, '数据集目录不可用')
    return worker('POST', f'/inspect/{endpoint}',
                  {'relative_path': item['raw_relpath'], **options})


@router.get('/raw/{dataset_id}/metadata')
def raw_metadata(dataset_id: str):
    return inspect_dataset(dataset_id, 'summary')


@router.get('/raw/{dataset_id}/episodes')
def raw_episodes(dataset_id: str, offset: int = 0, limit: int = 20, task: str | None = None):
    return inspect_dataset(dataset_id, 'episodes', offset=offset, limit=limit, task=task)


@router.get('/raw/{dataset_id}/episodes/{episode_index}/sample')
def raw_episode_sample(dataset_id: str, episode_index: int, frame_index: int = 0):
    return inspect_dataset(dataset_id, 'sample', episode_index=episode_index,
                           frame_index=frame_index)


@router.get('/raw/{dataset_id}/video/{video_path:path}')
def raw_video(dataset_id: str, video_path: str):
    item = get_dataset(dataset_id)
    if not item or not item['available'] or item['status'] == 'DELETED':
        raise HTTPException(404, '视频不存在')
    root = RAW / item['raw_relpath'] / 'videos'
    file = readable_file(root, video_path)
    if file is None or file.suffix.lower() != '.mp4':
        raise HTTPException(404, '视频不存在')
    return FileResponse(file, media_type='video/mp4')


@router.get('/outputs/')
def outputs_page(request: Request):
    return page(request, 'data_outputs.html', categories=category_summary())


@router.get('/outputs/{category}/')
def output_category(request: Request, category: str, q: str = ''):
    entries = category_entries(category)
    if entries is None:
        raise HTTPException(404, '报告分类不存在')
    rows = [report_summary(category, path) for path in entries]
    query = q.strip().casefold()
    if query:
        rows = [row for row in rows if query in ' '.join(
            str(row.get(key, '')) for key in ('name', 'dataset_id', 'relative_path', 'decision')).casefold()]
    return page(request, 'data_reports.html', category=category, reports=rows,
                query=q.strip(), total=len(entries))


@router.get('/outputs/{category}/report/{report_path:path}')
def output_report(request: Request, category: str, report_path: str):
    root = category_root(category)
    if root is None:
        raise HTTPException(404, '报告分类不存在')
    file = readable_file(root, report_path)
    if file is None:
        raise HTTPException(404, '报告不存在')
    summary = report_summary(category, file)
    detail = report_detail(category, file)
    return page(request, 'data_report_detail.html', category=category,
                report=summary, detail=detail)


@router.get('/outputs/{category}/file/{report_path:path}')
def output_file(category: str, report_path: str):
    root = category_root(category)
    if root is None:
        raise HTTPException(404, '报告分类不存在')
    file = readable_file(root, report_path)
    if file is None:
        raise HTTPException(404, '报告不存在')
    return FileResponse(file, filename=file.name)
