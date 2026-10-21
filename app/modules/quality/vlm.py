"""Episode-aligned video judgements through the existing OpenAI-compatible VLM API."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
import pyarrow.parquet as pq

from app.modules.datasets.lerobot_v3 import (
    episode_task_indexes,
    episode_task_text,
    load_tasks,
)
from app.modules.datasets.service import safe_file

MAX_VIDEO_BYTES = 100 * 1024 * 1024


def _probe_endpoint(base_url: str, token: str) -> None:
    """Fail once, quickly, when the external VLM service cannot be reached."""
    with httpx.Client(timeout=httpx.Timeout(8, connect=3), trust_env=False) as client:
        response = client.get(base_url.rstrip("/") + "/models",
                              headers={"Authorization": f"Bearer {token or 'EMPTY'}"})
        response.raise_for_status()


def _episode_rows(folder: Path) -> list[dict]:
    rows = []
    for path in sorted((folder / "meta" / "episodes").rglob("*.parquet")):
        if safe_file(folder, path.relative_to(folder).as_posix()):
            rows.extend(pq.read_table(path).to_pylist())
    return sorted(rows, key=lambda row: row.get("episode_index", 0))


def _video_segment(folder: Path, info: dict, episode: dict, key: str,
                   destination: Path, timeout: int) -> tuple[Path, dict]:
    prefix = f"videos/{key}/"
    chunk = episode.get(prefix + "chunk_index")
    file_index = episode.get(prefix + "file_index")
    start = episode.get(prefix + "from_timestamp")
    end = episode.get(prefix + "to_timestamp")
    if any(value is None for value in (chunk, file_index, start, end)):
        raise ValueError("Episode 缺少此相机的视频索引或时间边界")
    duration = float(end) - float(start)
    if duration <= 0 or duration > 3600:
        raise ValueError("Episode 视频时间范围无效")
    template = info.get("video_path")
    if not isinstance(template, str):
        raise TypeError("meta/info.json 缺少 video_path")
    relative = template.format(video_key=key, chunk_index=int(chunk), file_index=int(file_index))
    source = safe_file(folder, relative)
    if source is None or source.suffix.lower() != ".mp4":
        raise ValueError("Episode 对应的 MP4 不存在或路径不安全")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-i", str(source), "-ss", str(float(start)), "-t", str(duration),
               "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
               "-movflags", "+faststart", str(destination)]
    run = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if run.returncode or not destination.is_file() or destination.stat().st_size == 0:
        raise ValueError((run.stderr or "无法截取 Episode 视频")[-600:])
    if destination.stat().st_size > MAX_VIDEO_BYTES:
        raise ValueError("Episode 片段超过 100 MB，请人工复核或调整视频编码")
    return destination, {"camera": key, "source_file": relative,
                         "from_timestamp": start, "to_timestamp": end,
                         "clip_bytes": destination.stat().st_size}


def _json_answer(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("模型未返回 JSON") from None
        data = json.loads(match.group())
    if not isinstance(data, dict) or data.get("result") not in {"pass", "fail", "uncertain"}:
        raise ValueError("模型 JSON 缺少有效的 result")
    reason = data.get("reason")
    evidence = data.get("evidence")
    if not isinstance(reason, str) or not isinstance(evidence, str):
        raise TypeError("模型 JSON 缺少 reason 或 evidence")
    return {"result": data["result"], "reason": reason[:500], "evidence": evidence[:500]}


def _ask_model(base_url: str, token: str, model: str, rule: dict, task: str,
               clip: Path, timeout: int, max_tokens: int) -> dict:
    video = base64.b64encode(clip.read_bytes()).decode("ascii")
    prompt = (f"机器人采集任务：{task}\n判断要求：{rule['prompt']}\n"
              "只依据视频中可见的内容判断；看不清或无法确认时返回 uncertain。"
              "仅输出 JSON：{\"result\":\"pass|fail|uncertain\","
              "\"reason\":\"简短中文理由\",\"evidence\":\"可见证据\"}。")
    payload = {"model": model, "temperature": 0, "max_tokens": max_tokens,
               "messages": [{"role": "user", "content": [
                   {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + video}},
                   {"type": "text", "text": prompt},
               ]}]}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.post(base_url.rstrip("/") + "/chat/completions", json=payload,
                               headers={"Authorization": f"Bearer {token or 'EMPTY'}"})
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise TypeError("模型返回内容不是文本")
    return _json_answer(content)


def run_vlm_rules(root: Path, task_id: str, folder: Path, snapshot: dict,
                  base_url: str, token: str) -> dict:
    config = snapshot["vlm"]
    rules = config["rules"]
    active = [rule for rule in rules if rule["enabled"]]
    results = [{"id": rule["id"], "name": rule["name"], "status": "SKIPPED",
                "episodes": [], "reason": "本次未启用"} for rule in rules if not rule["enabled"]]
    if not active:
        return {"status": "SKIPPED", "results": results, "reason": "本次未启用 VLM 规则"}
    if not base_url:
        results.extend({"id": rule["id"], "name": rule["name"], "status": "ERROR",
                        "episodes": [], "reason": "VLM_BASE_URL 未配置"} for rule in active)
        return {"status": "ERROR", "results": results}
    try:
        _probe_endpoint(base_url, token)
    except Exception as exc:  # noqa: BLE001 - report one connection failure for all rules
        reason = f"VLM 服务连接失败：{type(exc).__name__}: {exc}"[:500]
        results.extend({"id": rule["id"], "name": rule["name"], "status": "ERROR",
                        "episodes": [], "reason": reason} for rule in active)
        return {"status": "ERROR", "results": results}
    try:
        info_file = safe_file(folder, "meta/info.json")
        if info_file is None:
            raise ValueError("meta/info.json 不可读取")
        info = json.loads(info_file.read_text(encoding="utf-8"))
        episodes = _episode_rows(folder)
        if not episodes:
            raise ValueError("没有可读取的 Episode 元数据")
        _, task_names = load_tasks(folder)
    except Exception as exc:  # noqa: BLE001 - surface metadata failures per rule
        results.extend({"id": rule["id"], "name": rule["name"], "status": "ERROR",
                        "episodes": [], "reason": str(exc)[:500]} for rule in active)
        return {"status": "ERROR", "results": results}

    max_episodes = config.get("max_episodes")
    sampled = bool(max_episodes and len(episodes) > max_episodes)
    selected = episodes[:int(max_episodes)] if sampled else episodes
    frame_task_indexes = {}
    if any(not episode.get("task") and not episode.get("tasks") for episode in selected):
        try:
            frame_task_indexes = episode_task_indexes(folder)
        except ValueError as exc:
            results.extend({"id": rule["id"], "name": rule["name"], "status": "ERROR",
                            "episodes": [], "reason": str(exc)[:500]} for rule in active)
            return {"status": "ERROR", "results": results}
    timeout = int(config["timeout_seconds"])
    staging = root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"qc-{task_id}-", dir=staging) as temp:
        clip_cache: dict[tuple[int, str], tuple[Path, dict] | Exception] = {}
        for rule in active:
            camera = rule["camera_key"] or next(iter(snapshot["robot"]["video_keys"]), "")
            judged = []
            for episode in selected:
                episode_id = int(episode.get("episode_index", 0))
                cache_key = (episode_id, camera)
                try:
                    if not camera:
                        raise ValueError("规则未配置相机")
                    if cache_key not in clip_cache:
                        destination = Path(temp) / f"ep-{episode_id:06d}-{len(clip_cache)}.mp4"
                        try:
                            clip_cache[cache_key] = _video_segment(
                                folder, info, episode, camera, destination, timeout
                            )
                        except Exception as exc:  # noqa: BLE001 - reuse clip failure across rules
                            clip_cache[cache_key] = exc
                    clip = clip_cache[cache_key]
                    if isinstance(clip, Exception):
                        raise clip
                    clip_file, evidence = clip
                    task_text = episode_task_text(episode, task_names, frame_task_indexes)
                    answer = _ask_model(base_url, token, config["model"], rule,
                                        task_text or "未填写", clip_file, timeout,
                                        int(config["max_tokens"]))
                    judged.append({"episode": episode_id, "status": answer["result"].upper(),
                                   "reason": answer["reason"], "evidence": answer["evidence"],
                                   "video": evidence})
                except Exception as exc:  # noqa: BLE001 - continue with other episodes
                    judged.append({"episode": episode_id, "status": "ERROR",
                                   "reason": str(exc)[:500], "evidence": "",
                                   "video": {"camera": camera}})
            statuses = {row["status"] for row in judged}
            status = ("FAIL" if "FAIL" in statuses else "ERROR" if "ERROR" in statuses
                      else "REVIEW" if "UNCERTAIN" in statuses else "PASS")
            results.append({"id": rule["id"], "name": rule["name"], "kind": rule["kind"],
                            "status": status, "episodes": judged})
    active_statuses = {row["status"] for row in results if row["status"] != "SKIPPED"}
    status = ("FAIL" if "FAIL" in active_statuses else "ERROR" if "ERROR" in active_statuses
              else "REVIEW" if "REVIEW" in active_statuses else "PASS")
    return {"status": status, "sampled": sampled, "checked_episodes": len(selected),
            "total_episodes": len(episodes), "model": config["model"], "results": results}
