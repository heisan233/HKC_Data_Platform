"""Read-only summaries of files below /data for the data-management pages."""
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from dataset_store import RAW, ROOT, raw_path
from data_management import list_updates, record_exists, update_target_for_worker

OUTPUTS = ROOT / 'outputs'
REPORT_LABELS = {'qc': '质检报告'}
STATUS_LABELS = {
    'QC_PASS': '质检通过', 'PUBLISHED': '历史已发布', 'QC_REVIEW': '待人工复核',
    'QC_FAIL': '质检未通过', 'QC_QUEUED': '等待质检', 'QC_RUNNING': '质检中',
    'READY': '待质检', 'UPLOADING': '上传中', 'UPLOAD_ERROR': '上传失败',
    'DELETED': '已删除',
}
DECISION_LABELS = {'PASS': '通过', 'REVIEW': '待复核', 'FAIL': '未通过',
                   'WARN': '警告', 'ERROR': '错误'}


def size_label(size):
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024


def duration_label(frames, fps):
    if not isinstance(frames, (int, float)) or not isinstance(fps, (int, float)) or fps <= 0:
        return '—'
    seconds = round(frames / fps)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}时{minutes:02d}分{seconds:02d}秒' if hours else f'{minutes}分{seconds:02d}秒'


def file_size(root):
    total = 0
    for path in root.rglob('*'):
        if path.is_file() and not path.is_symlink():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def read_json(path, limit=4 * 1024 * 1024):
    try:
        if path.is_file() and path.stat().st_size <= limit:
            return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        pass
    return None


def readable_file(root, relative):
    """Keep file requests inside one asset directory, including through symlinks."""
    part = Path(relative)
    if part.is_absolute() or '..' in part.parts or not part.parts:
        return None
    try:
        target = root.joinpath(part).resolve(strict=True)
        if target.is_file() and target.is_relative_to(root.resolve(strict=True)) and not target.is_symlink():
            return target
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def dataset_root(item):
    try:
        root = raw_path(item['raw_relpath'])
        return root if root.is_dir() and not root.is_symlink() else None
    except (OSError, ValueError):
        return None


