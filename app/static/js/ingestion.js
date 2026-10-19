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

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const file = fileInput.files[0];
        if (!file || !file.name.toLowerCase().endsWith(".zip")) {
            showResult("error", "请选择一个 .zip 格式的 LeRobot v3 数据集。 ");
            return;
        }

        result.hidden = true;
        progressPanel.hidden = false;
        progressLabel.textContent = "正在上传";
        progressPercent.textContent = "0%";
        progressBar.style.width = "0%";
        submitButton.disabled = true;
        submitButton.textContent = "正在处理，请勿关闭页面";

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
                showResult(
                    "success",
                    `入库成功：${payload.dataset_name}，共 ${summary.total_episodes ?? "-"} 个 Episodes；保存位置 ${payload.raw_relative_path}。刷新页面可查看记录。`
                );
                form.reset();
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
})();
