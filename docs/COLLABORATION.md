# HKC 数采平台协作约定

更新日期：2026-11-11

本文只记录当前协作方式和最近一次交接，不保存旧版本流水账。架构规则以
`AGENTS.md` 为准，功能状态以 `docs/CODEX_CURRENT_STATE.md` 为准。

## 1. 当前共同基线

- 分支：`integration/hs-lxp-v3.2.0-cq`；
- 应用：FastAPI + MySQL 8.4；
- 数据格式：LeRobot v3；
- 训练：宿主执行器调用独立 X-VLA Docker；
- 旧 SQLite 和历史分支不再作为当前实现依据。

开始工作前：

```bash
git status --short --branch
git log -1 --oneline --decorate
```

已有未提交改动必须保留。未经使用者授权，不提交、不推送、不合并、不部署。

## 2. 默认维护范围

Heisan：

- 网页上传与分布式采集工具；
- 数据标注；
- 数据处理和视频裁剪；
- Docker、Compose、局域网和服务器部署。

LXP：

- 数据管理、详情和回收站页面；
- 质检报告展示；
- 格式转换与导出；
- X-VLA 训练接入。

共同评审：

- `app/db/` 和 `migrations/`；
- DS/RB/CV/TR 编号与目录解析；
- 质检状态和人工审核；
- `app/main.py`、`app/templates/base.html`；
- `pyproject.toml`、Dockerfile、Compose 和存储规范。

跨范围修改时必须说明原因、接口影响和验证方法。

## 3. 共享接口

### 数据集

- 对外主标识始终使用 DS 编号；
- 真实路径通过 MySQL 的 `storage_area + storage_relpath` 解析；
- 数据集显示名称唯一，比较忽略首尾空格和英文大小写；
- 发布新 DS 必须使用统一发布服务，不直接写登记表；
- 源 DS 不原地修改；
- 回收站 DS 仍保留登记和机器人关联，永久清除后才清除关系。

### 机器人

- 一个 DS 最多关联一台机器人，一台机器人可关联多个 DS；
- 派生 DS 只有在所有来源属于同一机器人时自动继承；
- 停用机器人不能接收新关联或 Token 上传；
- 网页列表只能获取 Token 前缀；完整值仅在创建/重新生成响应或部署终端显示。

### 质检

- 新 DS 默认不可用、待质检；
- 活跃质检方案名称唯一；
- 自动检查完成后仍需人工审核；
- PASS 标记可用，RECAPTURE 标记不可用；
- 质检报告文件和 MySQL 任务/审核记录是两类不同信息。

### 文件与配置

- 业务代码只通过 `settings.data_root` 访问数据目录；
- `HKC_STORAGE_PATH` 与 `HKC_MYSQL_DATA_PATH` 分开；
- 不提交真实数据、密码、Token、环境文件、模型或日志；
- 通用配置不写死个人路径、IP、临时代理或镜像源。

## 4. 容易冲突的文件

修改前先确认另一位开发者没有同时工作：

- `app/main.py`、`app/web.py`；
- `app/templates/base.html`；
- `app/core/config.py`、`app/core/paths.py`；
- `app/db/`、`migrations/mysql/`；
- `app/modules/datasets/lerobot_v3.py`；
- `compose*.yaml`、`deploy/compose*.yaml`、`Dockerfile`；
- `pyproject.toml`、`.gitignore`、`.dockerignore`；
- 根 README、当前状态和协作文档。

不要为了整理顺手修改不属于当前任务的共享文件。

## 5. 分支和合并

1. 从最新共同分支开始；
2. 先获取远程更新并确认工作区；
3. 并行任务使用范围明确的功能分支或独立 worktree；
4. 提交前运行相关测试；
5. 共享接口变更由双方评审；
6. 集成分支验收后再决定是否进入 `main`。

禁止使用 `git reset --hard`、`git clean` 或强制推送处理他人改动。

## 6. 验证与汇报

最低验证按改动范围选择：

```bash
python -m pytest -q -p no:cacheprovider
python -m ruff check app tests tools
git diff --check
```

Compose、JavaScript、真实文件和服务器变更还要执行对应专项验证。汇报必须区分：

- 代码已实现；
- 自动化测试已通过；
- 本机人工验收已通过；
- 服务器部署与验收已通过。

## 7. 最近交接

日期：2026-11-11

分支：`integration/hs-lxp-v3.2.0-cq`

本地已完成：

- 回收站 DS 继续显示在机器人关联列表，并标记“回收站”；永久清除后关系消失；
- 新增采集端上传标记清理工具；
- 数据集显示名称不可重复；
- 活跃质检方案名称不可重复；
- 精简并重写项目 Markdown 文档。

名称规则：

- 忽略首尾空格和英文大小写；
- 回收站中的 DS 仍占用显示名称；
- 既有重复记录不会自动改名或删除；
- 自动上传工具使用文件夹名或 ZIP 去扩展名作为显示名称；
- 旧单 ZIP 工具支持 `--name`。

验证状态：

- 完整 pytest：`161 passed`；
- 本轮相关文件 Ruff：通过；
- 全仓 Ruff 有日志旧模块 5 个既有 E741；
- 尚未提交、推送或部署重庆服务器。

下一步：

1. 人工验证名称冲突、回收站机器人关联和上传标记清理；
2. 处理发现的问题；
3. 提交并推送当前分支；
4. 更新服务器代码和镜像；
5. 服务器验收分布式上传、回收站关联和 X-VLA 训练；
6. 验收后确定下一阶段稳定基线。

## 8. 后续交接模板

```text
日期：
开发者 / 分支：
完成内容：
主要修改文件：
执行的验证：
未完成或已知问题：
是否修改共享接口：
是否已提交、推送或部署：
下一步建议：
```

每次只保留最近一次有效交接。更早历史通过 Git 提交记录查询，不再追加到本文。
