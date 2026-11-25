"""Create a short browser-compatible preview without changing the source video."""

import hashlib
import subprocess
from pathlib import Path
from uuid import uuid4


class PreviewError(Exception):
    pass


def create_preview(root: Path, dataset_id: str, source: Path, *,
                   start_seconds: float = 0, duration_seconds: float = 120) -> Path:
    stat = source.stat()
    key = hashlib.sha256(
        f"{source.relative_to(root)}:{stat.st_size}:{stat.st_mtime_ns}:"
        f"{start_seconds:.3f}:{duration_seconds:.3f}".encode()
    ).hexdigest()
    cache = root / "system" / "ego-previews" / dataset_id
    if any(path.is_symlink() for path in (root / "system", root / "system" / "ego-previews", cache)):
        raise PreviewError("预览缓存目录不安全")
    cache.mkdir(parents=True, exist_ok=True)
    output = cache / f"{key}.mp4"
    if output.is_file() and output.stat().st_size:
        return output
    temporary = cache / f".{key}.{uuid4().hex}.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-ss", f"{start_seconds:.3f}", "-i", str(source),
             "-t", f"{duration_seconds:.3f}", "-map", "0:v:0", "-an",
             "-vf", "scale=w=1280:h=720:force_original_aspect_ratio=decrease:force_divisible_by=2",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
             "-pix_fmt", "yuv420p", "-threads", "2", "-movflags", "+faststart",
             str(temporary)],
            check=True, timeout=180, capture_output=True,
        )
        if not temporary.is_file() or not temporary.stat().st_size:
            raise PreviewError("视频转码未生成预览")
        temporary.replace(output)
        return output
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise PreviewError("无法生成浏览器兼容预览") from exc
    finally:
        temporary.unlink(missing_ok=True)
