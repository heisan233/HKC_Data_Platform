(() => {
    const page = document.querySelector("#schema-page");
    if (!page) return;

    const datasetSelect = document.querySelector("#schema-dataset");
    const feedback = document.querySelector("#schema-feedback");
    const summary = document.querySelector("#schema-summary");
    const fieldsSection = document.querySelector("#schema-fields");
    const fieldDialog = document.querySelector("#field-dialog");
    const importDialog = document.querySelector("#import-dialog");
    const deleteDialog = document.querySelector("#delete-dialog");

    const escapeHtml = (value) => String(value ?? "")
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
    const showFeedback = (message, error = false) => {
        feedback.textContent = message;
        feedback.hidden = false;
        feedback.classList.toggle("error", error);
    };
    const clearFeedback = () => { feedback.hidden = true; };
    const responseMessage = async (response) => {
        try {
            const body = await response.json();
            if (body.message) return body.message;
            if (typeof body.detail === "string") return body.detail;
            if (Array.isArray(body.detail)) return body.detail.map((item) => item.msg).join("；");
        } catch (_error) {
            // Use the HTTP status below.
        }
        return `请求失败（HTTP ${response.status}）`;
    };
    const requestJson = async (url, options = {}) => {
        const response = await fetch(url, options);
        if (!response.ok) throw new Error(await responseMessage(response));
        return response.json();
    };

    const loadDatasets = async (selected = "") => {
        const payload = await requestJson("/semantics/api/datasets");
        datasetSelect.innerHTML = '<option value="">请选择已登记数据集</option>';
        payload.datasets.forEach((item) => {
            const option = document.createElement("option");
            option.value = item.dataset_id;
            option.textContent = `${item.dataset_id} · ${item.dataset_name}（${item.storage_area}）`;
            datasetSelect.append(option);
        });
        if (selected) datasetSelect.value = selected;
    };
    const stateLabel = (state) => ({
        complete: "完整",
        partial: "部分为空",
        declared_empty: "全空占位",
        missing_column: "缺少物理列",
        external_media: "外部媒体",
    }[state] ?? state);
    const renderFields = (target, rows, frame = false) => {
        target.innerHTML = rows.map((item) => {
            const deletion = item.deletion === "blocked"
                ? '<span class="schema-locked">核心字段</span>'
                : item.deletion === "unsupported"
                    ? '<span class="schema-locked">媒体暂不处理</span>'
                    : `<button type="button" class="schema-link schema-delete" data-scope="${escapeHtml(item.scope)}" data-name="${escapeHtml(item.name)}" data-deletion="${escapeHtml(item.deletion)}">删除</button>`;
            const type = frame
                ? `<code>${escapeHtml(item.dtype)}</code><small>[${escapeHtml((item.shape ?? []).join(", "))}]</small>`
                : `<code>${escapeHtml(item.dtype)}</code><small>${escapeHtml(item.arrow_type)}</small>`;
            return `<tr>
                <td><strong>${escapeHtml(item.name)}</strong>${item.description ? `<small>${escapeHtml(item.description)}</small>` : ""}</td>
                <td>${type}</td>
                ${frame ? `<td><code>${escapeHtml(item.arrow_type)}</code></td>` : ""}
                <td><span class="schema-origin ${item.origin}">${item.origin === "platform" ? "平台扩展" : "官方/源数据"}</span></td>
                <td><strong>${escapeHtml(item.completeness)}%</strong><small>${escapeHtml(item.non_null)} / ${escapeHtml(item.total)}</small></td>
                <td><span class="schema-state ${escapeHtml(item.state)}">${escapeHtml(stateLabel(item.state))}</span></td>
                <td>${deletion}</td>
            </tr>`;
        }).join("") || `<tr><td colspan="${frame ? 7 : 6}" class="schema-empty">没有读取到字段</td></tr>`;
    };
    const renderSchema = (payload) => {
        const dataset = payload.dataset;
        document.querySelector("#summary-id").textContent = dataset.dataset_id;
        document.querySelector("#summary-name").textContent = dataset.dataset_name;
        document.querySelector("#summary-size").textContent = `${dataset.total_episodes} / ${dataset.total_frames}`;
        document.querySelector("#summary-shards").textContent = `${payload.status.episode_shards} 个 Episode 分片 · ${payload.status.frame_shards} 个 frame 分片`;
        document.querySelector("#summary-extension").textContent = payload.status.structure_extended ? "已扩展" : "未扩展";
        const training = document.querySelector("#summary-training");
        training.textContent = payload.status.training_ready ? "可训练" : "不满足训练要求";
        training.className = payload.status.training_ready ? "ready" : "blocked";
        document.querySelector("#summary-blockers").textContent = payload.status.training_ready
            ? "未发现声明缺失" : `空值/缺失：${payload.status.training_blockers.join("、")}`;
        renderFields(document.querySelector("#episode-fields"), payload.episode_fields);
        renderFields(document.querySelector("#frame-fields"), payload.frame_features, true);
        summary.hidden = false;
        fieldsSection.hidden = false;
    };
    const scan = async (datasetId = datasetSelect.value) => {
        if (!datasetId) {
            showFeedback("请先选择一个数据集。", true);
            return;
        }
        clearFeedback();
        document.querySelector("#schema-scan").disabled = true;
        try {
            const payload = await requestJson(`/semantics/api/datasets/${encodeURIComponent(datasetId)}/schema`);
            renderSchema(payload);
            showFeedback(`已读取 ${datasetId} 的真实 info.json 和全部 Parquet 分片。`);
        } catch (error) {
            showFeedback(error.message, true);
        } finally {
            document.querySelector("#schema-scan").disabled = false;
        }
    };
    document.querySelector("#schema-scan").addEventListener("click", () => scan());
    document.querySelectorAll("[data-schema-tab]").forEach((button) => {
        button.addEventListener("click", () => {
            document.querySelectorAll("[data-schema-tab]").forEach((item) => item.classList.remove("active"));
            document.querySelectorAll("[data-schema-panel]").forEach((item) => item.classList.remove("active"));
            button.classList.add("active");
            document.querySelector(`[data-schema-panel="${button.dataset.schemaTab}"]`).classList.add("active");
        });
    });

    const parseList = (value) => value.split(",").map((item) => item.trim()).filter(Boolean);
    const parseShape = (value) => {
        const shape = parseList(value).map(Number);
        if (!shape.length || shape.some((item) => !Number.isInteger(item) || item < 1)) {
            throw new Error("Shape 必须是正整数，例如 1、6 或 2,3。");
        }
        return shape;
    };
    const openFieldDialog = (scope) => {
        document.querySelector("#field-form").reset();
        document.querySelector("#field-scope").value = scope;
        document.querySelector("#field-title").textContent = scope === "episode" ? "新增 Episode 字段" : "新增空逐帧 Feature";
        const dtype = document.querySelector("#field-dtype");
        dtype.innerHTML = (scope === "episode"
            ? ["string", "int64", "float64", "bool"]
            : ["float32", "float64", "int32", "int64", "bool"])
            .map((item) => `<option value="${item}">${item}</option>`).join("");
        document.querySelector("#shape-label").hidden = scope === "episode";
        document.querySelector("#names-label").hidden = scope === "episode";
        document.querySelector("#value-label").hidden = scope === "frame";
        document.querySelector("#field-shape").value = "1";
        document.querySelector("#field-note").textContent = scope === "episode"
            ? "统一值会写入每个 Episode；留空则建立 nullable 空列。"
            : "该 Feature 会进入 info.json.features，并在每个 frame Parquet 中建立全空列，因此新 DS 会标记为不满足训练要求。";
        fieldDialog.showModal();
    };
    document.querySelectorAll("[data-open-create]").forEach((button) => {
        button.addEventListener("click", () => openFieldDialog(button.dataset.openCreate));
    });
    document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => fieldDialog.close()));
    document.querySelector("#field-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const scope = document.querySelector("#field-scope").value;
        const dtype = document.querySelector("#field-dtype").value;
        const rawValue = document.querySelector("#field-value").value.trim();
        let value = null;
        if (scope === "episode" && rawValue !== "") {
            if (["int64", "float64"].includes(dtype)) value = Number(rawValue);
            else if (dtype === "bool") {
                const normalized = rawValue.toLowerCase();
                if (!["true", "false", "1", "0", "yes", "no"].includes(normalized)) {
                    showFeedback("bool 统一值只能填写 true/false、1/0 或 yes/no。", true);
                    return;
                }
                value = ["true", "1", "yes"].includes(normalized);
            }
            else value = rawValue;
        }
        try {
            const names = scope === "episode" ? null : parseList(document.querySelector("#field-names").value);
            const payload = {
                scope,
                name: document.querySelector("#field-name").value,
                dtype,
                shape: scope === "episode" ? [1] : parseShape(document.querySelector("#field-shape").value),
                names: names?.length ? names : null,
                description: document.querySelector("#field-description").value,
                nullable: value === null,
                value,
                version_name: document.querySelector("#field-version-name").value,
                version_description: document.querySelector("#field-version-description").value,
            };
            const result = await requestJson(`/semantics/api/datasets/${encodeURIComponent(datasetSelect.value)}/fields`, {
                method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
            });
            fieldDialog.close();
            await loadDatasets(result.result.dataset_id);
            await scan(result.result.dataset_id);
            showFeedback(`已生成 ${result.result.dataset_id}，源数据集未修改。`);
        } catch (error) {
            showFeedback(error.message, true);
            fieldDialog.close();
        }
    });

    document.querySelector("#open-import").addEventListener("click", () => {
        document.querySelector("#import-form").reset();
        importDialog.showModal();
    });
    document.querySelectorAll("[data-close-import]").forEach((button) => button.addEventListener("click", () => importDialog.close()));
    document.querySelector("#import-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            const data = new FormData(event.currentTarget);
            data.set("shape", JSON.stringify(parseShape(data.get("shape_text"))));
            data.set("source_columns", JSON.stringify(parseList(data.get("source_columns_text"))));
            data.set("names", JSON.stringify(parseList(data.get("names_text"))));
            data.delete("shape_text");
            data.delete("source_columns_text");
            data.delete("names_text");
            const response = await fetch(`/semantics/api/datasets/${encodeURIComponent(datasetSelect.value)}/frame-import`, {method: "POST", body: data});
            if (!response.ok) throw new Error(await responseMessage(response));
            const result = await response.json();
            importDialog.close();
            await loadDatasets(result.result.dataset_id);
            await scan(result.result.dataset_id);
            showFeedback(`逐帧 Feature 已导入，新版本为 ${result.result.dataset_id}。`);
        } catch (error) {
            showFeedback(error.message, true);
            importDialog.close();
        }
    });

    fieldsSection.addEventListener("click", (event) => {
        const button = event.target.closest(".schema-delete");
        if (!button) return;
        const official = button.dataset.deletion === "warning";
        document.querySelector("#delete-form").reset();
        document.querySelector("#delete-scope").value = button.dataset.scope;
        document.querySelector("#delete-name").value = button.dataset.name;
        document.querySelector("#delete-official").value = official ? "true" : "false";
        document.querySelector("#delete-copy").innerHTML = official
            ? `<strong>警告：${escapeHtml(button.dataset.name)} 不是平台新增字段。</strong><p>删除后可能导致训练或转换失败。源 DS 不变，但新 Derived DS 将不再包含此定义和物理列。</p>`
            : `<strong>将删除平台字段 ${escapeHtml(button.dataset.name)}</strong><p>源 DS 不变，操作只作用于新 Derived DS。</p>`;
        deleteDialog.showModal();
    });
    document.querySelectorAll("[data-close-delete]").forEach((button) => button.addEventListener("click", () => deleteDialog.close()));
    document.querySelector("#delete-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const official = document.querySelector("#delete-official").value === "true";
        if (official && !window.confirm("这是官方/源数据 Feature。确认仍要从新的 Derived DS 删除？")) return;
        const payload = {
            scope: document.querySelector("#delete-scope").value,
            name: document.querySelector("#delete-name").value,
            confirm_official: official,
            version_name: document.querySelector("#delete-version-name").value,
            version_description: document.querySelector("#delete-version-description").value,
        };
        try {
            const result = await requestJson(`/semantics/api/datasets/${encodeURIComponent(datasetSelect.value)}/fields/delete`, {
                method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
            });
            deleteDialog.close();
            await loadDatasets(result.result.dataset_id);
            await scan(result.result.dataset_id);
            showFeedback(`字段已从新版本 ${result.result.dataset_id} 删除，源数据集未修改。`);
        } catch (error) {
            showFeedback(error.message, true);
            deleteDialog.close();
        }
    });

    loadDatasets().catch((error) => showFeedback(error.message, true));
})();
