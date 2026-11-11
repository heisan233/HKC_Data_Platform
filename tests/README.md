# 自动化测试

测试使用 pytest，并为每个测试创建独立 MySQL 数据库和临时 storage。禁止把测试
连接到正式 MySQL 或真实 `storage/`。

## 必需环境变量

```text
HKC_TEST_MYSQL_HOST
HKC_TEST_MYSQL_ROOT_PASSWORD
HKC_TEST_MYSQL_PORT（可选，默认 3306）
```

root 权限只用于创建和删除每个测试的临时数据库。测试结束后自动清理。

## 命令

完整测试：

```bash
python -m pytest -q -p no:cacheprovider
```

定向测试：

```bash
python -m pytest -q -p no:cacheprovider tests/integration/test_ingestion.py
```

代码规则：

```bash
python -m ruff check app tests tools
git diff --check
```

当前全仓 Ruff 在 `app/modules/logs/router.py` 有 5 个既有 E741 命名问题；新改动
不应新增问题。本轮相关文件应单独通过 Ruff。

## 覆盖范围

- `tests/unit/`：纯函数、CLI 和采集端工具；
- `tests/integration/`：路由、MySQL、临时数据集、文件发布和页面行为；
- `tests/quality_helpers.py`：测试专用质检通过辅助；
- `tests/conftest.py`：隔离 MySQL、临时 storage 和认证夹具。

涉及 DB、DS 发布、共享 LeRobot 读取或回收站时应运行完整测试。只修改单一页面时可
先跑定向测试，但提交前仍需按影响范围补足验证。

不要把真实业务数据复制进测试目录；需要视频或 Parquet 时由测试动态生成小型样本。
