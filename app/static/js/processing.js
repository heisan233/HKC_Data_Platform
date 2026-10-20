(() => {
    "use strict";

    const elements = {
        message: document.getElementById("processing-message"),
        source: document.getElementById("source-dataset"),
        sourceStats: document.getElementById("source-stats"),
        search: document.getElementById("episode-search"),
        episodeList: document.getElementById("episode-list"),
        toggleAll: document.getElementById("toggle-all-episodes"),
        filterCount: document.getElementById("filter-count"),
        splitMode: document.getElementById("split-mode"),
        splitRatios: document.getElementById("split-ratio-fields"),
        splitIndexes: document.getElementById("split-index-fields"),
        mergeDatasets: document.getElementById("merge-datasets"),
        reorder: document.getElementById("reorder-indices"),
        scanDuplicates: document.getElementById("scan-duplicates"),
        duplicateReport: document.getElementById("duplicate-report"),
        versionName: document.getElementById("version-name"),
        versionDescription: document.getElementById("version-description"),
        start: document.getElementById("start-processing"),
        refreshJobs: document.getElementById("refresh-jobs"),
        jobList: document.getElementById("job-list"),
    };

    const state = {
        datasets: [],
        workspace: null,
        selected: new Set(),
        operation: "filter",
        duplicateReport: null,
    };

    const operationLabels = {
        filter: "Episode 筛除",
        split: "数据集拆分",
        merge: "数据集合并",
        reorder: "Episode 重排",
        deduplicate: "重复检测与去重",
    };
    const statusLabels = {
        queued: "排队中",
        running: "处理中",
        completed: "已完成",
        failed: "失败",
    };

    const showMessage = (message, kind = "info") => {
        elements.message.hidden = !message;
        elements.message.className = `processing-message ${kind}`;
        elements.message.textContent = message;
    };

    const requestJson = async (url, options = {}) => {
        const response = await fetch(url, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = Array.isArray(payload.detail)
                ? payload.detail.map((item) => item.msg).join("；")
                : payload.detail;
            throw new Error(payload.message || detail || `请求失败（${response.status}）`);
        }
        return payload;
    };

    const datasetLabel = (dataset) => {
        const kind = {raw: "原始", derived: "派生", detected: "质检通过"}[dataset.kind] || dataset.kind;
        return `${dataset.dataset_name} · ${kind} · ${dataset.total_episodes} Episodes`;
    };

    const renderDatasetOptions = () => {
        elements.source.replaceChildren(
            ...state.datasets.map((dataset) => {
                const option = document.createElement("option");
                option.value = dataset.dataset_id;
                option.textContent = datasetLabel(dataset);
                return option;
            })
        );
        elements.mergeDatasets.replaceChildren(
            ...state.datasets.map((dataset) => {
                const label = document.createElement("label");
                label.className = "merge-item";
                const checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.value = dataset.dataset_id;
                const copy = document.createElement("div");
                const name = document.createElement("strong");
                name.textContent = dataset.dataset_name;
                const meta = document.createElement("span");
                meta.textContent = `${dataset.kind} · ${dataset.total_episodes} Episodes · ${dataset.robot_type}`;
                copy.append(name, meta);
                label.append(checkbox, copy);
                return label;
            })
        );
    };

    const renderStats = () => {
        if (!state.workspace) {
            elements.sourceStats.replaceChildren();
            return;
        }
        const dataset = state.workspace.dataset;
        const values = [
            ["Episodes", dataset.total_episodes],
            ["Frames", dataset.total_frames.toLocaleString()],
            ["机器人", dataset.robot_type],
            ["FPS / 视频", `${dataset.fps ?? "-"} / ${dataset.video_keys.length}`],
        ];
        elements.sourceStats.replaceChildren(
            ...values.map(([label, value]) => {
                const item = document.createElement("div");
                item.className = "source-stat";
                const title = document.createElement("span");
                title.textContent = label;
                const content = document.createElement("strong");
                content.textContent = value;
                content.title = String(value);
                item.append(title, content);
                return item;
            })
        );
    };

    const filteredEpisodes = () => {
        if (!state.workspace) return [];
        const query = elements.search.value.trim().toLowerCase();
        if (!query) return state.workspace.episodes;
        return state.workspace.episodes.filter((episode) =>
            `episode ${episode.episode_index} ${episode.task}`.toLowerCase().includes(query)
        );
    };

    const renderSelectionCount = () => {
        elements.filterCount.textContent = state.selected.size;
    };

    const renderEpisodes = () => {
        const episodes = filteredEpisodes();
        if (!episodes.length) {
            const empty = document.createElement("p");
            empty.className = "empty-copy";
            empty.textContent = "没有符合条件的 Episode。";
            elements.episodeList.replaceChildren(empty);
            return;
        }
        elements.episodeList.replaceChildren(
            ...episodes.map((episode) => {
                const item = document.createElement("label");
                item.className = "episode-item";
                if (state.selected.has(episode.episode_index)) item.classList.add("selected");
                const checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.checked = state.selected.has(episode.episode_index);
                checkbox.addEventListener("change", () => {
                    if (checkbox.checked) state.selected.add(episode.episode_index);
                    else state.selected.delete(episode.episode_index);
                    renderEpisodes();
                    renderSelectionCount();
                });
                const copy = document.createElement("div");
                const title = document.createElement("strong");
                title.textContent = `Episode ${String(episode.episode_index).padStart(4, "0")}`;
                const task = document.createElement("small");
                task.textContent = episode.task || "未设置 task";
                copy.append(title, task);
                const duration = document.createElement("span");
                duration.className = "episode-duration";
                duration.textContent = episode.duration_seconds == null
                    ? `${episode.length} 帧`
                    : `${episode.duration_seconds.toFixed(1)}s`;
                item.append(checkbox, copy, duration);
                return item;
            })
        );
    };

    const loadWorkspace = async () => {
        const datasetId = elements.source.value;
        if (!datasetId) return;
        showMessage("正在读取数据集……");
        try {
            const payload = await requestJson(`/preprocess/api/datasets/${encodeURIComponent(datasetId)}`);
            state.workspace = {dataset: payload.dataset, episodes: payload.episodes};
            state.selected.clear();
            state.duplicateReport = null;
            elements.duplicateReport.textContent = "尚未扫描。";
            elements.reorder.value = payload.episodes.map((item) => item.episode_index).join(", ");
            renderStats();
            renderEpisodes();
            renderSelectionCount();
            for (const checkbox of elements.mergeDatasets.querySelectorAll("input")) {
                checkbox.checked = checkbox.value === datasetId;
            }
            showMessage("");
        } catch (error) {
            showMessage(error.message, "error");
        }
    };

    const loadDatasets = async () => {
        try {
            const payload = await requestJson("/preprocess/api/datasets");
            state.datasets = payload.datasets;
            renderDatasetOptions();
            if (!state.datasets.length) {
                showMessage("没有找到可处理的 LeRobot v3 数据集。", "info");
                elements.start.disabled = true;
                return;
            }
            await loadWorkspace();
        } catch (error) {
            showMessage(error.message, "error");
        }
    };

    const setOperation = (operation) => {
        state.operation = operation;
        for (const card of document.querySelectorAll(".operation-card")) {
            card.classList.toggle("active", card.querySelector("input").value === operation);
        }
        for (const panel of document.querySelectorAll(".operation-config")) panel.hidden = true;
        document.getElementById(`panel-${operation}`).hidden = false;
    };

    const parseReorder = () => elements.reorder.value
        .split(/[\s,，]+/)
        .filter(Boolean)
        .map((value) => {
            const number = Number(value);
            if (!Number.isInteger(number) || number < 0) throw new Error(`无效 Episode 编号：${value}`);
            return number;
        });

    const buildSplits = () => {
        if (elements.splitMode.value === "ratio") {
            const values = {
                train: Number(document.getElementById("ratio-train").value),
                val: Number(document.getElementById("ratio-val").value),
                test: Number(document.getElementById("ratio-test").value),
            };
            const splits = Object.fromEntries(Object.entries(values).filter(([, value]) => value > 0));
            if (!Object.keys(splits).length) throw new Error("至少填写一个大于 0 的拆分比例。");
            if (Object.values(splits).some((value) => !Number.isFinite(value) || value > 1)) {
                throw new Error("拆分比例必须在 0 到 1 之间。");
            }
            if (Object.values(splits).reduce((sum, value) => sum + value, 0) > 1.000001) {
                throw new Error("拆分比例总和不能超过 1。")
            }
            return splits;
        }
        const selectedName = document.getElementById("split-selected-name").value.trim();
        const restName = document.getElementById("split-rest-name").value.trim();
        const selected = [...state.selected].sort((a, b) => a - b);
        const rest = state.workspace.episodes
            .map((item) => item.episode_index)
            .filter((index) => !state.selected.has(index));
        if (!selectedName || !restName || selectedName === restName) {
            throw new Error("两个拆分组需要填写不同名称。");
        }
        if (!selected.length || !rest.length) throw new Error("两个拆分组都必须至少包含一个 Episode。");
        return {[selectedName]: selected, [restName]: rest};
    };

    const buildPayload = () => {
        if (!state.workspace) throw new Error("请先选择数据集。");
        const versionName = elements.versionName.value.trim();
        if (versionName.length < 2) throw new Error("版本名称至少需要 2 个字符。");
        const payload = {
            operation: state.operation,
            source_dataset_ids: [state.workspace.dataset.dataset_id],
            version_name: versionName,
            description: elements.versionDescription.value.trim(),
            episode_indices: [],
            splits: {},
        };
        if (state.operation === "filter") {
            payload.episode_indices = [...state.selected].sort((a, b) => a - b);
            if (!payload.episode_indices.length) throw new Error("请选择要筛除的 Episode。");
        } else if (state.operation === "split") {
            payload.splits = buildSplits();
        } else if (state.operation === "merge") {
            payload.source_dataset_ids = [...elements.mergeDatasets.querySelectorAll("input:checked")]
                .map((input) => input.value);
            if (payload.source_dataset_ids.length < 2) throw new Error("至少选择两个数据集进行合并。");
        } else if (state.operation === "reorder") {
            payload.episode_indices = parseReorder();
        }
        return payload;
    };

    const renderDuplicateReport = (report) => {
        elements.duplicateReport.replaceChildren();
        const summary = document.createElement("strong");
        summary.textContent = report.duplicate_episode_count
            ? `发现 ${report.duplicate_groups.length} 组，共 ${report.duplicate_episode_count} 个可去重 Episode。`
            : "没有发现精确重复 Episode。";
        elements.duplicateReport.append(summary);
        for (const group of report.duplicate_groups) {
            const item = document.createElement("div");
            item.className = "duplicate-group";
            item.textContent = `保留 ${group.keep}；重复：${group.episodes.slice(1).join(", ")}`;
            elements.duplicateReport.append(item);
        }
    };

    const scanDuplicates = async () => {
        if (!state.workspace) return;
        elements.scanDuplicates.disabled = true;
        elements.duplicateReport.textContent = "正在计算 frame 数据指纹……";
        try {
            const report = await requestJson(
                `/preprocess/api/datasets/${encodeURIComponent(state.workspace.dataset.dataset_id)}/duplicates`,
                {method: "POST"}
            );
            state.duplicateReport = report;
            renderDuplicateReport(report);
        } catch (error) {
            elements.duplicateReport.textContent = error.message;
        } finally {
            elements.scanDuplicates.disabled = false;
        }
    };

    const startJob = async () => {
        let payload;
        try {
            payload = buildPayload();
        } catch (error) {
            showMessage(error.message, "error");
            return;
        }
        elements.start.disabled = true;
        showMessage("正在创建处理任务……");
        try {
            const result = await requestJson("/preprocess/api/jobs", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(payload),
            });
            showMessage(`任务 ${result.job.job_id} 已创建。`, "success");
            elements.versionName.value = "";
            elements.versionDescription.value = "";
            await loadJobs();
        } catch (error) {
            showMessage(error.message, "error");
        } finally {
            elements.start.disabled = false;
        }
    };

    const renderJobs = (jobs) => {
        if (!jobs.length) {
            const empty = document.createElement("p");
            empty.className = "empty-copy";
            empty.textContent = "尚无数据处理任务。";
            elements.jobList.replaceChildren(empty);
            return;
        }
        elements.jobList.replaceChildren(
            ...jobs.map((job) => {
                const item = document.createElement("article");
                item.className = "job-item";
                const row = document.createElement("div");
                row.className = "job-row";
                const title = document.createElement("strong");
                title.textContent = operationLabels[job.operation] || job.operation;
                const status = document.createElement("span");
                status.className = `job-status ${job.status}`;
                status.textContent = statusLabels[job.status] || job.status;
                row.append(title, status);
                const message = document.createElement("p");
                message.textContent = `${job.version_name} · ${job.message}`;
                const track = document.createElement("div");
                track.className = "progress-track";
                const bar = document.createElement("div");
                bar.className = "progress-bar";
                bar.style.width = `${job.progress || 0}%`;
                track.append(bar);
                item.append(row, message, track);
                for (const output of job.outputs || []) {
                    const result = document.createElement("p");
                    result.className = "job-output";
                    result.textContent = `${output.dataset_id} · ${output.total_episodes} Episodes`;
                    item.append(result);
                }
                return item;
            })
        );
    };

    async function loadJobs() {
        try {
            const payload = await requestJson("/preprocess/api/jobs");
            renderJobs(payload.jobs);
        } catch (error) {
            elements.jobList.textContent = error.message;
        }
    }

    elements.source.addEventListener("change", loadWorkspace);
    elements.search.addEventListener("input", renderEpisodes);
    elements.toggleAll.addEventListener("click", () => {
        const episodes = filteredEpisodes();
        const allSelected = episodes.every((episode) => state.selected.has(episode.episode_index));
        for (const episode of episodes) {
            if (allSelected) state.selected.delete(episode.episode_index);
            else state.selected.add(episode.episode_index);
        }
        renderEpisodes();
        renderSelectionCount();
    });
    for (const input of document.querySelectorAll('input[name="operation"]')) {
        input.addEventListener("change", () => setOperation(input.value));
    }
    elements.splitMode.addEventListener("change", () => {
        const ratio = elements.splitMode.value === "ratio";
        elements.splitRatios.hidden = !ratio;
        elements.splitIndexes.hidden = ratio;
    });
    elements.scanDuplicates.addEventListener("click", scanDuplicates);
    elements.start.addEventListener("click", startJob);
    elements.refreshJobs.addEventListener("click", loadJobs);

    setOperation("filter");
    loadDatasets();
    loadJobs();
    window.setInterval(loadJobs, 4000);
})();
