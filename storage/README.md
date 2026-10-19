# 平台存储目录

这里保存数据集、处理结果和平台运行状态，容器内统一映射为 `/data`。除目录中的
`.gitkeep` 和本说明外，所有内容都被 Git 忽略。

## 当前存储方案

当前阶段只使用普通文件和文件夹，不使用 SQLite、PostgreSQL 等数据库。程序将
通过目录结构和 LeRobot 数据集自身的元数据读取信息；如果需要补充平台自己的
状态，可以先在 `system/` 中保存 JSON 等普通文件。`app/db/` 和 `migrations/`
只是未来接入数据库的预留位置，当前程序不依赖它们。

宿主机上的本目录会被 Docker 映射为容器内的 `/data`：

```text
项目中的 ./storage  ->  容器中的 /data
```

所以业务代码应读取配置提供的存储根目录，不能写死
`/home/heisan/.../storage` 这样的个人路径。

## 子目录用途

- `raw/`：上传成功并登记后的原始数据，只读、不可原地修改。
- `staging/`：上传、校验和安全解压期间的临时内容。
- `derived/`：裁剪、合并、拆分、去重或修改 task 后生成的新版本。
- `converted/`：格式转换输出。
- `exports/`：供用户或机器人下载的打包结果。
- `reports/`：结构检查、自定义规则、VLM 等质检报告。
- `models/`：模型定义、权重或发布包；首期暂不使用。
- `training-runs/`：训练日志、指标和 checkpoint；首期暂不使用。
- `system/`：平台自身的文件式状态。当前上传记录位于
  `system/upload-records/<upload_id>.json`；如果未来采用 SQLite，数据库文件也
  可以放在这里，但现在尚未启用。

不要把 Python、HTML、配置文件或 Dockerfile 放在此目录。测试用小样本应放在
`tests/fixtures/`，并确认其许可和体积适合进入 Git。
