"""提供质检页面、数据集 ZIP 上传，以及 worker 任务、规则和报告的平台代理接口。"""
import json
import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from dataset_store import RAW, ROOT, create_dataset, get_dataset, init_store, list_datasets, raw_path, set_status
from data_management import MUTATION_LOCK, list_updates, update_target_for_worker

router = APIRouter()
templates = Jinja2Templates(directory='templates')
WORKER = os.getenv('QC_WORKER_URL', 'http://lerobot-worker:9000')
MAX_EXTRACTED_BYTES = 100 * 1024**3
init_store()


def worker(method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = UrlRequest(WORKER + path, data=body, method=method,
                         headers={'Content-Type': 'application/json'} if body else {})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            detail = json.load(exc).get('detail', str(exc))
        except ValueError:
            detail = str(exc)
        raise HTTPException(exc.code, detail)
    except URLError as exc:
        raise HTTPException(503, f'质检 worker 不可用: {exc.reason}')


def unpack_dataset(archive, staging):
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if sum(item.file_size for item in members) > MAX_EXTRACTED_BYTES:
            raise ValueError('解压后数据超过 100 GB')
        for item in members:
            name = item.filename.replace('\\', '/')
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or not relative.parts:
                raise ValueError('压缩包包含非法路径')
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError('压缩包不能包含软链接')
            target = staging.joinpath(*relative.parts)
            target.resolve().relative_to(staging.resolve())
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
    infos = list(staging.rglob('meta/info.json'))
    if len(infos) != 1:
        raise ValueError('压缩包必须包含且仅包含一个数据集的 meta/info.json')
    dataset_root = infos[0].parent.parent
    info = json.loads(infos[0].read_text(encoding='utf-8'))
    if not str(info.get('codebase_version', '')).startswith('v3'):
        raise ValueError('当前仅支持 LeRobot v3 格式的原始数据')
    return dataset_root


class DatasetRef(BaseModel):
    dataset_id: str
    profile: str | None = None


class ProfileCreate(BaseModel):
    id: str
    config: dict


class ProfileUpdate(BaseModel):
    config: dict


class RuleChange(BaseModel):
    enabled: bool


class DecisionChange(BaseModel):
    decision: str
    reason: str = ''


@router.get('/')
def qc_page(request: Request):
    return templates.TemplateResponse(request=request, name='qc.html', context={})


@router.get('/api/datasets')
def datasets():
    return list_datasets(available_only=True)


@router.post('/api/datasets/upload', status_code=201)
async def upload_dataset(name: str = Form(...), profile: str = Form('zhiyan'), file: UploadFile = File(...)):
    name = name.strip()
    if not name or len(name) > 100:
        raise HTTPException(400, '数据集名称须为 1–100 个字符')
    profiles = {item['id']: item for item in worker('GET', '/profiles')}
    if profile not in profiles:
        raise HTTPException(400, '机器人配置不存在')
    if not file.filename or not file.filename.lower().endswith('.zip'):
        raise HTTPException(400, '请上传 ZIP 数据集压缩包')
    dataset_id = create_dataset(name, profile)
    incoming = ROOT / 'upload_tmp'
    incoming.mkdir(parents=True, exist_ok=True)
    archive = incoming / f'{dataset_id}.zip'
    staging = incoming / f'{dataset_id}.part'
    destination = RAW / dataset_id
    try:
        with archive.open('wb') as output:
            while block := await file.read(8 * 1024 * 1024):
                output.write(block)
        staging.mkdir()
        dataset_root = unpack_dataset(archive, staging)
        info = json.loads((dataset_root / 'meta/info.json').read_text(encoding='utf-8'))
        if info.get('robot_type') != profiles[profile]['robot_type']:
            raise ValueError('压缩包 robot_type 与所选机器人配置不匹配')
        os.replace(dataset_root, destination)
        set_status(dataset_id, 'READY')
        return get_dataset(dataset_id)
    except (ValueError, zipfile.BadZipFile, OSError, json.JSONDecodeError) as exc:
        set_status(dataset_id, 'UPLOAD_ERROR')
        raise HTTPException(400, f'上传失败: {exc}')
    finally:
        await file.close()
        archive.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


@router.delete('/api/datasets/{dataset_id}')
def delete_dataset(dataset_id: str):
    with MUTATION_LOCK:
        dataset = get_dataset(dataset_id)
        if not dataset or dataset['status'] == 'DELETED':
            raise HTTPException(404, '数据集不存在')
        if any(item['status'] not in {'APPLIED', 'CANCELLED'} for item in list_updates(dataset_id)):
            raise HTTPException(409, '存在待处理的数据更新，请先应用或取消')
        jobs = worker('GET', '/jobs')
        if any(item['dataset_id'] == dataset_id and item['status'] in {'QUEUED', 'RUNNING'} for item in jobs):
            raise HTTPException(409, '质检任务正在运行，不能删除原始数据集')
        try:
            path = raw_path(dataset['raw_relpath'])
            if path.exists():
                if not path.is_dir():
                    raise HTTPException(409, '原始数据路径不是目录')
                shutil.rmtree(path)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, f'无法删除原始数据目录: {exc}') from exc
        set_status(dataset_id, 'DELETED')
        return {'deleted': True, 'dataset_id': dataset_id, 'code': dataset['code']}


