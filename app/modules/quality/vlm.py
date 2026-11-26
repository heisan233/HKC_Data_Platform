"""Episode-aligned video judgements through the existing OpenAI-compatible VLM API."""

from __future__ import annotations

import base64
import json
import logging
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
MAX_SUMMARY_CHARS = 80
_logger = logging.getLogger(__name__)


def _clean_summary(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return ""
    sentences = [item.strip() for item in re.split(r"(?<=[。！？.!?])\s+", text)
                 if item.strip()]
    unique = []
    for sentence in sentences:
        if sentence.casefold() not in {item.casefold() for item in unique}:
            unique.append(sentence)
    text = " ".join(unique)
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS].rstrip("，,；;。.!！?") + "…"
    return text


def _is_chinese_summary(value: str) -> bool:
    chinese = len(re.findall(r"[\u3400-\u9fff]", value))
    latin = len(re.findall(r"[A-Za-z]", value))
    return chinese >= 4 and chinese >= latin


def _fallback_summary(status: str) -> str:
    return {
        "PASS": "视频内容符合该项检查要求。",
        "FAIL": "视频内容未通过该项检查，请查看问题视频片段并人工复核。",
        "UNCERTAIN": "视频证据不足，无法确认是否符合要求，请人工复核。",
        "ERROR": "该 Episode 检查执行失败，请查看任务信息并人工复核。",
    }.get(status.upper(), "VLM 结果需要人工复核。")


def display_summary(episode: dict) -> str:
    """Return one concise Chinese sentence for report pages, including old reports."""
    value = episode.get("summary") or episode.get("reason") or ""
    summary = _clean_summary(value) if isinstance(value, str) else ""
    return summary if _is_chinese_summary(summary) else _fallback_summary(
        str(episode.get("status") or "UNCERTAIN")
    )


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
    summary = data.get("summary")
    if not isinstance(summary, str):
        summary = data.get("reason")
    if not isinstance(summary, str):
        raise TypeError("模型 JSON 缺少 summary")
    summary = _clean_summary(summary)
    if not summary:
        raise ValueError("模型 JSON 的 summary 为空")
    return {"result": data["result"], "summary": summary,
            "reason": summary, "evidence": ""}


def _ask_model(base_url: str, token: str, model: str, rule: dict, task: str,
               clip: Path, timeout: int, max_tokens: int) -> dict:
    video = base64.b64encode(clip.read_bytes()).decode("ascii")
    prompt = (f"机器人采集任务：{task}\n判断要求：{rule['prompt']}\n"
              "只依据视频中可见的内容判断；看不清或无法确认时返回 uncertain。"
              "结论必须使用简体中文，只写一个不重复的完整句子，最多80个汉字。"
              "仅输出 JSON：{\"result\":\"pass|fail|uncertain\","
              "\"summary\":\"简短中文结论\"}。")

    def request(content: list[dict]) -> dict:
        payload = {"model": model, "temperature": 0, "max_tokens": max_tokens,
                   "messages": [{"role": "user", "content": content}]}
        response = client.post(base_url.rstrip("/") + "/chat/completions", json=payload,
                               headers={"Authorization": f"Bearer {token or 'EMPTY'}"})
        response.raise_for_status()
        response_content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(response_content, str):
            raise TypeError("模型返回内容不是文本")
        return _json_answer(response_content)

    with httpx.Client(timeout=timeout, trust_env=False) as client:
        answer = request([
            {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + video}},
            {"type": "text", "text": prompt},
        ])
        if _is_chinese_summary(answer["summary"]):
            return answer
        rewrite = (
            "将下面的视频质检结论改写为简体中文，只保留一个不重复的完整句子，"
            "最多80个汉字。result 必须保持不变。仅输出 JSON："
            "{\"result\":\"pass|fail|uncertain\",\"summary\":\"简短中文结论\"}。\n"
            f"原结果：{json.dumps(answer, ensure_ascii=False)}"
        )
        try:
            rewritten = request([{"type": "text", "text": rewrite}])
            if (rewritten["result"] == answer["result"]
                    and _is_chinese_summary(rewritten["summary"])):
                return rewritten
        except Exception:
            _logger.debug("VLM 中文摘要重写失败，使用固定中文说明", exc_info=True)
    summary = _fallback_summary(answer["result"].upper())
    return {"result": answer["result"], "summary": summary,
            "reason": summary, "evidence": ""}


def episode_tasks(folder: Path) -> dict[int, str]:
    """Read task labels for report display without trusting report-provided text."""
    episodes = _episode_rows(folder)
    _, task_names = load_tasks(folder)
    frame_task_indexes = {}
    if any(not episode.get("task") and not episode.get("tasks") for episode in episodes):
        frame_task_indexes = episode_task_indexes(folder)
    return {
        int(episode.get("episode_index", 0)): (
            episode_task_text(episode, task_names, frame_task_indexes) or "未填写"
        )
        for episode in episodes
    }


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
                task_text = episode_task_text(episode, task_names, frame_task_indexes) or "未填写"
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
                    answer = _ask_model(base_url, token, config["model"], rule,
                                        task_text, clip_file, timeout,
                                        int(config["max_tokens"]))
                    judged.append({"episode": episode_id, "status": answer["result"].upper(),
                                   "task": task_text, "summary": answer["summary"],
                                   "reason": answer["reason"], "evidence": answer["evidence"],
                                   "video": evidence})
                except Exception as exc:  # noqa: BLE001 - continue with other episodes
                    judged.append({"episode": episode_id, "status": "ERROR",
                                   "task": task_text,
                                   "summary": _fallback_summary("ERROR"),
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
