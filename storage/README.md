# 平台文件存储

宿主机 `HKC_STORAGE_PATH` 映射为应用容器 `/data`。这里保存大型文件和运行产物；
MySQL 只保存编号、目录映射、关系、任务和审核元数据。

## 目录

| 目录 | 内容 |
| --- | --- |
| `data/DS000001/` | 新上传或新发布的完整 LeRobot v3 数据集 |
| `raw/` | 历史原始数据集目录映射 |
| `derived/` | 历史派生数据集目录映射 |
| `staging/` | 上传、标注、处理、转换和导出的临时内容 |
| `reports/qc/<DS>/` | 当前质检 JSON 报告 |
| `converted/<CV>/` | HDF5 转换结果，不占用 DS 编号 |
| `exports/` | DS 或 CV 的导出压缩包 |
| `training-runs/<TR>/` | 训练状态、日志、checkpoint 和评估 |
| `models/` | 已验证模型包 |
| `trash/datasets/<DS>/` | 数据集回收站 |
| `trash/reports/<DS>/` | 单独移入的报告回收站 |
| `system/logs/` | 脱敏访问日志 |
| `system/collector-claims/` | 采集 ZIP 摘要与 DS/RB 去重旁路记录 |

真实运行内容不提交 Git。

## 数据集规则

- 新 DS 一个编号对应一个完整目录；
- 新结果先在 `staging/` 构建，校验后发布到 `data/<DS>/`；
- 旧 `raw/`、`derived/` 保持原路径，不自动迁移；
- 源 DS 不原地修改；
- 存储区只表示物理位置，不代表是否可用；
- 新 DS 默认不可用、待质检；
- 标注、处理和数据定义发布的新 DS 必须重新质检。

## 回收站

普通删除只移动到回收站。恢复时返回原登记位置；永久清除会删除文件和相应管理记录，
但 DS 编号不复用。

存在派生 DS、转换任务、训练任务或运行中任务引用时，永久清除会被拒绝。回收站 DS
在机器人管理页继续显示历史关联，永久清除后关联才消失。

## MySQL 分离

`HKC_MYSQL_DATA_PATH` 映射到 MySQL 容器 `/var/lib/mysql`，必须与
`HKC_STORAGE_PATH` 分开。MySQL 目录应放在 Linux 原生文件系统，不能混入本目录。

旧 `system/platform.sqlite` 如存在只作历史文件保留，当前平台不读取或导入。

完整备份必须同时覆盖 storage 和 MySQL；只备份一方不能完整恢复平台。
