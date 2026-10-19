(() => {
    const datasetSelect = document.getElementById("dataset-select");
    if (!datasetSelect) return;

    const elements = {
        message: document.getElementById("annotation-message"),
        empty: document.getElementById("annotation-empty"),
        workspace: document.getElementById("annotation-workspace"),
        stats: document.getElementById("dataset-stats"),
        search: document.getElementById("episode-search"),
        episodeList: document.getElementById("episode-list"),
        selectAll: document.getElementById("select-all"),
        episodeTitle: document.getElementById("episode-title"),
        episodeMeta: document.getElementById("episode-meta"),
        videoGrid: document.getElementById("video-grid"),
        syncPlay: document.getElementById("sync-play"),
        currentTask: document.getElementById("current-task"),
        selectedCount: document.getElementById("selected-count"),
        taskInput: document.getElementById("task-input"),
        applyTask: document.getElementById("apply-task"),
        pendingList: document.getElementById("pending-list"),
        clearChanges: document.getElementById("clear-changes"),
        versionName: document.getElementById("version-name"),
        versionDescription: document.getElementById("version-description"),
        publish: document.getElementById("publish-version"),
    };

    const state = {
        datasets: [],
        workspace: null,
        currentEpisode: null,
        selected: new Set(),
        pending: new Map(),
    };

    const showMessage = (kind, text) => {
        elements.message.hidden = false;
        elements.message.className = `annotation-message ${kind}`;
        elements.message.textContent = text;
    };

    const hideMessage = () => {
        elements.message.hidden = true;
    };

    const requestJson = async (url, options = {}) => {
        const response = await fetch(url, options);
        let payload;
        try {
            payload = await response.json();
        } catch (_) {
            throw new Error(`服务器返回了无法解析的结果（HTTP ${response.status}）。`);
        }
        if (!response.ok || payload.ok === false) {
            throw new Error(payload.message || `请求失败（HTTP ${response.status}）。`);
        }
        return payload;
    };

    const episodeByIndex = (index) =>
        state.workspace?.episodes.find((episode) => episode.episode_index === index);

    const effectiveTask = (episode) =>
        state.pending.get(episode.episode_index) ?? episode.task;

    const renderStats = () => {
        const dataset = state.workspace.dataset;
        const values = [
            ["数据集编号", dataset.dataset_id],
            ["来源", dataset.kind === "raw" ? "原始数据集" : "派生数据集"],
            ["Episodes", dataset.total_episodes],
            ["Frames", dataset.total_frames.toLocaleString()],
            ["机器人 / FPS", `${dataset.robot_type} · ${dataset.fps ?? "-"} FPS`],
        ];
        elements.stats.replaceChildren(
            ...values.map(([label, value]) => {
                const card = document.createElement("div");
                card.className = "stat-chip";
                const labelNode = document.createElement("span");
                labelNode.textContent = label;
                const valueNode = document.createElement("strong");
                valueNode.textContent = value;
                valueNode.title = String(value);
                card.append(labelNode, valueNode);
                return card;
            })
        );
    };

    const filteredEpisodes = () => {
        const query = elements.search.value.trim().toLowerCase();
        if (!query) return state.workspace?.episodes ?? [];
        return state.workspace.episodes.filter((episode) =>
            `episode ${episode.episode_index} ${effectiveTask(episode)}`.toLowerCase().includes(query)
        );
    };

    const renderEpisodeList = () => {
        const episodes = filteredEpisodes();
        if (!episodes.length) {
            const empty = document.createElement("p");
            empty.className = "episode-empty";
            empty.textContent = "没有符合条件的 Episode。";
            elements.episodeList.replaceChildren(empty);
            return;
        }

        elements.episodeList.replaceChildren(
            ...episodes.map((episode) => {
                const row = document.createElement("div");
                row.className = "episode-row";
                if (state.currentEpisode === episode.episode_index) row.classList.add("active");
                if (state.pending.has(episode.episode_index)) row.classList.add("changed");

                const checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.checked = state.selected.has(episode.episode_index);
                checkbox.setAttribute("aria-label", `选择 Episode ${episode.episode_index}`);
                checkbox.addEventListener("click", (event) => event.stopPropagation());
                checkbox.addEventListener("change", () => {
                    if (checkbox.checked) state.selected.add(episode.episode_index);
                    else state.selected.delete(episode.episode_index);
                    renderSelectedCount();
                });

                const copy = document.createElement("div");
                copy.className = "episode-copy";
                const title = document.createElement("strong");
                title.textContent = `Episode ${String(episode.episode_index).padStart(4, "0")}`;
                const task = document.createElement("span");
                task.textContent = effectiveTask(episode) || "未设置 task";
                copy.append(title, task);

                const duration = document.createElement("span");
                duration.className = "episode-duration";
                duration.textContent = episode.duration_seconds == null
                    ? `${episode.num_frames} 帧`
                    : `${episode.duration_seconds.toFixed(1)}s`;

                row.append(checkbox, copy, duration);
                row.addEventListener("click", () => showEpisode(episode.episode_index));
                return row;
            })
        );
    };

    const renderSelectedCount = () => {
        elements.selectedCount.textContent = `${state.selected.size} 项`;
    };

    const createVideo = (clip) => {
        const card = document.createElement("div");
        card.className = "video-card";
        const label = document.createElement("span");
        label.className = "camera-label";
        label.textContent = clip.camera;
        const video = document.createElement("video");
        video.controls = true;
        video.preload = "metadata";
        video.src = clip.url;
        video.dataset.start = String(clip.start);
        video.dataset.end = String(clip.end);
        video.addEventListener("loadedmetadata", () => {
            if (Number.isFinite(clip.start)) video.currentTime = clip.start;
        });
        video.addEventListener("play", () => {
            if (video.currentTime < clip.start || video.currentTime >= clip.end) {
                video.currentTime = clip.start;
            }
        });
        video.addEventListener("timeupdate", () => {
            if (video.currentTime >= clip.end) {
                video.pause();
                video.currentTime = clip.start;
            }
        });
        card.append(label, video);
        return card;
    };

    const showEpisode = (episodeIndex) => {
        const episode = episodeByIndex(episodeIndex);
        if (!episode) return;
        state.currentEpisode = episodeIndex;
        elements.episodeTitle.textContent = `Episode ${String(episodeIndex).padStart(4, "0")}`;
        const duration = episode.duration_seconds == null ? "时长未知" : `${episode.duration_seconds.toFixed(2)} 秒`;
        elements.episodeMeta.textContent = `${episode.num_frames.toLocaleString()} 帧 · ${duration} · ${episode.videos.length} 路视频`;
        elements.currentTask.textContent = effectiveTask(episode) || "未设置 task";
        elements.taskInput.value = effectiveTask(episode);

        if (episode.videos.length) {
            elements.videoGrid.replaceChildren(...episode.videos.map(createVideo));
            elements.syncPlay.disabled = episode.videos.length < 2;
        } else {
            const placeholder = document.createElement("div");
            placeholder.className = "video-placeholder";
            placeholder.textContent = "这个 Episode 没有可播放的视频索引。";
            elements.videoGrid.replaceChildren(placeholder);
            elements.syncPlay.disabled = true;
        }
        renderEpisodeList();
    };

    const renderPending = () => {
        if (!state.pending.size) {
            const empty = document.createElement("p");
            empty.className = "muted-copy";
            empty.textContent = "尚未修改 task。";
            elements.pendingList.replaceChildren(empty);
            elements.publish.disabled = true;
            return;
        }

        const items = [...state.pending.entries()].sort((a, b) => a[0] - b[0]);
        elements.pendingList.replaceChildren(
            ...items.map(([index, task]) => {
                const item = document.createElement("div");
                item.className = "pending-item";
                const badge = document.createElement("span");
                badge.textContent = `E${index}`;
                const text = document.createElement("p");
                text.textContent = task;
                const undo = document.createElement("button");
                undo.type = "button";
                undo.textContent = "撤销";
                undo.addEventListener("click", () => {
                    state.pending.delete(index);
                    renderPending();
                    renderEpisodeList();
                    if (state.currentEpisode === index) showEpisode(index);
                });
                item.append(badge, text, undo);
                return item;
            })
        );
        elements.publish.disabled = false;
    };

    const resetWorkspaceState = () => {
        state.currentEpisode = null;
        state.selected.clear();
        state.pending.clear();
        elements.search.value = "";
        elements.taskInput.value = "";
        elements.versionName.value = "";
        elements.versionDescription.value = "";
        renderSelectedCount();
        renderPending();
    };

    const loadWorkspace = async (datasetId) => {
        if (!datasetId) return;
        showMessage("info", "正在读取真实 Episode、task 和视频索引…");
        elements.workspace.hidden = true;
        try {
            const payload = await requestJson(`/annotation/api/datasets/${encodeURIComponent(datasetId)}`);
            state.workspace = payload;
            resetWorkspaceState();
            renderStats();
            renderEpisodeList();
            elements.workspace.hidden = false;
            hideMessage();
            if (payload.episodes.length) showEpisode(payload.episodes[0].episode_index);
        } catch (error) {
            showMessage("error", error.message);
        }
    };

    const loadDatasets = async (preferredId = null) => {
        try {
            const payload = await requestJson("/annotation/api/datasets");
            state.datasets = payload.datasets;
            datasetSelect.replaceChildren();
            if (!payload.datasets.length) {
                const option = document.createElement("option");
                option.textContent = "没有可标注的数据集";
                option.value = "";
                datasetSelect.append(option);
                elements.empty.hidden = false;
                elements.workspace.hidden = true;
                return;
            }
            elements.empty.hidden = true;
            for (const dataset of payload.datasets) {
                const option = document.createElement("option");
                option.value = dataset.dataset_id;
                option.textContent = `${dataset.dataset_name} · ${dataset.kind_label} · ${dataset.total_episodes} Episodes`;
                datasetSelect.append(option);
            }
            const selectedId = payload.datasets.some((item) => item.dataset_id === preferredId)
                ? preferredId
                : payload.datasets[0].dataset_id;
            datasetSelect.value = selectedId;
            await loadWorkspace(selectedId);
        } catch (error) {
            showMessage("error", error.message);
        }
    };

    datasetSelect.addEventListener("change", () => loadWorkspace(datasetSelect.value));
    elements.search.addEventListener("input", renderEpisodeList);

    elements.selectAll.addEventListener("click", () => {
        const visible = filteredEpisodes();
        const allSelected = visible.length && visible.every((episode) => state.selected.has(episode.episode_index));
        for (const episode of visible) {
            if (allSelected) state.selected.delete(episode.episode_index);
            else state.selected.add(episode.episode_index);
        }
        elements.selectAll.textContent = allSelected ? "全选" : "取消全选";
        renderSelectedCount();
        renderEpisodeList();
    });

    elements.syncPlay.addEventListener("click", () => {
        const videos = [...elements.videoGrid.querySelectorAll("video")];
        for (const video of videos) video.currentTime = Number(video.dataset.start || 0);
        Promise.allSettled(videos.map((video) => video.play()));
    });

    elements.applyTask.addEventListener("click", () => {
        const task = elements.taskInput.value.trim();
        if (!task) {
            showMessage("error", "请先填写新的 task 提示词。 ");
            return;
        }
        const targets = state.selected.size
            ? [...state.selected]
            : state.currentEpisode == null ? [] : [state.currentEpisode];
        if (!targets.length) {
            showMessage("error", "请至少选择一个 Episode。 ");
            return;
        }
        for (const index of targets) {
            const episode = episodeByIndex(index);
            if (!episode) continue;
            if (episode.task === task) state.pending.delete(index);
            else state.pending.set(index, task);
        }
        hideMessage();
        renderPending();
        renderEpisodeList();
        if (state.currentEpisode != null) showEpisode(state.currentEpisode);
    });

    elements.clearChanges.addEventListener("click", () => {
        state.pending.clear();
        renderPending();
        renderEpisodeList();
        if (state.currentEpisode != null) showEpisode(state.currentEpisode);
    });

    elements.publish.addEventListener("click", async () => {
        const versionName = elements.versionName.value.trim();
        if (versionName.length < 2) {
            showMessage("error", "请填写至少 2 个字符的派生版本名称。 ");
            elements.versionName.focus();
            return;
        }
        if (!state.pending.size) return;
        const confirmed = window.confirm(
            `将复制完整数据集并发布 ${state.pending.size} 个 Episode 的 task 修改。源数据不会改变，是否继续？`
        );
        if (!confirmed) return;

        const originalText = elements.publish.textContent;
        elements.publish.disabled = true;
        elements.publish.textContent = "正在复制、修改并校验…";
        showMessage("info", "正在创建完整派生数据集。数据较大时需要等待，请勿关闭页面。 ");
        try {
            const payload = await requestJson("/annotation/api/publish", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    dataset_id: state.workspace.dataset.dataset_id,
                    version_name: versionName,
                    description: elements.versionDescription.value.trim(),
                    changes: [...state.pending.entries()].map(([episodeIndex, task]) => ({
                        episode_index: episodeIndex,
                        task,
                    })),
                }),
            });
            await loadDatasets(payload.dataset_id);
            showMessage(
                "success",
                `发布成功：${payload.version_name}，生成完整派生数据集 ${payload.dataset_id}；源数据未修改。`
            );
        } catch (error) {
            showMessage("error", error.message);
        } finally {
            elements.publish.textContent = originalText;
            elements.publish.disabled = state.pending.size === 0;
        }
    });

    loadDatasets();
})();
