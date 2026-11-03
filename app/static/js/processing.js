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
        cropVideoKey: document.getElementById("crop-video-key"),
        cropEpisode: document.getElementById("crop-episode"),
        cropPreviewEmpty: document.getElementById("crop-preview-empty"),
        cropStage: document.getElementById("crop-stage"),
        cropVideo: document.getElementById("crop-video"),
        cropOverlay: document.getElementById("crop-overlay"),
        cropSelection: document.getElementById("crop-selection"),
        cropSizeBadge: document.getElementById("crop-size-badge"),
        cropPlayback: document.getElementById("crop-playback"),
        cropPlayToggle: document.getElementById("crop-play-toggle"),
        cropTimeline: document.getElementById("crop-timeline"),
        cropTime: document.getElementById("crop-time"),
        cropReset: document.getElementById("crop-reset"),
        cropSourceSize: document.getElementById("crop-source-size"),
        cropX: document.getElementById("crop-x"),
        cropY: document.getElementById("crop-y"),
        cropWidth: document.getElementById("crop-width"),
        cropHeight: document.getElementById("crop-height"),
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
        crop: {
            sourceWidth: 0,
            sourceHeight: 0,
            start: 0,
            end: 0,
            interaction: null,
            resetOnLoad: true,
        },
    };

    const operationLabels = {
        filter: "Episode 筛除",
        split: "数据集拆分",
        merge: "数据集合并",
        reorder: "Episode 重排",
        deduplicate: "重复检测与去重",
        video_crop: "视频画面裁剪",
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
        const kind = {raw: "原始", derived: "派生"}[dataset.kind] || dataset.kind;
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

    const selectedVideoFeature = () => state.workspace?.dataset.video_features?.find(
        (item) => item.key === elements.cropVideoKey.value
    );

    const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));

    const currentCrop = () => ({
        x: Number(elements.cropX.value),
        y: Number(elements.cropY.value),
        width: Number(elements.cropWidth.value),
        height: Number(elements.cropHeight.value),
    });

    const normalizeCrop = (crop) => {
        const sourceWidth = state.crop.sourceWidth;
        const sourceHeight = state.crop.sourceHeight;
        if (sourceWidth < 2 || sourceHeight < 2) return null;
        const x = clamp(Math.round(Number(crop.x) || 0), 0, sourceWidth - 2);
        const y = clamp(Math.round(Number(crop.y) || 0), 0, sourceHeight - 2);
        const maxWidth = sourceWidth - x;
        const maxHeight = sourceHeight - y;
        let width = clamp(Math.round(Number(crop.width) || 2), 2, maxWidth);
        let height = clamp(Math.round(Number(crop.height) || 2), 2, maxHeight);
        width = Math.max(2, Math.floor(width / 2) * 2);
        height = Math.max(2, Math.floor(height / 2) * 2);
        return {x, y, width, height};
    };

    const renderCropSelection = () => {
        const crop = normalizeCrop(currentCrop());
        if (!crop) {
            elements.cropSelection.hidden = true;
            return;
        }
        elements.cropSelection.hidden = false;
        elements.cropSelection.style.left = `${crop.x / state.crop.sourceWidth * 100}%`;
        elements.cropSelection.style.top = `${crop.y / state.crop.sourceHeight * 100}%`;
        elements.cropSelection.style.width = `${crop.width / state.crop.sourceWidth * 100}%`;
        elements.cropSelection.style.height = `${crop.height / state.crop.sourceHeight * 100}%`;
        elements.cropSizeBadge.textContent = `${crop.width} × ${crop.height}`;
        elements.cropSourceSize.textContent = (
            `原始 ${state.crop.sourceWidth} × ${state.crop.sourceHeight} · `
            + `范围 (${crop.x}, ${crop.y}) → (${crop.x + crop.width}, ${crop.y + crop.height}) · `
            + `输出 ${crop.width} × ${crop.height}`
        );
    };

    const applyCrop = (crop) => {
        const normalized = normalizeCrop(crop);
        if (!normalized) return;
        elements.cropX.value = String(normalized.x);
        elements.cropY.value = String(normalized.y);
        elements.cropWidth.value = String(normalized.width);
        elements.cropHeight.value = String(normalized.height);
        renderCropSelection();
    };

    const resetCropToFrame = () => {
        if (state.crop.sourceWidth < 2 || state.crop.sourceHeight < 2) return;
        applyCrop({
            x: 0,
            y: 0,
            width: Math.floor(state.crop.sourceWidth / 2) * 2,
            height: Math.floor(state.crop.sourceHeight / 2) * 2,
        });
    };

    const cropEpisodes = () => {
        const camera = elements.cropVideoKey.value;
        return (state.workspace?.episodes || []).filter((episode) =>
            episode.videos?.some((video) => video.camera === camera)
        );
    };

    const selectedCropVideo = () => {
        const episodeIndex = Number(elements.cropEpisode.value);
        const episode = cropEpisodes().find((item) => item.episode_index === episodeIndex);
        return episode?.videos?.find((video) => video.camera === elements.cropVideoKey.value);
    };

    const updateCropTime = () => {
        const current = clamp(
            Number(elements.cropVideo.currentTime) || state.crop.start,
            state.crop.start,
            state.crop.end,
        );
        elements.cropTimeline.value = String(current);
        elements.cropTime.textContent = (
            `${Math.max(0, current - state.crop.start).toFixed(2)} / `
            + `${Math.max(0, state.crop.end - state.crop.start).toFixed(2)}s`
        );
    };

    const loadCropVideo = ({resetCrop = false} = {}) => {
        const video = selectedCropVideo();
        elements.cropVideo.pause();
        elements.cropPlayToggle.textContent = "播放";
        state.crop.resetOnLoad = resetCrop;
        if (!video) {
            elements.cropStage.hidden = true;
            elements.cropPlayback.hidden = true;
            elements.cropPreviewEmpty.hidden = false;
            elements.cropPreviewEmpty.textContent = "所选相机没有可预览的 Episode 视频。";
            elements.cropSourceSize.textContent = "当前相机没有可裁剪的视频。";
            elements.cropVideo.removeAttribute("src");
            elements.cropVideo.load();
            return;
        }
        state.crop.start = Number(video.start) || 0;
        state.crop.end = Number(video.end) || state.crop.start;
        elements.cropPreviewEmpty.hidden = true;
        elements.cropStage.hidden = false;
        elements.cropPlayback.hidden = false;
        elements.cropSourceSize.textContent = "正在读取真实视频尺寸……";
        elements.cropVideo.src = video.url;
        elements.cropVideo.load();
    };

    const renderCropEpisodes = ({resetCrop = false} = {}) => {
        const previous = elements.cropEpisode.value;
        const episodes = cropEpisodes();
        elements.cropEpisode.replaceChildren(
            ...episodes.map((episode) => {
                const option = document.createElement("option");
                option.value = String(episode.episode_index);
                option.textContent = `Episode ${String(episode.episode_index).padStart(4, "0")} · ${episode.task || "未设置 task"}`;
                return option;
            })
        );
        if (episodes.some((episode) => String(episode.episode_index) === previous)) {
            elements.cropEpisode.value = previous;
        }
        loadCropVideo({resetCrop});
    };

    const showCropSourceSize = () => {
        state.crop.sourceWidth = 0;
        state.crop.sourceHeight = 0;
        renderCropEpisodes({resetCrop: true});
    };

    const renderVideoFeatures = () => {
        const features = state.workspace?.dataset.video_features || [];
        elements.cropVideoKey.replaceChildren(
            ...features.map((feature) => {
                const option = document.createElement("option");
                option.value = feature.key;
                option.textContent = feature.width && feature.height
                    ? `${feature.key} · ${feature.width}×${feature.height}`
                    : feature.key;
                return option;
            })
        );
        showCropSourceSize();
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
            renderVideoFeatures();
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

    const loadDatasets = async (preferredId = null) => {
        try {
            const payload = await requestJson("/preprocess/api/datasets");
            state.datasets = payload.datasets;
            renderDatasetOptions();
            if (!state.datasets.length) {
                showMessage("没有找到可处理的 LeRobot v3 数据集。", "info");
                elements.start.disabled = true;
                return false;
            }
            const selectedId = state.datasets.some(
                (dataset) => dataset.dataset_id === preferredId
            )
                ? preferredId
                : state.datasets[0].dataset_id;
            elements.source.value = selectedId;
            await loadWorkspace();
            return true;
        } catch (error) {
            showMessage(error.message, "error");
            return false;
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

    const cropSourcePoint = (event) => {
        const bounds = elements.cropOverlay.getBoundingClientRect();
        return {
            x: clamp(
                (event.clientX - bounds.left) / bounds.width * state.crop.sourceWidth,
                0,
                state.crop.sourceWidth,
            ),
            y: clamp(
                (event.clientY - bounds.top) / bounds.height * state.crop.sourceHeight,
                0,
                state.crop.sourceHeight,
            ),
        };
    };

    const beginCropInteraction = (event) => {
        const initial = normalizeCrop(currentCrop());
        if (!initial || event.button !== 0) return;
        const handle = event.target.closest("[data-crop-handle]")?.dataset.cropHandle;
        const mode = handle
            || (elements.cropSelection.contains(event.target) ? "move" : "new");
        state.crop.interaction = {
            pointerId: event.pointerId,
            mode,
            start: cropSourcePoint(event),
            initial,
        };
        elements.cropOverlay.setPointerCapture(event.pointerId);
        event.preventDefault();
    };

    const moveCropInteraction = (event) => {
        const interaction = state.crop.interaction;
        if (!interaction || interaction.pointerId !== event.pointerId) return;
        const point = cropSourcePoint(event);
        const dx = point.x - interaction.start.x;
        const dy = point.y - interaction.start.y;
        const initial = interaction.initial;
        let left = initial.x;
        let top = initial.y;
        let right = initial.x + initial.width;
        let bottom = initial.y + initial.height;

        if (interaction.mode === "new") {
            left = Math.min(interaction.start.x, point.x);
            right = Math.max(interaction.start.x, point.x);
            top = Math.min(interaction.start.y, point.y);
            bottom = Math.max(interaction.start.y, point.y);
        } else if (interaction.mode === "move") {
            const width = initial.width;
            const height = initial.height;
            left = clamp(initial.x + dx, 0, state.crop.sourceWidth - width);
            top = clamp(initial.y + dy, 0, state.crop.sourceHeight - height);
            right = left + width;
            bottom = top + height;
        } else {
            if (interaction.mode.includes("w")) {
                left = clamp(initial.x + dx, 0, right - 2);
            }
            if (interaction.mode.includes("e")) {
                right = clamp(right + dx, left + 2, state.crop.sourceWidth);
            }
            if (interaction.mode.includes("n")) {
                top = clamp(initial.y + dy, 0, bottom - 2);
            }
            if (interaction.mode.includes("s")) {
                bottom = clamp(bottom + dy, top + 2, state.crop.sourceHeight);
            }
        }
        applyCrop({x: left, y: top, width: right - left, height: bottom - top});
        event.preventDefault();
    };

    const endCropInteraction = (event) => {
        if (!state.crop.interaction || state.crop.interaction.pointerId !== event.pointerId) return;
        state.crop.interaction = null;
        if (elements.cropOverlay.hasPointerCapture(event.pointerId)) {
            elements.cropOverlay.releasePointerCapture(event.pointerId);
        }
        applyCrop(currentCrop());
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
        } else if (state.operation === "video_crop") {
            const feature = selectedVideoFeature();
            if (!feature) throw new Error("当前数据集没有可裁剪的视频。");
            if (!state.crop.sourceWidth || !state.crop.sourceHeight) {
                throw new Error("请等待视频预览加载完成后再创建裁剪任务。");
            }
            const values = {
                x: Number(elements.cropX.value),
                y: Number(elements.cropY.value),
                width: Number(elements.cropWidth.value),
                height: Number(elements.cropHeight.value),
            };
            if (!Number.isInteger(values.x) || values.x < 0 || !Number.isInteger(values.y) || values.y < 0) {
                throw new Error("裁剪起点 X、Y 必须是非负整数。");
            }
            if (!Number.isInteger(values.width) || values.width < 2 || !Number.isInteger(values.height) || values.height < 2) {
                throw new Error("裁剪宽度和高度必须是至少为 2 的整数。");
            }
            if (values.width % 2 || values.height % 2) {
                throw new Error("H.264 输出的裁剪宽度和高度必须为偶数。");
            }
            if (values.x + values.width > state.crop.sourceWidth) {
                throw new Error("裁剪区域右边界超出原视频宽度。");
            }
            if (values.y + values.height > state.crop.sourceHeight) {
                throw new Error("裁剪区域下边界超出原视频高度。");
            }
            payload.video_crop = {video_key: feature.key, ...values};
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
                    const result = document.createElement("button");
                    result.type = "button";
                    result.className = "job-output";
                    const label = document.createElement("span");
                    label.textContent = `${output.dataset_id} · ${output.total_episodes} Episodes`;
                    const action = document.createElement("small");
                    action.textContent = "载入派生数据集 →";
                    result.append(label, action);
                    result.addEventListener("click", async () => {
                        const loaded = await loadDatasets(output.dataset_id);
                        if (!loaded) return;
                        showMessage(`已载入派生数据集 ${output.dataset_id}。`, "success");
                        document.querySelector(".source-panel")?.scrollIntoView({
                            behavior: "smooth",
                            block: "start",
                        });
                    });
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
    elements.cropVideoKey.addEventListener("change", showCropSourceSize);
    elements.cropEpisode.addEventListener("change", () => loadCropVideo({resetCrop: false}));
    elements.cropVideo.addEventListener("loadedmetadata", () => {
        const width = Number(elements.cropVideo.videoWidth);
        const height = Number(elements.cropVideo.videoHeight);
        if (width < 2 || height < 2) {
            elements.cropSourceSize.textContent = "视频没有提供有效的画面尺寸。";
            return;
        }
        state.crop.sourceWidth = width;
        state.crop.sourceHeight = height;
        const duration = Number(elements.cropVideo.duration);
        if (Number.isFinite(duration)) {
            state.crop.start = clamp(state.crop.start, 0, duration);
            state.crop.end = clamp(state.crop.end, state.crop.start, duration);
        }
        elements.cropX.max = String(width - 2);
        elements.cropY.max = String(height - 2);
        elements.cropWidth.max = String(width);
        elements.cropHeight.max = String(height);
        const crop = normalizeCrop(currentCrop());
        const needsReset = state.crop.resetOnLoad
            || !crop
            || crop.x + crop.width > width
            || crop.y + crop.height > height;
        if (needsReset) resetCropToFrame();
        else applyCrop(crop);
        state.crop.resetOnLoad = false;
        const fps = Number(state.workspace?.dataset.fps) || 25;
        elements.cropTimeline.min = String(state.crop.start);
        elements.cropTimeline.max = String(state.crop.end);
        elements.cropTimeline.step = String(1 / fps);
        elements.cropTimeline.value = String(state.crop.start);
        elements.cropVideo.currentTime = state.crop.start;
        updateCropTime();
    });
    elements.cropVideo.addEventListener("loadeddata", renderCropSelection);
    elements.cropVideo.addEventListener("timeupdate", () => {
        if (elements.cropVideo.currentTime >= state.crop.end - 0.001) {
            elements.cropVideo.pause();
            elements.cropPlayToggle.textContent = "播放";
        }
        updateCropTime();
    });
    elements.cropVideo.addEventListener("play", () => {
        elements.cropPlayToggle.textContent = "暂停";
    });
    elements.cropVideo.addEventListener("pause", () => {
        elements.cropPlayToggle.textContent = "播放";
    });
    elements.cropVideo.addEventListener("error", () => {
        elements.cropPreviewEmpty.hidden = false;
        elements.cropPreviewEmpty.textContent = "视频预览加载失败，请检查该 Episode 的视频文件。";
        elements.cropStage.hidden = true;
        elements.cropPlayback.hidden = true;
        state.crop.sourceWidth = 0;
        state.crop.sourceHeight = 0;
    });
    elements.cropPlayToggle.addEventListener("click", async () => {
        if (elements.cropVideo.paused) {
            if (elements.cropVideo.currentTime >= state.crop.end - 0.001) {
                elements.cropVideo.currentTime = state.crop.start;
            }
            try {
                await elements.cropVideo.play();
            } catch (error) {
                showMessage(`视频无法播放：${error.message}`, "error");
            }
        } else {
            elements.cropVideo.pause();
        }
    });
    elements.cropTimeline.addEventListener("input", () => {
        elements.cropVideo.currentTime = Number(elements.cropTimeline.value);
        updateCropTime();
    });
    elements.cropReset.addEventListener("click", resetCropToFrame);
    for (const input of [elements.cropX, elements.cropY, elements.cropWidth, elements.cropHeight]) {
        input.addEventListener("change", () => applyCrop(currentCrop()));
    }
    elements.cropOverlay.addEventListener("pointerdown", beginCropInteraction);
    elements.cropOverlay.addEventListener("pointermove", moveCropInteraction);
    elements.cropOverlay.addEventListener("pointerup", endCropInteraction);
    elements.cropOverlay.addEventListener("pointercancel", endCropInteraction);
    elements.start.addEventListener("click", startJob);
    elements.refreshJobs.addEventListener("click", loadJobs);

    setOperation("filter");
    loadDatasets();
    loadJobs();
    window.setInterval(loadJobs, 4000);
})();
