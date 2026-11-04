# MySQL 表结构版本

`mysql/001_initial.sql` 创建二阶段全新的平台管理元数据表，版本记录保存在
MySQL 的 `schema_migrations`。启动时对尚未初始化的独立数据库执行，之后按版本检查。

本目录保存可提交的数据库结构脚本，不保存数据库文件、备份、真实数据或密钥。
现有 `storage/system/platform.sqlite` 不导入 MySQL，也不由本目录脚本修改。
`registry.sqlite3` 不在此范围内。
