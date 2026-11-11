# MySQL 迁移

平台启动时由 `python -m app.cli.init_mysql` 按编号执行
`migrations/mysql/*.sql`。已执行版本记录在 `schema_migrations`。

## 当前迁移

| 版本 | 内容 |
| --- | --- |
| `001_initial.sql` | 用户、DS、机器人、质检、转换、训练和兼容词表基础表 |
| `002_user_profiles.sql` | 显示名称、首次改密和密码修改时间 |
| `003_dataset_availability.sql` | `data` 存储区、可用性和状态标签 |
| `004_dataset_trash.sql` | 数据集回收状态 |
| `005_robot_api_tokens.sql` | 机器人机器上传 Token |

## 规则

- 新结构使用更高编号的新文件；
- 已发布、已执行的 SQL 不得修改；
- 迁移必须可在已有数据上安全执行；
- 迁移目录不存放数据库文件、备份、数据集、密码或 Token；
- 表结构变更需同步仓储层、测试和文档；
- 正式部署前先备份 MySQL；
- 测试只在隔离数据库中运行迁移。
