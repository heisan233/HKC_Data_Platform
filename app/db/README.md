# `app/db` 目录说明

本目录保存数据管理使用的 SQLite 访问代码。

- 当前数据集保存在项目根目录的 `storage/` 中。
- `datasets.py` 管理编号、登记信息和目录映射。
- 数据库文件是运行数据，位于 `storage/system/platform.sqlite`，不提交 Git。
- 数据集文件始终留在 `storage/raw/` 和 `storage/detected/`，不写入数据库。

这里保存“如何操作数据库”的代码，而不是数据库文件本身。
