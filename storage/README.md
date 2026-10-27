# 平台存储目录

这里保存数据集、处理结果和平台运行状态，容器内统一映射为 `/data`。除目录中的
`.gitkeep` 和本说明外，所有内容都被 Git 忽略。

## 当前存储方案

数据集和质检报告保存在普通文件夹。数据管理使用
`system/platform.sqlite` 保存登记编号、展示信息、来源关系及 Raw/Derived/Converted 目录映射；大型数据文件
不进入数据库。质检页面的 ZIP 上传先在 `staging/` 校验，随后进入 `raw/` 并自动登记；
其他流程放入 `raw/` 的数据仍可在数据管理页面登记。

宿主机上的本目录会被 Docker 映射为容器内的 `/data`：

```text
HKC_STORAGE_PATH（默认 ./storage） -> 容器中的 /data
```

服务器可在 `.env` 中把 `HKC_STORAGE_PATH` 设置成独立磁盘上的绝对路径；更换该
路径不会改变容器内路径或业务代码。目录所有者的数字 UID/GID 应与 `.env` 中的
`APP_UID`/`APP_GID` 一致。

所以业务代码应读取配置提供的存储根目录，不能写死
`/home/heisan/.../storage` 这样的个人路径。

## 子目录用途

- `raw/`：上传成功后的原始数据，只读、不可原地修改。真实目录名与 DS 编号相互独立。
- `staging/`：上传、校验、安全解压或派生版本发布期间的临时内容。标注发布临时
  副本位于 `staging/annotation/`，数据处理临时输出位于 `staging/processing/`；
  格式转换临时输出位于 `staging/conversion/`；成功或失败后都会清理。
- `derived/`：裁剪、合并、拆分、去重或标注后生成的新版本；成功发布时会自动登记
  新的 DS 编号并记录源 DS 编号。一次正式标注发布会
  在这里创建一个完整且独立的 LeRobot v3 数据集副本。总体 task 按 LeRobot
  标准字段更新；Episode 结果和 Subtask 时间段存放在副本的
  `meta/annotations/episodes.parquet` 与 `subtasks.parquet`，不污染核心 feature。
- `converted/`：格式转换输出。每个成功任务一个 `CV-.../` 目录，包含 HDF5
  分块和 `manifest.json`；结果通过 CV 任务编号展示和导出，不进入 DS 登记表，来源 Derived 数据集不原地修改。
- `exports/`：供用户或机器人下载的 `tar.gz` 打包结果。导出先写入
  `staging/export/`，完成后原子发布；同一数据集文件快照复用已有包。
- `reports/qc/<DS 编号>/`：每个质检任务的统一报告；质检结果和人工审核
  状态由 SQLite 管理，不因通过质检而复制数据集。
- `trash/reports/<DS 编号>/`：从页面移除的质检报告回收区；保存报告原文件和隐藏
  元数据（原路径、移入时间），支持页面恢复，不提供不可恢复的清理。
- `models/`：通过验证的训练模型包；X-VLA 基座权重由宿主执行器另行配置，不放在此目录。
- `training-runs/`：训练任务状态、日志与离线评估结果；临时 checkpoint 工作目录
  由宿主执行器单独配置，不能放在不支持符号链接的 exFAT 数据盘上。
- `system/`：平台运行状态。`platform.sqlite` 保存数据集登记、质检任务和人工
  审核记录；上传记录位于
  `system/upload-records/<upload_id>.json`；标注派生版本记录位于
  `system/dataset-versions/<dataset_id>.json`；数据处理任务记录位于
  `system/processing-jobs/<job_id>.json`，其派生版本仍登记到
  `system/dataset-versions/`。SQLite 和这些运行记录都不提交 Git。
  格式转换和训练任务记录保存在 `system/platform.sqlite`。旧版
  `system/conversion-jobs/<CV 编号>.json` 在首次访问转换页时复制入库并原样保留。

不要把 Python、HTML、配置文件或 Dockerfile 放在此目录。测试用小样本应放在
`tests/fixtures/`，并确认其许可和体积适合进入 Git。

平台不会启动时自动扫描或迁移 `raw/`、`derived/` 中已有的测试目录。需要继续
使用的 Raw 目录可在数据管理页手动登记；新上传和新派生结果会自动登记。