def dataset_summary(item):
    result = dict(item)
    result['status_label'] = STATUS_LABELS.get(item['status'], item['status'])
    for field in ('created_at', 'uploaded_at'):
        value = item.get(field)
        try:
            result[f'{field}_display'] = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(
                ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M') if value else None
        except (ValueError, AttributeError):
            result[f'{field}_display'] = None
    result['state_label'] = {'PRESENT': '目录存在', 'MISSING': '目录缺失',
                             'UPLOADING': '上传中', 'DELETED': '已删除'}.get(item['raw_state'], item['raw_state'])
    root = dataset_root(item) if item['available'] else None
    info_path = readable_file(root, 'meta/info.json') if root else None
    info = read_json(info_path) if info_path else None
    info = info if isinstance(info, dict) else {}
    result['episodes'] = info.get('total_episodes')
    result['frames'] = info.get('total_frames')
    result['fps'] = info.get('fps')
    features = info.get('features')
    features = features if isinstance(features, dict) else {}
    result['cameras'] = sum(isinstance(value, dict) and value.get('dtype') == 'video'
                            for value in features.values())
    result['size'] = size_label(file_size(root)) if root else '—'
    result['duration'] = duration_label(result['frames'], result['fps'])
    return result


def dataset_detail(item):
    root = dataset_root(item)
    if root is None:
        return None
    info_path = readable_file(root, 'meta/info.json')
    stats_path = readable_file(root, 'meta/stats.json')
    info = read_json(info_path) if info_path else None
    stats = read_json(stats_path) if stats_path else None
    info = info if isinstance(info, dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    features = info.get('features')
    features = features if isinstance(features, dict) else {}
    cameras = []
    for key, definition in features.items():
        if not isinstance(definition, dict) or definition.get('dtype') != 'video':
            continue
        files = sorted((root / 'videos' / key).glob('chunk-*/*.mp4'))
        options = []
        for file in files:
            if file.is_symlink() or not file.is_file():
                continue
            relative = file.relative_to(root / 'videos').as_posix()
            if readable_file(root / 'videos', relative) is None:
                continue
            options.append({'name': file.name, 'size': size_label(file.stat().st_size),
                            'url': f"/data/raw/{quote(item['id'], safe='')}/video/{quote(relative, safe='/')}"})
        shape = definition.get('shape')
        resolution = f'{shape[1]} × {shape[0]}' if isinstance(shape, list) and len(shape) >= 2 else '分辨率未知'
        cameras.append({'key': key, 'name': key.removeprefix('observation.images.').replace('_', ' '),
                        'resolution': resolution,
                        'fps': (definition.get('video_info') or {}).get('video.fps') or info.get('fps'),
                        'files': options})
    feature_rows = [{'name': key, 'dtype': definition.get('dtype', '—'),
                     'shape': definition.get('shape', '—'), 'stats': stats.get(key)}
                    for key, definition in features.items() if isinstance(definition, dict)]
    files = []
    for file in sorted(root.rglob('*')):
        if file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(root.resolve()):
            files.append({'path': file.relative_to(root).as_posix(),
                          'size': size_label(file.stat().st_size)})
    report_groups = {item['id']} | {row['worker_dataset_id'] for row in list_updates(item['id'])
                                    if row['status'] == 'APPLIED'}
    reports = [report_summary('qc', file) for file in category_entries('qc') or []
               if file.relative_to(OUTPUTS / 'qc').parts[0] in report_groups]
    latest = reports[0] if reports else None
    episode_rows = []
    if latest:
        report = read_json(OUTPUTS / 'qc' / latest['relative_path'])
        if isinstance(report, dict) and isinstance(report.get('episode_summary'), list):
            episode_rows = report['episode_summary']
    return {'info': info, 'stats': stats, 'cameras': cameras, 'features': feature_rows,
            'files': files, 'reports': reports, 'episodes': episode_rows}


def category_root(category):
    part = Path(category)
    if not category or category.startswith('.') or len(part.parts) != 1 or part.name != category:
        return None
    root = OUTPUTS / category
    if not root.is_dir() or root.is_symlink():
        return None
    return root


def category_entries(category):
    root = category_root(category)
    if root is None:
        return None
    files = [path for path in root.rglob('*') if path.is_file() and not path.is_symlink()
             and path.resolve().is_relative_to(root.resolve())
             and not any(part.startswith('.') for part in path.relative_to(root).parts)]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def category_summary():
    if not OUTPUTS.is_dir():
        return []
    rows = []
    for root in sorted(OUTPUTS.iterdir()):
        entries = category_entries(root.name)
        if entries is not None:
            rows.append({'key': root.name, 'label': REPORT_LABELS.get(root.name, root.name),
                         'count': len(entries), 'size': size_label(sum(p.stat().st_size for p in entries))})
    return rows


def report_summary(category, path):
    root = OUTPUTS / category
    relative = path.relative_to(root).as_posix()
    data = read_json(path) if path.suffix.lower() == '.json' else None
    data = data if isinstance(data, dict) else {}
    group = path.relative_to(root).parts[0] if len(path.relative_to(root).parts) > 1 else None
    update = update_target_for_worker(group) if category == 'qc' and group else None
    dataset_id = update['target_id'] if update else group if category == 'qc' else None
    created = data.get('created_at')
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created.replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M')
        except ValueError:
            pass
    return {'name': path.name, 'relative_path': relative, 'url_path': quote(relative, safe='/'),
            'dataset_id': dataset_id, 'source_exists': record_exists(dataset_id) if dataset_id else False,
            'group': group, 'size': size_label(path.stat().st_size),
            'created_at': created or datetime.fromtimestamp(path.stat().st_mtime).astimezone().strftime('%Y-%m-%d %H:%M'),
            'decision': data.get('decision'),
            'decision_label': DECISION_LABELS.get(data.get('decision'), data.get('decision') or '—'),
            'status': data.get('status') or '—', 'summary': data.get('summary'),
            'extension': path.suffix.lower()}


def report_detail(category, path):
    suffix = path.suffix.lower()
    if suffix == '.json':
        data = read_json(path)
        if data is None:
            return {'kind': 'unavailable', 'message': '文件过大或 JSON 内容无法解析。'}
        if category == 'qc' and isinstance(data, dict):
            return {'kind': 'qc', 'data': data}
        return {'kind': 'json', 'text': json.dumps(data, ensure_ascii=False, indent=2)}
    if suffix in {'.txt', '.md', '.log', '.csv'}:
        try:
            with path.open('r', encoding='utf-8', errors='replace') as source:
                return {'kind': 'text', 'text': source.read(200_001)[:200_000]}
        except OSError:
            return {'kind': 'unavailable', 'message': '文件无法读取。'}
    return {'kind': 'file'}
