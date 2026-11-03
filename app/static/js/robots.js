(() => {
    const page = document.querySelector("#robot-page");
    const dialog = document.querySelector("#robot-dialog");
    const form = document.querySelector("#robot-form");
    if (!page || !dialog || !form) return;

    const robots = JSON.parse(document.querySelector("#robot-records-json").textContent);
    const fields = {
        id: document.querySelector("#robot-id"),
        name: document.querySelector("#robot-name"),
        robot_type: document.querySelector("#robot-type"),
        serial_number: document.querySelector("#robot-serial"),
        network_address: document.querySelector("#robot-address"),
        notes: document.querySelector("#robot-notes"),
    };
    const feedback = document.querySelector("#robot-feedback");
    const saveButton = document.querySelector("#robot-save");
    const title = document.querySelector("#robot-dialog-title");
    const datasetDialog = document.querySelector("#robot-dataset-dialog");
    const datasetTitle = document.querySelector("#robot-dataset-title");
    const datasetSubtitle = document.querySelector("#robot-dataset-subtitle");
    const datasetSearch = document.querySelector("#robot-dataset-search");
    const datasetRows = document.querySelector("#robot-dataset-rows");
    const datasetBind = document.querySelector("#robot-dataset-bind");
    const datasetFeedback = document.querySelector("#robot-dataset-feedback");
    let bindingState = null;
    let bindingChanged = false;

    const escapeHtml = (value) => String(value ?? "").replace(
        /[&<>"']/g,
        (character) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[character]),
    );

    const showFeedback = (message, isError = false) => {
        feedback.textContent = message;
        feedback.hidden = false;
        feedback.classList.toggle("robot-feedback-error", isError);
    };

    const errorMessage = async (response) => {
        try {
            const body = await response.json();
            if (typeof body.detail === "string") return body.detail;
            if (Array.isArray(body.detail)) return body.detail.map((item) => item.msg).join("；");
        } catch (_error) {
            // The status fallback below is sufficient when the response is not JSON.
        }
        return `请求失败（HTTP ${response.status}）`;
    };

    const openDialog = (robot = null) => {
        form.reset();
        fields.id.value = robot?.id ?? "";
        fields.name.value = robot?.name ?? "";
        fields.robot_type.value = robot?.robot_type ?? "";
        fields.serial_number.value = robot?.serial_number ?? "";
        fields.network_address.value = robot?.network_address ?? "";
        fields.notes.value = robot?.notes ?? "";
        title.textContent = robot ? `编辑 ${robot.id}` : "注册机器人";
        dialog.showModal();
        fields.name.focus();
    };

    document.querySelector("#robot-create").addEventListener("click", () => openDialog());
    document.querySelector("#robot-dialog-close").addEventListener("click", () => dialog.close());
    document.querySelector("#robot-cancel").addEventListener("click", () => dialog.close());

    document.querySelectorAll("[data-robot-edit]").forEach((button) => {
        button.addEventListener("click", () => {
            const robot = robots.find((item) => item.id === button.dataset.robotEdit);
            if (robot) openDialog(robot);
        });
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const id = fields.id.value;
        const payload = {
            name: fields.name.value,
            robot_type: fields.robot_type.value,
            serial_number: fields.serial_number.value,
            network_address: fields.network_address.value,
            notes: fields.notes.value,
        };
        saveButton.disabled = true;
        try {
            const response = await fetch(id ? `/robots/api/robots/${id}` : "/robots/api/robots", {
                method: id ? "PUT" : "POST",
                headers: {"Content-Type": "application/json", "Accept": "application/json"},
                body: JSON.stringify(payload),
            });
            if (!response.ok) throw new Error(await errorMessage(response));
            dialog.close();
            window.location.reload();
        } catch (error) {
            showFeedback(error.message, true);
            dialog.close();
        } finally {
            saveButton.disabled = false;
        }
    });

    const changeStatus = async (id, action, prompt) => {
        if (!window.confirm(prompt)) return;
        try {
            const response = await fetch(`/robots/api/robots/${id}/${action}`, {
                method: "POST",
                headers: {"Accept": "application/json"},
            });
            if (!response.ok) throw new Error(await errorMessage(response));
            window.location.reload();
        } catch (error) {
            showFeedback(error.message, true);
        }
    };

    document.querySelectorAll("[data-robot-deactivate]").forEach((button) => {
        button.addEventListener("click", () => changeStatus(
            button.dataset.robotDeactivate,
            "deactivate",
            "确认停用这条机器人档案？历史记录会保留，可以稍后恢复。",
        ));
    });
    document.querySelectorAll("[data-robot-activate]").forEach((button) => {
        button.addEventListener("click", () => changeStatus(
            button.dataset.robotActivate,
            "activate",
            "确认恢复使用这条机器人档案？",
        ));
    });

    const showDatasetFeedback = (message, isError = false) => {
        datasetFeedback.textContent = message || "";
        datasetFeedback.hidden = !message;
        datasetFeedback.classList.toggle("robot-feedback-error", isError);
    };

    const selectedDatasetIds = () => Array.from(
        datasetRows.querySelectorAll("[data-bind-dataset]:checked"),
        (input) => input.dataset.bindDataset,
    );

    const updateBindButton = () => {
        const count = selectedDatasetIds().length;
        datasetBind.disabled = !bindingState || bindingState.robot.status === "inactive" || count === 0;
        datasetBind.textContent = count ? `关联选中数据集（${count}）` : "关联选中数据集";
    };

    const renderDatasetBindings = () => {
        if (!bindingState) return;
        const term = datasetSearch.value.trim().toLocaleLowerCase();
        const records = bindingState.datasets.filter((dataset) => [
            dataset.id,
            dataset.name,
            dataset.robot_type,
            dataset.collector_robot_id,
            dataset.collector_robot_name,
        ].some((value) => String(value ?? "").toLocaleLowerCase().includes(term)));
        datasetTitle.textContent = `${bindingState.robot.id} · ${bindingState.robot.name}`;
        datasetSubtitle.textContent = `${bindingState.linked_count} 个已关联数据集 · ${bindingState.robot.status_label}`;
        datasetRows.innerHTML = records.length ? records.map((dataset) => {
            const selected = dataset.linked_to_selected;
            const inactive = bindingState.robot.status === "inactive";
            let collector = "未关联";
            if (selected) collector = `<span class="robot-dataset-current">${escapeHtml(bindingState.robot.id)} · ${escapeHtml(bindingState.robot.name)}</span>`;
            else if (dataset.collector_robot_id) collector = `${escapeHtml(dataset.collector_robot_id)} · ${escapeHtml(dataset.collector_robot_name)}<small>选择后将改绑</small>`;
            return `<tr class="${selected ? "robot-dataset-linked" : ""}">
                <td>${selected || inactive ? "—" : `<input type="checkbox" data-bind-dataset="${escapeHtml(dataset.id)}" aria-label="选择 ${escapeHtml(dataset.id)}">`}</td>
                <td><strong>${escapeHtml(dataset.id)} · ${escapeHtml(dataset.name)}</strong><small>${escapeHtml(dataset.storage_area)}</small></td>
                <td>${escapeHtml(dataset.robot_type)}${dataset.type_mismatch ? '<span class="robot-type-warning">与机器人类型不一致</span>' : ""}</td>
                <td>${collector}</td>
                <td>${selected ? `<button type="button" data-unlink-dataset="${escapeHtml(dataset.id)}">解除关联</button>` : "—"}</td>
            </tr>`;
        }).join("") : '<tr><td colspan="5" class="robot-empty">没有符合条件的数据集。</td></tr>';
        updateBindButton();
    };

    const loadDatasetBindings = async (robotId) => {
        const response = await fetch(`/robots/api/robots/${encodeURIComponent(robotId)}/datasets`, {
            headers: {"Accept": "application/json"},
        });
        if (!response.ok) throw new Error(await errorMessage(response));
        bindingState = await response.json();
        renderDatasetBindings();
    };

    document.querySelectorAll("[data-robot-datasets]").forEach((button) => {
        button.addEventListener("click", async () => {
            bindingState = null;
            bindingChanged = false;
            datasetSearch.value = "";
            datasetRows.innerHTML = '<tr><td colspan="5" class="robot-empty">正在读取数据集…</td></tr>';
            showDatasetFeedback("");
            datasetDialog.showModal();
            try {
                await loadDatasetBindings(button.dataset.robotDatasets);
                if (bindingState.robot.status === "inactive") {
                    showDatasetFeedback("该机器人已停用，只能查看或解除历史关联。", true);
                }
            } catch (error) {
                showDatasetFeedback(error.message, true);
            }
        });
    });

    datasetSearch.addEventListener("input", renderDatasetBindings);
    datasetRows.addEventListener("change", updateBindButton);

    datasetBind.addEventListener("click", async () => {
        const ids = selectedDatasetIds();
        if (!bindingState || !ids.length) return;
        const selected = bindingState.datasets.filter((dataset) => ids.includes(dataset.id));
        const rebinds = selected.filter((dataset) => dataset.collector_robot_id);
        const mismatches = selected.filter((dataset) => dataset.type_mismatch);
        const warnings = [];
        if (rebinds.length) warnings.push(`${rebinds.length} 个数据集将从其他机器人改绑`);
        if (mismatches.length) warnings.push(`${mismatches.length} 个数据集的 robot_type 不一致`);
        const prompt = warnings.length
            ? `${warnings.join("；")}。仍要关联到 ${bindingState.robot.id} 吗？`
            : `确认将 ${ids.length} 个数据集关联到 ${bindingState.robot.id}？`;
        if (!window.confirm(prompt)) return;
        datasetBind.disabled = true;
        try {
            const response = await fetch(`/robots/api/robots/${encodeURIComponent(bindingState.robot.id)}/datasets`, {
                method: "POST",
                headers: {"Content-Type": "application/json", "Accept": "application/json"},
                body: JSON.stringify({dataset_ids: ids}),
            });
            if (!response.ok) throw new Error(await errorMessage(response));
            bindingState = await response.json();
            bindingChanged = true;
            showDatasetFeedback(`已完成 ${ids.length} 个数据集的关联。`);
            renderDatasetBindings();
        } catch (error) {
            showDatasetFeedback(error.message, true);
            updateBindButton();
        }
    });

    datasetRows.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-unlink-dataset]");
        if (!button || !bindingState) return;
        const datasetId = button.dataset.unlinkDataset;
        if (!window.confirm(`确认解除 ${datasetId} 与 ${bindingState.robot.id} 的关联？数据集文件不会被删除。`)) return;
        button.disabled = true;
        try {
            const response = await fetch(
                `/robots/api/robots/${encodeURIComponent(bindingState.robot.id)}/datasets/${encodeURIComponent(datasetId)}`,
                {method: "DELETE", headers: {"Accept": "application/json"}},
            );
            if (!response.ok) throw new Error(await errorMessage(response));
            bindingChanged = true;
            await loadDatasetBindings(bindingState.robot.id);
            showDatasetFeedback(`${datasetId} 已解除关联。`);
        } catch (error) {
            showDatasetFeedback(error.message, true);
            button.disabled = false;
        }
    });

    const closeDatasetDialog = () => datasetDialog.close();
    document.querySelector("#robot-dataset-close").addEventListener("click", closeDatasetDialog);
    datasetDialog.addEventListener("close", () => {
        if (bindingChanged) window.location.reload();
    });
})();
