# 数据定义（Schema 编辑器）

`semantics` 读取已登记 LeRobot v3 数据集的真实结构，不再维护页面受控词表。页面
扫描 `meta/info.json`、全部 `meta/episodes/**/*.parquet` 和
`data/**/*.parquet`，同时展示字段来源、Arrow 物理类型、完整率及训练可用状态。

## Episode 字段

平台扩展定义写入：

```text
meta/info.json -> hkc_extensions.episode_features
```

实际值作为列写入每个 Episode 元数据 Parquet。第一版支持
`string / int64 / float64 / bool` 标量，以及“全部 Episode 使用同一值”或“全部为空”。

## 逐帧 Feature

逐帧字段同时写入：

1. `meta/info.json.features` 的 dtype、shape、names；
2. 所有 frame Parquet 的物理列；
3. `hkc_extensions.frame_feature_states` 的来源、nullable、完整率和状态；
4. 从 CSV/Parquet 导入真实数值时，在 `meta/stats.json` 写入非空值统计。

第一版支持 bool/int32/int64/float32/float64 标量与定长向量。全空字段是真实 nullable
物理列，不会自动填 0；该数据集会明确标记为“结构扩展完成，但不满足训练要求”。
CSV/Parquet 导入必须包含 `episode_index`、`frame_index` 和映射值列，向量可由多个
来源列组合。

图像、视频和深度媒体暂时只读展示，不在第一版新增或导入。未来媒体实现必须同时
处理媒体文件、路径模板、Episode 索引和编码信息，不能仅增加一个空 Parquet 列。

## 不可变发布与删除保护

- Raw 和已有 Derived 永不原地修改；
- 每次操作先完整复制到 `storage/staging/schema/`；
- 修改后重新读取 JSON 与全部 Parquet Schema；
- 验证成功后移入 `storage/derived/`，登记新 DS 并记录来源；
- `index / episode_index / frame_index / timestamp / task_index` 及 Episode 定位字段
  禁止删除；
- 平台新增字段可以删除；其他官方 Feature 必须经过醒目警告和二次确认；
- 删除数值 Feature 时同步清理 `info.json.features`、物理列和 `stats.json`。

旧 `app/db/semantics.py` 仅为已有 SQLite 兼容保留，当前页面不再读写它，也不会
破坏性删除旧表。

## 文件职责

- `router.py`：页面、JSON API 和最大 512 MB 导入文件的临时接收；
- `schemas.py`：字段名称、类型、shape 与删除确认请求校验；
- `service.py`：结构扫描、不可变复制、Parquet/JSON 修改、验证、发布与 DS 登记；
- `semantics.html`：结构编辑页面；
- `semantics.js`：扫描、增加、导入、删除和新 DS 切换；
- `semantics.css`：页面样式。
