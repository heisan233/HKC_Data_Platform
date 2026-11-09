"""Reversible dataset removal and guarded permanent cleanup."""

import shutil
from pathlib import Path

from app.db.datasets import DatasetRepository
from app.modules.datasets import service


def _safe_child(parent: Path, name: str, *, create_parent: bool = False) -> Path:
    if not name or Path(name).name != name or name.startswith("."):
        raise ValueError("目录名称不安全")
    current = parent
    for path in (current, *current.parents):
        if path.is_symlink():
            raise ValueError("存储路径包含符号链接")
    if create_parent:
        current.mkdir(parents=True, exist_ok=True)
    if not current.is_dir():
        raise ValueError("存储目录不存在")
    child = current / name
    if child.is_symlink():
        raise ValueError("数据集路径包含符号链接")
    return child


def _paths(root: Path, record: dict, *, create_trash: bool = False) -> tuple[Path, Path, Path, Path, Path]:
    dataset_id = record["id"]
    if not dataset_id.startswith("DS") or not dataset_id[2:].isdigit():
        raise ValueError("数据集编号不合法")
    source = _safe_child(root / record["storage_area"], record["storage_relpath"])
    reports = _safe_child(root / "reports" / "qc", dataset_id, create_parent=True)
    trash_folder = _safe_child(root / "trash" / "datasets", dataset_id,
                               create_parent=create_trash)
    if create_trash:
        trash_folder.mkdir(exist_ok=True)
    content = _safe_child(trash_folder, "content") if trash_folder.exists() else trash_folder / "content"
    trash_reports = _safe_child(trash_folder, "reports") if trash_folder.exists() else trash_folder / "reports"
    return source, reports, trash_folder, content, trash_reports


def _active_job_reason(repo: DatasetRepository, dataset_id: str) -> str | None:
    with repo.connection() as db:
        if db.execute("SELECT 1 FROM qc_tasks WHERE dataset_id=? AND status IN ('QUEUED','RUNNING') LIMIT 1",
                      (dataset_id,)).fetchone():
            return "质检任务仍在执行"
        if db.execute("""SELECT 1 FROM training_jobs WHERE dataset_id=?
            AND status IN ('QUEUED','PREPARING','RUNNING','EVALUATING','PACKAGING') LIMIT 1""",
                      (dataset_id,)).fetchone():
            return "训练任务仍在执行"
        if db.execute("""SELECT 1 FROM conversion_jobs WHERE source_dataset_id=?
            AND status NOT IN ('COMPLETED','FAILED','CANCELED','INTERRUPTED') LIMIT 1""",
                      (dataset_id,)).fetchone():
            return "转换任务仍在执行"
    return None


def purge_blocker(root: Path, record: dict) -> str | None:
    """Retain DS lineage and immutable task references until dependents are cleared."""
    repo = DatasetRepository(root)
    dataset_id = record["id"]
    reason = _active_job_reason(repo, dataset_id)
    if reason:
        return reason
    for other in repo.list(deleted=None):
        if other["id"] != dataset_id and dataset_id in other["source_dataset_ids"]:
            return f"派生数据集 {other['id']} 仍引用此数据集"
    with repo.connection() as db:
        training = db.execute("SELECT id FROM training_jobs WHERE dataset_id=? LIMIT 1",
                              (dataset_id,)).fetchone()
        if training:
            return f"训练任务 {training['id']} 仍引用此数据集"
        conversion = db.execute("SELECT id FROM conversion_jobs WHERE source_dataset_id=? LIMIT 1",
                                (dataset_id,)).fetchone()
        if conversion:
            return f"转换任务 {conversion['id']} 仍引用此数据集"
    return None


