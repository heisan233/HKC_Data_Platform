# 平台存储目录

这里保存数据集、处理结果和平台运行状态，容器内统一映射为 `/data`。除目录中的
`.gitkeep` 和本说明外，所有内容都被 Git 忽略。

## 当前存储方案

数据集和质检报告保存在普通文件夹。数据管理使用
`system/platform.sqlite` 保存登记编号、展示信息及 Raw 目录映射；大型数据文件
不进入数据库。质检页面的 ZIP 上传先在 `staging/` 校验，随后进入 `raw/` 并自动登记；
其他流程放入 `raw/` 的数据仍可在数据管理页面登记。

宿主机上的本目录会被 Docker 映射为容器内的 `/data`：

```text
项目中的 ./storage  ->  容器中的 /data
```

所以业务代码应读取配置提供的存储根目录，不能写死
`/home/heisan/.../storage` 这样的个人路径。

## 子目录用途

- `raw/`：原始数据集；质检页 ZIP 上传后自动登记，其他流程放入的目录可手动登记。
- `detected/`：预留给未来实际清洗后的数据集；当前质检不复制 Raw。
- `staging/`：上传、校验和安全解压期间的临时内容。
- `derived/`：裁剪、合并、拆分、去重或修改 task 后生成的新版本；导出页从这里选择 LeRobot v3.0 转换来源，也可直接打包下载。
- `converted/`：格式转换输出；每个成功任务生成一个 `CV-*` 目录，包含 HDF5 分块和 `manifest.json`，可直接打包下载。
- `exports/`：预留给需要落盘的打包结果；当前导出页从 `derived/` 或 `converted/` 即时生成 tar.gz 下载，不保留副本。
- `reports/qc/<DS 编号>/`：每个质检任务的一份三部分统一报告。
- `models/`：模型定义、权重或发布包；首期暂不使用。
- `training-runs/`：训练日志、指标和 checkpoint；首期暂不使用。
- `system/`：平台运行数据目录；其中 `platform.sqlite` 是 SQLite 数据库，保存数据集登记、质检任务和人工审核记录。数据库文件不提交 Git。

不要把 Python、HTML、配置文件或 Dockerfile 放在此目录。测试用小样本应放在
`tests/fixtures/`，并确认其许可和体积适合进入 Git。
