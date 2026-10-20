# 平台存储目录

这里保存数据集、处理结果和平台运行状态，容器内统一映射为 `/data`。除目录中的
`.gitkeep` 和本说明外，所有内容都被 Git 忽略。

## 当前存储方案

数据集和质检报告保存在普通文件夹。数据管理使用
`system/platform.sqlite` 保存登记编号、展示信息、来源关系及 Raw/Derived 目录映射；大型数据文件
不进入数据库。质检页面的 ZIP 上传先在 `staging/` 校验，随后进入 `raw/` 并自动登记；
其他流程放入 `raw/` 的数据仍可在数据管理页面登记。

宿主机上的本目录会被 Docker 映射为容器内的 `/data`：

```text
项目中的 ./storage  ->  容器中的 /data
```

所以业务代码应读取配置提供的存储根目录，不能写死
`/home/heisan/.../storage` 这样的个人路径。

## 子目录用途

- `raw/`：上传成功后的原始数据，只读、不可原地修改。真实目录名与 DS 编号相互独立。
- `staging/`：上传、校验、安全解压或派生版本发布期间的临时内容。标注发布临时
  副本位于 `staging/annotation/`，数据处理临时输出位于 `staging/processing/`；
  成功或失败后都会清理。
- `derived/`：裁剪、合并、拆分、去重或标注后生成的新版本；成功发布时会自动登记
  新的 DS 编号并记录源 DS 编号。一次正式标注发布会
  在这里创建一个完整且独立的 LeRobot v3 数据集副本。总体 task 按 LeRobot
  标准字段更新；Episode 结果和 Subtask 时间段存放在副本的
  `meta/annotations/episodes.parquet` 与 `subtasks.parquet`，不污染核心 feature。
- `converted/`：格式转换输出。
- `exports/`：供用户或机器人下载的打包结果。
- `reports/qc/<DS 编号>/`：每个质检任务的统一报告；质检结果和人工审核
  状态由 SQLite 管理，不因通过质检而复制数据集。
- `trash/reports/`：从页面移除的质检报告回收区；首期不做不可恢复的清理。
- `models/`：模型定义、权重或发布包；首期暂不使用。
- `training-runs/`：训练日志、指标和 checkpoint；首期暂不使用。
- `system/`：平台运行状态。`platform.sqlite` 保存数据集登记、质检任务和人工
  审核记录；上传记录位于
  `system/upload-records/<upload_id>.json`；标注派生版本记录位于
  `system/dataset-versions/<dataset_id>.json`；数据处理任务记录位于
  `system/processing-jobs/<job_id>.json`，其派生版本仍登记到
  `system/dataset-versions/`。SQLite 和这些运行记录都不提交 Git。

不要把 Python、HTML、配置文件或 Dockerfile 放在此目录。测试用小样本应放在
`tests/fixtures/`，并确认其许可和体积适合进入 Git。

平台不会启动时自动扫描或迁移 `raw/`、`derived/` 中已有的测试目录。需要继续
使用的 Raw 目录可在数据管理页手动登记；新上传和新派生结果会自动登记。
