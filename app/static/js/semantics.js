(() => {
    const page = document.querySelector("#semantic-page");
    const dialog = document.querySelector("#semantic-dialog");
    const form = document.querySelector("#semantic-form");
    if (!page || !dialog || !form) return;

    const records = JSON.parse(document.querySelector("#semantic-records-json").textContent);
    const fields = {
        id: document.querySelector("#semantic-id"),
        category: document.querySelector("#semantic-category"),
        code: document.querySelector("#semantic-code"),
        name: document.querySelector("#semantic-name"),
        sort_order: document.querySelector("#semantic-sort"),
        description: document.querySelector("#semantic-description"),
        examples: document.querySelector("#semantic-examples"),
        status: document.querySelector("#semantic-status"),
    };
    const feedback = document.querySelector("#semantic-feedback");
    const saveButton = document.querySelector("#semantic-save");
    const title = document.querySelector("#semantic-dialog-title");

    const showFeedback = (message, isError = false) => {
        feedback.textContent = message;
        feedback.hidden = false;
        feedback.classList.toggle("semantic-feedback-error", isError);
    };

    const errorMessage = async (response) => {
        try {
            const body = await response.json();
            if (typeof body.detail === "string") return body.detail;
            if (Array.isArray(body.detail)) return body.detail.map((item) => item.msg).join("；");
        } catch (_error) {
            // Fall through to the HTTP status when the response is not JSON.
        }
        return `请求失败（HTTP ${response.status}）`;
    };

    const openDialog = (record = null) => {
        form.reset();
        fields.id.value = record?.id ?? "";
        fields.category.value = record?.category ?? "scene";
        fields.code.value = record?.code ?? "";
        fields.name.value = record?.name ?? "";
        fields.sort_order.value = record?.sort_order ?? 100;
        fields.description.value = record?.description ?? "";
        fields.examples.value = (record?.examples ?? []).join("\n");
        fields.status.value = record?.status ?? "active";
        fields.category.disabled = Boolean(record);
        fields.code.disabled = Boolean(record);
        title.textContent = record ? `编辑 ${record.id}` : "新建定义";
        dialog.showModal();
        (record ? fields.name : fields.code).focus();
    };

    const examples = () => fields.examples.value
        .split(/[\n,]/)
        .map((value) => value.trim())
        .filter((value, index, values) => value && values.indexOf(value) === index);

    document.querySelector("#semantic-create").addEventListener("click", () => openDialog());
    document.querySelector("#semantic-dialog-close").addEventListener("click", () => dialog.close());
    document.querySelector("#semantic-cancel").addEventListener("click", () => dialog.close());

    document.querySelectorAll("[data-semantic-edit]").forEach((button) => {
        button.addEventListener("click", () => {
            const record = records.find((item) => item.id === button.dataset.semanticEdit);
            if (record) openDialog(record);
        });
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const id = fields.id.value;
        const common = {
            name: fields.name.value,
            description: fields.description.value,
            examples: examples(),
            status: fields.status.value,
            sort_order: Number(fields.sort_order.value),
        };
        const payload = id ? common : {
            category: fields.category.value,
            code: fields.code.value,
            ...common,
        };
        saveButton.disabled = true;
        try {
            const response = await fetch(
                id ? `/semantics/api/definitions/${id}` : "/semantics/api/definitions",
                {
                    method: id ? "PUT" : "POST",
                    headers: {"Content-Type": "application/json", "Accept": "application/json"},
                    body: JSON.stringify(payload),
                },
            );
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
            const response = await fetch(`/semantics/api/definitions/${id}/${action}`, {
                method: "POST",
                headers: {"Accept": "application/json"},
            });
            if (!response.ok) throw new Error(await errorMessage(response));
            window.location.reload();
        } catch (error) {
            showFeedback(error.message, true);
        }
    };

    document.querySelectorAll("[data-semantic-deactivate]").forEach((button) => {
        button.addEventListener("click", () => changeStatus(
            button.dataset.semanticDeactivate,
            "deactivate",
            "确认停用这条定义？历史记录会保留，可以稍后恢复。",
        ));
    });
    document.querySelectorAll("[data-semantic-activate]").forEach((button) => {
        button.addEventListener("click", () => changeStatus(
            button.dataset.semanticActivate,
            "activate",
            "确认恢复使用这条定义？",
        ));
    });
})();
