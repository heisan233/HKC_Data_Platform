# 测试目录

测试按照 `app/modules/` 的业务结构组织。单元测试放在 `tests/unit/`，HTTP 和
文件存储集成测试放在 `tests/integration/`，可安全提交的小型 LeRobot 样本放在
`tests/fixtures/`。真实业务数据集不能放进测试目录。

`tests/integration/test_data_management.py` 使用临时 `storage/` 和 SQLite 文件测试
目录扫描、编号登记、路径安全及报告关联，不访问真实业务数据。
