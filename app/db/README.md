# `app/db` 目录说明

本目录保存数据管理使用的 SQLite 访问代码。

- 当前数据集保存在项目根目录的 `storage/` 中。
- `datasets.py` 管理统一 DS 编号、登记信息、派生来源以及 Raw/Derived 目录映射。
- `users.py` 管理用户、scrypt 密码哈希和只保存令牌摘要的登录会话。
- `robots.py` 管理稳定 RB 编号、机器人档案、状态和传感器摘要；不保存机器人运行
  遥测，也不直接修改数据集。
- `semantics.py` 管理稳定 SD 编号，以及场景、任务、动作、对象四类受控词表；
  词表不自动改写已有数据集。
- 管理员使用 `python -m app.cli.create_user` 交互创建用户，使用
  `python -m app.cli.list_users` 查看不含认证秘密的账号摘要。
- 数据库文件是运行数据，位于 `storage/system/platform.sqlite`，不提交 Git。
- 原始和派生数据文件分别留在 `storage/raw/`、`storage/derived/`；质检任务和人工审核记录写入 SQLite，报告文件保存在 `storage/reports/qc/`。

这里保存“如何操作数据库”的代码，而不是数据库文件本身。