@router.get('/api/rules')
def rules(profile: str = 'zhiyan'):
    return worker('GET', f'/rules?profile={quote(profile, safe="")}')


@router.patch('/api/rules/{name}')
def toggle_rule(name: str, body: RuleChange, profile: str = 'zhiyan'):
    return worker('PATCH', f'/rules/{quote(name, safe="")}?profile={quote(profile, safe="")}', body.model_dump())


@router.get('/api/profiles')
def profiles():
    return worker('GET', '/profiles')


@router.get('/api/profiles/{profile_id}')
def profile_detail(profile_id: str):
    return worker('GET', f'/profiles/{quote(profile_id, safe="")}')


@router.post('/api/profiles', status_code=201)
def create_profile(body: ProfileCreate):
    return worker('POST', '/profiles', body.model_dump())


@router.put('/api/profiles/{profile_id}')
def update_profile(profile_id: str, body: ProfileUpdate):
    current = worker('GET', f'/profiles/{quote(profile_id, safe="")}')['config']
    if body.config.get('robot_type') != current['robot_type'] and any(
        item['profile'] == profile_id and item['status'] != 'DELETED'
        for item in list_datasets(include_deleted=True)
    ):
        raise HTTPException(409, '该配置已关联原始数据集，不能修改 robot_type')
    return worker('PUT', f'/profiles/{quote(profile_id, safe="")}', body.model_dump())


@router.delete('/api/profiles/{profile_id}')
def delete_profile(profile_id: str):
    if any(item['profile'] == profile_id and item['status'] != 'DELETED'
           for item in list_datasets(include_deleted=True)):
        raise HTTPException(409, '该配置仍关联原始数据集，不能删除')
    return worker('DELETE', f'/profiles/{quote(profile_id, safe="")}')


@router.post('/api/jobs', status_code=202)
def create_job(body: DatasetRef):
    with MUTATION_LOCK:
        dataset = get_dataset(body.dataset_id)
        if not dataset or not dataset['available'] or dataset['status'] in {'UPLOADING', 'UPLOAD_ERROR', 'DELETED'}:
            raise HTTPException(404, '待质检数据集不存在或上传未完成')
        result = worker('POST', '/jobs', {'dataset_id': dataset['id'],
                                          'relative_path': dataset['raw_relpath'],
                                          'profile': body.profile or dataset['profile']})
        set_status(dataset['id'], 'QC_QUEUED')
        return result


@router.get('/api/jobs')
def jobs():
    datasets = {item['id']: item for item in list_datasets(include_deleted=True)}
    items = worker('GET', '/jobs')
    synced = set()
    for item in items:
        dataset = datasets.get(item['dataset_id'])
        update = update_target_for_worker(item['dataset_id']) if not dataset else None
        target = datasets.get(update['target_id']) if update else None
        item['dataset_name'] = (dataset['name'] if dataset else
                                f"{target['name']} · 更新候选" if target else item['dataset_id'])
        if update and update['status'] == 'APPLIED' and target and target['available'] and target['id'] not in synced:
            status = f"QC_{item['decision']}" if item['status'] == 'COMPLETED' else f"QC_{item['status']}"
            if target['status'] != status:
                set_status(target['id'], status)
            synced.add(target['id'])
        if dataset and dataset['available'] and item['dataset_id'] not in synced and dataset['status'] != 'PUBLISHED':
            status = f"QC_{item['decision']}" if item['status'] == 'COMPLETED' else f"QC_{item['status']}"
            if dataset['status'] != status:
                set_status(item['dataset_id'], status)
            synced.add(item['dataset_id'])
    return items


@router.get('/api/jobs/{run_id}')
def job(run_id: str):
    return worker('GET', f'/jobs/{run_id}')


@router.delete('/api/jobs/{run_id}')
def delete_job(run_id: str):
    with MUTATION_LOCK:
        item = worker('GET', f'/jobs/{quote(run_id, safe="")}')
        if any(update['run_id'] == run_id for update in list_updates()):
            raise HTTPException(409, '该质检记录关联数据更新流程，不能单独删除')
        result = worker('DELETE', f'/jobs/{quote(run_id, safe="")}')
        remaining = jobs()
        dataset = get_dataset(item['dataset_id'])
        if dataset and dataset['status'].startswith('QC_'):
            related = False
            for other in remaining:
                update = update_target_for_worker(other['dataset_id'])
                if other['dataset_id'] == dataset['id'] or (
                    update and update['target_id'] == dataset['id'] and update['status'] == 'APPLIED'
                ):
                    related = True
                    break
            if not related:
                set_status(dataset['id'], 'READY')
        return result


@router.get('/api/jobs/{run_id}/report')
def report(run_id: str):
    return worker('GET', f'/jobs/{run_id}/report')


@router.delete('/api/jobs/{run_id}/report')
def delete_report(run_id: str):
    with MUTATION_LOCK:
        return worker('DELETE', f'/jobs/{run_id}/report')


@router.post('/api/jobs/{run_id}/decision')
def set_decision(run_id: str, body: DecisionChange):
    with MUTATION_LOCK:
        result = worker('POST', f'/jobs/{run_id}/decision', body.model_dump())
        # The list is newest-first, so a decision on an older run cannot replace
        # the state established by a newer QC run or an applied update candidate.
        jobs()
        return result
