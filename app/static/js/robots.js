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
        model: document.querySelector("#robot-model"),
        serial_number: document.querySelector("#robot-serial"),
        status: document.querySelector("#robot-status"),
        network_address: document.querySelector("#robot-address"),
        sensors: document.querySelector("#robot-sensors"),
        notes: document.querySelector("#robot-notes"),
    };
    const feedback = document.querySelector("#robot-feedback");
    const saveButton = document.querySelector("#robot-save");
    const title = document.querySelector("#robot-dialog-title");

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
        fields.model.value = robot?.model ?? "";
        fields.serial_number.value = robot?.serial_number ?? "";
        fields.status.value = robot?.status ?? "active";
        fields.network_address.value = robot?.network_address ?? "";
        fields.sensors.value = (robot?.sensors ?? []).join("\n");
        fields.notes.value = robot?.notes ?? "";
        title.textContent = robot ? `编辑 ${robot.id}` : "注册机器人";
        dialog.showModal();
        fields.name.focus();
    };

    const sensorValues = () => fields.sensors.value
        .split(/[\n,]/)
        .map((value) => value.trim())
        .filter((value, index, values) => value && values.indexOf(value) === index);

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
            model: fields.model.value,
            serial_number: fields.serial_number.value,
            status: fields.status.value,
            network_address: fields.network_address.value,
            sensors: sensorValues(),
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
})();
