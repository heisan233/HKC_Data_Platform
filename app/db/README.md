# `app/db` 目录说明

本目录保存数据管理使用的 SQLite 访问代码。

- 当前数据集保存在项目根目录的 `storage/` 中。
- `datasets.py` 管理 Raw/Derived 数据集的统一 DS 编号、登记信息、来源关系和目录映射。
- `conversions.py` 管理转换任务状态；转换结果由 CV 任务编号关联 `converted/` 目录，不进入 DS 登记表。旧版转换登记表若已存在，会保留但不再读写。
- 数据库文件是运行数据，位于 `storage/system/platform.sqlite`，不提交 Git。
- 原始和派生数据文件分别留在 `storage/raw/`、`storage/derived/`；质检任务和人工审核记录写入 SQLite，报告文件保存在 `storage/reports/qc/`。

这里保存“如何操作数据库”的代码，而不是数据库文件本身。
