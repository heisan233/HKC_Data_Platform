# MySQL 数据访问层

`app/db/` 只保存 MySQL 访问代码，不保存数据库文件或 VLA 数据集。

## 运行方式

平台使用 MySQL 8.4/InnoDB。连接信息来自环境变量：

- `HKC_MYSQL_HOST`、`HKC_MYSQL_PORT`；
- `HKC_MYSQL_DATABASE`；
- `HKC_MYSQL_USER`、`HKC_MYSQL_PASSWORD`。

`app/db/mysql.py` 提供连接、事务和迁移初始化。缺少 MySQL 时平台不会回退 SQLite。
旧 `platform.sqlite` 不自动导入，也不再读取。

## 仓储职责

| 文件 | 职责 |
| --- | --- |
| `datasets.py` | DS 编号、目录映射、可用性、标签、来源和回收状态 |
| `dataset_robots.py` | DS 与机器人关联 |
| `robots.py` | RB 编号和机器人档案 |
| `robot_tokens.py` | 每台机器人唯一上传 Token |
| `quality.py` | 质检方案、版本、任务和人工审核 |
| `conversions.py` | CV 转换任务 |
| `training.py` | TR 训练、评估和模型包元数据 |
| `users.py` | 用户、密码摘要和服务端会话 |
| `semantics.py` | 旧词表兼容表；当前 Schema 页面不依赖它 |

数据集显示名称和活跃质检方案名称必须唯一；比较时忽略首尾空格和英文大小写。

## 表结构变更

所有结构升级放入 `migrations/mysql/`，按编号顺序幂等执行。已发布迁移不得改写，
应新增更高编号迁移。详见 `migrations/README.md`。

## 边界

- MySQL 只保存管理元数据；视频、Parquet、报告和模型仍位于 `storage/`；
- 数据集真实路径必须经过统一 DS 解析，不能自行拼接用户输入；
- Token、密码和连接字符串不得写入源码、测试样本或文档；
- 测试必须使用隔离 MySQL，禁止连接正式数据库。
