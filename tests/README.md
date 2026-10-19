# 测试目录

测试按照 `app/modules/` 的业务结构组织。单元测试放在 `tests/unit/`，HTTP 和
文件存储集成测试放在 `tests/integration/`，可安全提交的小型 LeRobot 样本放在
`tests/fixtures/`。真实业务数据集不能放进测试目录。

当前没有数据库测试；未来启用数据库后，再把相应集成测试加入
`tests/integration/`。
