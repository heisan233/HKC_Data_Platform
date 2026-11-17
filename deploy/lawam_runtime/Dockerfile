FROM python:3.12-slim-bookworm

ARG DEBIAN_MIRROR=http://mirrors.aliyun.com

LABEL org.opencontainers.image.title="HKC LaWAM training runtime" \
      org.opencontainers.image.description="LaWAM and LIBERO runtime on LeRobot 0.6.2 with CUDA 13.0"

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MUJOCO_GL=egl \
    PYOPENGL_PLATFORM=egl \
    TOKENIZERS_PARALLELISM=false

RUN sed -i "s#http://deb.debian.org#${DEBIAN_MIRROR}#g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        libegl1 \
        libgl1 \
        libglib2.0-0 \
        libglfw3 \
        libosmesa6 \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/home/zzjj \
    WAM=/home/zzjj/wam \
    PATH=/home/zzjj/wam/env/bin:${PATH} \
    LD_LIBRARY_PATH=/home/zzjj/wam/env/lib

COPY env/ /home/zzjj/wam/env/
COPY source/ /home/zzjj/wam/code/lerobot-0.6.2/

RUN python -c "import torch, transformers; from lerobot.policies.lawam.modeling_lawam import LaWAMPolicy; import libero, mujoco, robosuite; print(torch.__version__, transformers.__version__, LaWAMPolicy.__name__)"

WORKDIR /work
CMD ["sleep", "infinity"]
