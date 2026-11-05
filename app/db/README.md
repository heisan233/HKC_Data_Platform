# `app/db` 数据库访问层

二阶段平台元数据使用 MySQL 8.4/InnoDB。`mysql.py` 负责连接、事务和顺序迁移，
表结构脚本位于 `migrations/mysql/`。应用启动命令先执行
`python -m app.cli.init_mysql`；仓储调用 `initialize()` 时也会幂等检查结构。

- `datasets.py`：DS 编号、Raw/Derived 位置、展示信息和来源关系；
- `conversions.py`：CV 转换任务状态；
- `quality.py`：质检方案、版本、任务、运行控制和人工审核；
- `users.py`：无默认网页用户的账号、显示名称、首次改密状态、scrypt 密码哈希和
  服务端会话摘要；
- `robots.py`、`dataset_robots.py`：机器人档案和 DS 关联；
- `training.py`：训练任务、评估和模型包管理元数据；
- `semantics.py`：旧受控词表接口保留，当前 Schema 页面不使用它。

大型数据集、报告、模型与导出文件仍在 `HKC_STORAGE_PATH` 对应的普通文件夹。
MySQL 数据文件由 MySQL 容器管理，宿主目录由 `HKC_MYSQL_DATA_PATH` 指定，
与 `storage/` 分开。原有 `storage/system/platform.sqlite` 不读取、不导入、不删除。
`registry.sqlite3` 由 HS 负责，不在本数据库层范围内。

连接配置使用 `HKC_MYSQL_HOST`、`HKC_MYSQL_PORT`、`HKC_MYSQL_DATABASE`、
`HKC_MYSQL_USER`，密码从 `HKC_MYSQL_PASSWORD_FILE` 或 `HKC_MYSQL_PASSWORD` 读取。
缺少密码或 MySQL 不可用时启动失败，不回退 SQLite。
