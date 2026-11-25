(() => {
    const form = document.getElementById("upload-form");
    if (!form) return;

    const fileInput = document.getElementById("archive");
    const dropZone = document.getElementById("drop-zone");
    const fileSummary = document.getElementById("file-summary");
    const submitButton = document.getElementById("submit-upload");
    const progressPanel = document.getElementById("progress-panel");
    const progressLabel = document.getElementById("progress-label");
    const progressPercent = document.getElementById("progress-percent");
    const progressBar = document.getElementById("progress-bar");
    const result = document.getElementById("upload-result");
    const family = document.getElementById("dataset-family");
    const robotField = document.getElementById("robot-type-field");
    const robotInput = document.getElementById("robot-type");
    const requirements = {
        vla: document.getElementById("vla-requirements"),
        wam: document.getElementById("wam-requirements"),
        ego: document.getElementById("ego-requirements"),
    };
    const uploadLimit = document.getElementById("upload-limit");
    const clearRecords = document.getElementById("clear-upload-records");

    const updateFamily = () => {
        const ego = family.value === "ego";
        robotField.hidden = ego;
        robotInput.disabled = ego;
        Object.entries(requirements).forEach(([name, panel]) => {
            panel.hidden = family.value !== name;
        });
        uploadLimit.textContent = ego ? "按服务器剩余空间接收" : `单包上限 ${uploadLimit.dataset.lerobotLimit}`;
    };
    family.addEventListener("change", updateFamily);
    updateFamily();

    const formatBytes = (bytes) => {
        const units = ["B", "KiB", "MiB", "GiB", "TiB"];
        let value = bytes;
        let unitIndex = 0;
        while (value >= 1024 && unitIndex < units.length - 1) {
            value /= 1024;
            unitIndex += 1;
        }
        return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
    };

    const showSelectedFile = () => {
        const file = fileInput.files[0];
        fileSummary.textContent = file
            ? `${file.name} · ${formatBytes(file.size)}`
            : "尚未选择文件";
    };

    ["dragenter", "dragover"].forEach((eventName) => {
        dropZone.addEventListener(eventName, (event) => {
            event.preventDefault();
            dropZone.classList.add("is-dragging");
        });
    });

    ["dragleave", "drop"].forEach((eventName) => {
        dropZone.addEventListener(eventName, (event) => {
            event.preventDefault();
            dropZone.classList.remove("is-dragging");
        });
    });

    dropZone.addEventListener("drop", (event) => {
        if (event.dataTransfer.files.length) {
            fileInput.files = event.dataTransfer.files;
            showSelectedFile();
        }
    });
    fileInput.addEventListener("change", showSelectedFile);

    const showResult = (kind, message) => {
        result.hidden = false;
        result.className = `upload-result ${kind}`;
        result.textContent = message;
    };

    const uploadEgo = async (file) => {
        const key = `hkc-ego-upload:${file.name}:${file.size}:${file.lastModified}`;
        let sessionId = window.localStorage.getItem(key);
        let offset = 0;
        if (sessionId) {
            const stateResponse = await fetch(`/ingestion/api/ego-sessions/${sessionId}`);
            if (stateResponse.ok) {
                const state = await stateResponse.json();
                offset = state.received_bytes;
                if (state.filename !== file.name || state.complete || offset > file.size) sessionId = null;
            } else {
                sessionId = null;
            }
        }
        if (!sessionId) {
            const created = await fetch("/ingestion/api/ego-sessions", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ filename: file.name,
                    dataset_name: document.getElementById("dataset-name").value,
                    description: document.getElementById("description").value }),
            });
            const response = await created.json();
            if (!created.ok) throw new Error(response.detail || "无法创建 EGO 上传会话");
            sessionId = response.session_id;
            window.localStorage.setItem(key, sessionId);
        }
        const chunkSize = 8 * 1024 * 1024;
        while (offset < file.size) {
            const end = Math.min(file.size, offset + chunkSize);
            const response = await fetch(`/ingestion/api/ego-sessions/${sessionId}/chunks?offset=${offset}`, {
                method: "PUT", body: file.slice(offset, end),
            });
            if (response.status === 409) {
                const stateResponse = await fetch(`/ingestion/api/ego-sessions/${sessionId}`);
                if (!stateResponse.ok) throw new Error("上传会话已失效，请重新选择文件");
                offset = (await stateResponse.json()).received_bytes;
                continue;
            }
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || "分块上传失败");
            offset = data.received_bytes;
            const percent = Math.round((offset / file.size) * 100);
            progressPercent.textContent = `${percent}%`;
            progressBar.style.width = `${percent}%`;
        }
        progressLabel.textContent = "上传完成，服务器正在校验";
        const complete = await fetch(`/ingestion/api/ego-sessions/${sessionId}/complete`, { method: "POST" });
        const outcome = await complete.json();
        window.localStorage.removeItem(key);
        if (!complete.ok) throw new Error(outcome.message || outcome.detail || "EGO 数据集入库失败");
        return outcome;
    };

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const file = fileInput.files[0];
        if (!file || !file.name.toLowerCase().endsWith(".zip")) {
            showResult("error", "请选择一个 ZIP 数据集。");
            return;
        }

        result.hidden = true;
        progressPanel.hidden = false;
        progressLabel.textContent = "正在上传";
        progressPercent.textContent = "0%";
        progressBar.style.width = "0%";
        submitButton.disabled = true;
        submitButton.textContent = "正在处理，请勿关闭页面";

        if (family.value === "ego") {
            uploadEgo(file).then((payload) => {
                const summary = payload.summary || {};
                showResult("success", `入库成功：${payload.dataset_name}（${payload.dataset_id}），共 ${summary.total_episodes ?? 0} 个 Episodes，待质检。`);
                form.reset();
                updateFamily();
                showSelectedFile();
            }).catch((error) => {
                showResult("error", `${error.message}。重新选择同一文件可继续上传。`);
            }).finally(() => {
                submitButton.disabled = false;
                submitButton.textContent = "上传并校验数据集";
            });
            return;
        }

        const request = new XMLHttpRequest();
        request.open("POST", form.action);
        request.upload.addEventListener("progress", (progressEvent) => {
            if (!progressEvent.lengthComputable) return;
            const percent = Math.round((progressEvent.loaded / progressEvent.total) * 100);
            progressPercent.textContent = `${percent}%`;
            progressBar.style.width = `${percent}%`;
            if (percent === 100) progressLabel.textContent = "上传完成，服务器正在校验";
        });

        request.addEventListener("load", () => {
            let payload = {};
            try {
                payload = JSON.parse(request.responseText);
            } catch (_) {
                payload = { message: "服务器返回了无法解析的结果。" };
            }

            if (request.status >= 200 && request.status < 300 && payload.ok) {
                const summary = payload.summary || {};
                const details = family.value === "ego"
                    ? `共 ${summary.task_count ?? 0} 个任务、${summary.clip_count ?? 0} 个片段，待质检。`
                    : `机器人类型 ${summary.robot_type ?? "-"}，共 ${summary.total_episodes ?? "-"} 个 Episode。`;
                showResult("success", `入库成功：${payload.dataset_name}（${payload.dataset_id}），${details}`);
                form.reset();
                updateFamily();
                showSelectedFile();
            } else {
                showResult("error", payload.message || `上传失败（HTTP ${request.status}）。`);
            }
        });

        request.addEventListener("error", () => {
            showResult("error", "网络连接中断，文件未能完成上传。 ");
        });

        request.addEventListener("loadend", () => {
            submitButton.disabled = false;
            submitButton.textContent = "上传并校验数据集";
        });

        request.send(new FormData(form));
    });

    if (clearRecords) clearRecords.addEventListener("click", async () => {
        if (!window.confirm("确定清空全部上传记录？已入库的数据集不会删除。")) return;
        clearRecords.disabled = true;
        try {
            const response = await fetch("/ingestion/api/uploads/records", { method: "DELETE" });
            if (!response.ok) throw new Error("清空记录失败");
            window.location.reload();
        } catch (error) {
            showResult("error", error.message);
            clearRecords.disabled = false;
        }
    });
})();
