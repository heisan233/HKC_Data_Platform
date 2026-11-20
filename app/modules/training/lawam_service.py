"""Platform-side client for the private LaWAM Docker executor."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from app.db.datasets import DatasetRepository
from app.modules.datasets import service as datasets
from app.modules.datasets.lerobot_v3 import load_tasks, parquet_files
from app.modules.quality.service import raw_fingerprint
from app.modules.quality.status import attach_status


class LaWAMError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def available_datasets(root: Path) -> list[dict]:
    rows = DatasetRepository(root).list(availability="available", dataset_family="wam")
    items = [datasets.summary(root, row["storage_relpath"], row, row["storage_area"])
             for row in rows]
    attach_status(root, items)
    return [{"id": item["id"], "name": item["name"],
             "episodes": item["episodes"], "robot_type": item["robot_type"]}
            for item in items if item["state"] == "ready" and item["qc_state"] == "passed"]


def preflight(root: Path, dataset_id: str, mode: str = "all",
              episodes: str = "") -> dict:
    try:
        record, folder = datasets.resolve_registered_dataset(
            root, dataset_id, allowed_areas=("data",), availability="available")
    except ValueError as exc:
        raise LaWAMError(str(exc), 409) from exc
    if record["dataset_family"] != "wam":
        raise LaWAMError("WAM 训练只能选择 WAM 数据集", 409)
    item = datasets.summary(root, record["storage_relpath"], record, "data")
    attach_status(root, [item])
    if item["state"] != "ready" or item["qc_state"] != "passed":
        raise LaWAMError("数据集目录或质检结论已变化，请重新质检", 409)
    info, error = datasets.dataset_info(folder)
    if error or info is None:
        raise LaWAMError(error or "数据集元数据不可读取", 422)
    features = info.get("features")
    if not isinstance(features, dict):
        raise LaWAMError("meta/info.json 缺少有效的 features", 422)
    required = ("observation.images.image", "observation.images.image2",
                "observation.state", "action", "task_index")
    missing = [key for key in required if key not in features]
    if missing:
        raise LaWAMError("缺少 LaWAM 必需字段：" + "、".join(missing), 422)
    if any(not isinstance(features[key], dict) for key in required):
        raise LaWAMError("LaWAM 字段定义无效", 422)
    for key, shape in (("observation.state", [8]), ("action", [7])):
        if features[key].get("shape") != shape:
            raise LaWAMError(f"{key} 维度必须是 {shape}，当前基座面向 Panda 数据", 422)
    if any(features[key].get("dtype") != "video" for key in required[:2]):
        raise LaWAMError("LaWAM 需要两路视频图像字段", 422)
    for key in required[:2]:
        video_files = sorted((folder / "videos" / key).rglob("*.mp4"))
        if not video_files or datasets.safe_file(folder, str(video_files[0].relative_to(folder))) is None:
            raise LaWAMError(f"缺少可读取的视频文件：{key}", 422)
    if info.get("robot_type") != "panda":
        raise LaWAMError("当前 LaWAM 基座只支持 panda 机器人数据", 422)
    try:
        tasks, _ = load_tasks(folder)
        if not tasks:
            raise ValueError("任务列表为空")
        paths = parquet_files(folder / "data")
        if not paths:
            raise ValueError("缺少帧数据")
        schema = pq.read_schema(paths[0])
        if not {"observation.state", "action", "task_index"}.issubset(schema.names):
            raise ValueError("帧数据缺少 LaWAM 必需列")
        sample = next(pq.ParquetFile(paths[0]).iter_batches(
            batch_size=1, columns=["observation.state", "action"]), None)
        if sample is None or sample.num_rows == 0:
            raise ValueError("帧数据为空")
        values = sample.to_pylist()[0]
        if len(values["observation.state"]) != 8 or len(values["action"]) != 7:
            raise ValueError("帧数据状态或动作维度与 LaWAM 基座不符")
        from app.modules.training.service import _episode_indexes
        indexes = _episode_indexes(folder)
        if not indexes:
            raise ValueError("缺少 Episode 元数据")
    except (ValueError, OSError, pa.ArrowException) as exc:
        raise LaWAMError(f"LaWAM 数据检查失败：{exc}", 422) from exc
    if mode == "custom":
        try:
            selected = [int(part.strip()) for part in episodes.split(",")]
        except ValueError as exc:
            raise LaWAMError("自定义 Episode 格式无效", 422) from exc
        if not selected or any(index not in indexes for index in selected):
            raise LaWAMError("自定义 Episode 不在所选数据集中", 422)
    try:
        fingerprint = raw_fingerprint(folder)
    except (ValueError, OSError) as exc:
        raise LaWAMError("数据集快照无法读取", 422) from exc
    return {"dataset_id": dataset_id, "dataset_name": record["name"],
            "episodes": len(indexes), "tasks": len(tasks),
            "snapshot": {"dataset_id": dataset_id, "info_sha256": record["info_sha256"],
                         "dataset_fingerprint": fingerprint}}


def request(method: str, path: str, payload: dict | None = None,
            headers: dict[str, str] | None = None) -> dict | list:
    socket = Path(os.getenv("HKC_LAWAM_AGENT_SOCKET", "/run/hkc-lawam-agent/agent.sock"))
    token_path = Path(os.getenv("HKC_LAWAM_AGENT_TOKEN_FILE", "/run/hkc-lawam-agent/token"))
    if (not socket.is_socket() or socket.is_symlink() or not token_path.is_file()
            or token_path.is_symlink()):
        raise LaWAMError("LaWAM 执行器尚未连接")
    try:
        token = token_path.read_text(encoding="utf-8").strip()
        if not token:
            raise LaWAMError("LaWAM 执行器令牌为空")
        with httpx.Client(transport=httpx.HTTPTransport(uds=str(socket)),
                          base_url="http://lawam-agent", timeout=15, trust_env=False,
                          headers={"Authorization": f"Bearer {token}"}) as client:
            response = client.request(method, path, json=payload, headers=headers)
        result = response.json()
    except LaWAMError:
        raise
    except (OSError, ValueError, httpx.HTTPError) as exc:
        raise LaWAMError("LaWAM 执行器未响应") from exc
    if response.is_error:
        message = result.get("error", "LaWAM 执行器请求失败") if isinstance(result, dict) else "LaWAM 执行器请求失败"
        raise LaWAMError(str(message), response.status_code if response.status_code < 500 else 503)
    if not isinstance(result, (dict, list)):
        raise LaWAMError("LaWAM 执行器返回格式无效", 502)
    return result
