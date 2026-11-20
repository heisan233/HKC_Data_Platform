# LaWAM 独立 Docker 运行时

该运行时把重庆服务器原有 `/home/zzjj/wam/env` 迁移为与其他模型并列的独立环境，
服务器目录为 `/srv/hkc-ai/models/LaWAM`。

目录约定：

- `source/`：原环境使用的定制 LeRobot 0.6.2 源码快照；
- `env/`：原 `/home/zzjj/wam/env` conda 环境快照，在镜像内保持同一绝对路径；
- `weights/lawam-pretrain/`：LaWAM 基座权重；
- `weights/Qwen3-VL-2B-Instruct/`：LaWAM 的视觉语言底座；
- `datasets/libero/`、`assets/libero/`、`config/libero/`：LIBERO 数据和仿真资源；
- `work/`、`cache/`：容器可写工作区和缓存；
- 平台 `storage/training-runs/lawam/`：后续 LaWAM 训练产物；
- `/mnt/data/wam-runs`：迁移前的历史训练产物，只读挂载供旧 checkpoint 使用。

镜像构建直接封装已实测的 conda 环境，不重新解析 Python 依赖；运行时设置
Hugging Face 和 Transformers 离线模式。
权重、数据集、仿真资产和历史训练产物只读挂载。容器内通过
`WAM_RUNS_ROOT=/data/training-runs/lawam` 将新训练产物写入平台 storage。
平台任务由执行器设置 `LAWAM_DATASET_ROOT`，将所选 `storage/data/<DS 编号>/`
只读挂入 `/datasets/selected`。训练数据需通过平台审核和 LaWAM 训前检查。
服务器默认宿主机目录为：

```text
/media/zzjj/A21E6DDF1E6DACCB/vscode_project/HKC_Data_Platform/storage/training-runs/lawam
```

如平台 storage 迁移到其他位置，可通过 `LAWAM_OUTPUT_ROOT` 覆盖宿主机来源目录。

```bash
cd /srv/hkc-ai/models/LaWAM/docker
docker compose --profile manual config
docker compose --profile manual build lawam
docker compose --profile manual run --rm lawam python -c \
  "import torch; from lerobot.policies.lawam.modeling_lawam import LaWAMPolicy; print(torch.__version__, torch.cuda.is_available(), LaWAMPolicy.__name__)"
```

重庆服务器已使用 RTX 4090 验证以下项目：

- 容器可导入 LaWAM、LIBERO、MuJoCo 和 robosuite；
- CUDA 可用，LaWAM 基座权重可完整加载到 GPU；
- `/mnt/data/wam-runs` 中已有训练检查点可只读加载；
- 新训练输出写入平台 `storage/training-runs/lawam/`；
- EGL 可在无窗口环境中渲染 MuJoCo 画面。

原 `/home/zzjj/wam` conda 环境保留为回退副本，现有 18001 训练台进程继续运行，
不会因构建或按次运行容器而切换。

平台 WAM 页的任务提交、日志和评测通过独立宿主执行器完成；配置方式见
`deploy/LAWAM_EXECUTOR.md`。