def trash_dataset(root: Path, dataset_id: str, username: str) -> dict:
    repo = DatasetRepository(root)
    record = repo.get(dataset_id)
    if record is None:
        raise ValueError("数据集不存在或已在回收站")
    reason = _active_job_reason(repo, dataset_id)
    if reason:
        raise ValueError(reason)
    source, reports, _folder, content, trash_reports = _paths(root, record, create_trash=True)
    if content.exists() or trash_reports.exists():
        raise ValueError("回收站中已有同编号文件，不能覆盖")
    if source.exists() and not source.is_dir():
        raise ValueError("数据集目录不是普通目录")
    if reports.exists() and not reports.is_dir():
        raise ValueError("质检报告目录不是普通目录")
    if repo.trash(dataset_id, username) is None:
        raise ValueError("数据集已被其他操作移入回收站")
    moved: list[tuple[Path, Path]] = []
    try:
        for original, destination in ((source, content), (reports, trash_reports)):
            if original.exists():
                original.replace(destination)
                moved.append((destination, original))
    except OSError:
        for destination, original in reversed(moved):
            destination.replace(original)
        repo.restore(dataset_id)
        raise
    return repo.get(dataset_id, include_deleted=True)


def restore_dataset(root: Path, dataset_id: str) -> dict:
    repo = DatasetRepository(root)
    record = repo.get(dataset_id, include_deleted=True)
    if record is None or record["deleted_at"] is None:
        raise ValueError("回收站中没有此数据集")
    if record["purge_requested_at"] is not None:
        raise ValueError("数据集正在永久清除，不能恢复")
    source, reports, folder, content, trash_reports = _paths(root, record)
    if (content.exists() and source.exists()) or (trash_reports.exists() and reports.exists()):
        raise ValueError("原目录已有同名文件，不能覆盖")
    moved: list[tuple[Path, Path]] = []
    try:
        for original, destination in ((content, source), (trash_reports, reports)):
            if original.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                original.replace(destination)
                moved.append((destination, original))
        if folder.exists():
            folder.rmdir()
        restored = repo.restore(dataset_id)
        if restored is None:
            raise ValueError("数据集状态已变化，无法恢复")
    except Exception:
        for destination, original in reversed(moved):
            original.parent.mkdir(parents=True, exist_ok=True)
            destination.replace(original)
        raise
    return restored


def purge_dataset(root: Path, dataset_id: str) -> dict:
    repo = DatasetRepository(root)
    record = repo.get(dataset_id, include_deleted=True)
    if record is None or record["deleted_at"] is None:
        raise ValueError("回收站中没有此数据集")
    reason = purge_blocker(root, record)
    if reason:
        raise ValueError(reason)
    source, reports, folder, content, trash_reports = _paths(root, record)
    # A restart between the DB flag and file moves can leave source files in place.
    # Finish that move before irreversible cleanup; never overwrite a trash entry.
    for original, destination in ((source, content), (reports, trash_reports)):
        if original.exists():
            if destination.exists():
                raise ValueError("原目录与回收站中都有文件，不能自动清除")
            folder.mkdir(parents=True, exist_ok=True)
            original.replace(destination)
    if record["purge_requested_at"] is None:
        repo.request_purge(dataset_id)
    if folder.exists():
        shutil.rmtree(folder)
    standalone_parent = root / "trash" / "reports"
    if standalone_parent.exists():
        standalone_reports = _safe_child(standalone_parent, dataset_id)
        if standalone_reports.exists():
            shutil.rmtree(standalone_reports)
    exports = root / "exports"
    if exports.is_dir() and not exports.is_symlink():
        for archive in exports.glob(f"{dataset_id}-*.tar.gz"):
            if archive.is_file() and not archive.is_symlink():
                archive.unlink()
    if not repo.finish_purge(dataset_id):
        raise ValueError("数据集状态已变化，清除未完成")
    return {"dataset_id": dataset_id, "purged": True}


def report_groups(root: Path) -> list[dict]:
    repo = DatasetRepository(root)
    groups = []
    for record in repo.list(deleted=None):
        reports = service.trashed_reports_for(root, record["id"])
        if reports:
            groups.append({"dataset": record, "reports": reports})
    return groups
