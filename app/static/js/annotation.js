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
        timeline: document.getElementById("segment-timeline"),
        timelineDuration: document.getElementById("timeline-duration"),
        selectedCount: document.getElementById("selected-count"),
        taskInput: document.getElementById("task-input"),
        applyTask: document.getElementById("apply-task"),
        episodeOutcome: document.getElementById("episode-outcome"),
        failureReasonWrap: document.getElementById("failure-reason-wrap"),
        failureReason: document.getElementById("failure-reason"),
        segmentStart: document.getElementById("segment-start"),
        segmentEnd: document.getElementById("segment-end"),
        captureStart: document.getElementById("capture-start"),
        captureEnd: document.getElementById("capture-end"),
        segmentDescription: document.getElementById("segment-description"),
        segmentAction: document.getElementById("segment-action"),
        segmentObject: document.getElementById("segment-object"),
        segmentOutcome: document.getElementById("segment-outcome"),
        addSegment: document.getElementById("add-segment"),
        cancelSegmentEdit: document.getElementById("cancel-segment-edit"),
        segmentList: document.getElementById("segment-list"),
        saveEpisodeAnnotation: document.getElementById("save-episode-annotation"),
        pendingList: document.getElementById("pending-list"),
        clearChanges: document.getElementById("clear-changes"),
        versionName: document.getElementById("version-name"),
        versionDescription: document.getElementById("version-description"),
        publish: document.getElementById("publish-version"),
    };

    const outcomeLabels = {
        success: "成功",
        partial: "部分成功",
        failure: "失败",
    };

    const state = {
        datasets: [],
        workspace: null,
        currentEpisode: null,
        selected: new Set(),
        taskPending: new Map(),
        annotationPending: new Map(),
        workingAnnotations: new Map(),
        editingSegmentIndex: null,
    };

    const clone = (value) => JSON.parse(JSON.stringify(value));

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
        state.taskPending.get(episode.episode_index) ?? episode.task;

    const emptyAnnotation = () => ({
        outcome: "",
        failure_reason: "",
        subtasks: [],
    });

    const annotationForPayload = (annotation) => ({
        outcome: annotation?.outcome || "",
        failure_reason: annotation?.failure_reason?.trim() || "",
        subtasks: (annotation?.subtasks || []).map((segment) => ({
            start_time: Number(segment.start_time),
            end_time: Number(segment.end_time),
            description: String(segment.description || "").trim(),
            action_type: String(segment.action_type || "").trim(),
            object_name: String(segment.object_name || "").trim(),
            outcome: segment.outcome,
        })),
    });

    const sourceAnnotation = (episode) => annotationForPayload(
        episode?.annotation || emptyAnnotation()
    );

    const draftForEpisode = (episode) => {
        if (!episode) return emptyAnnotation();
        const index = episode.episode_index;
        if (state.workingAnnotations.has(index)) {
            return state.workingAnnotations.get(index);
        }
        const initial = clone(
            state.annotationPending.get(index) || sourceAnnotation(episode)
        );
        state.workingAnnotations.set(index, initial);
        return initial;
    };

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
            `episode ${episode.episode_index} ${effectiveTask(episode)}`
                .toLowerCase()
                .includes(query)
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
                if (
                    state.taskPending.has(episode.episode_index)
                    || state.annotationPending.has(episode.episode_index)
                ) row.classList.add("changed");

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

                const annotation = state.annotationPending.get(episode.episode_index)
                    || sourceAnnotation(episode);
                const status = document.createElement("span");
                status.className = `episode-status ${annotation.outcome || "empty"}`;
                status.textContent = annotation.outcome
                    ? outcomeLabels[annotation.outcome]
                    : episode.duration_seconds == null
                        ? `${episode.num_frames} 帧`
                        : `${episode.duration_seconds.toFixed(1)}s`;

                row.append(checkbox, copy, status);
                row.addEventListener("click", () => {
                    rememberCurrentForm();
                    showEpisode(episode.episode_index);
                });
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

    const toggleFailureReason = () => {
        elements.failureReasonWrap.hidden = elements.episodeOutcome.value === "success"
            || elements.episodeOutcome.value === "";
    };

    const rememberCurrentForm = () => {
        if (state.currentEpisode == null) return;
        const episode = episodeByIndex(state.currentEpisode);
        const draft = draftForEpisode(episode);
        draft.outcome = elements.episodeOutcome.value;
        draft.failure_reason = elements.failureReason.value;
        state.workingAnnotations.set(state.currentEpisode, draft);
    };

    const seekToSegment = (segment) => {
        const videos = [...elements.videoGrid.querySelectorAll("video")];
        for (const video of videos) {
            video.pause();
            video.currentTime = Number(video.dataset.start || 0) + segment.start_time;
        }
    };

    const renderTimeline = () => {
        const episode = episodeByIndex(state.currentEpisode);
        const draft = draftForEpisode(episode);
        const duration = Number(episode?.duration_seconds || 0);
        elements.timelineDuration.textContent = duration ? `0 – ${duration.toFixed(3)} 秒` : "—";
        if (!draft.subtasks.length || !duration) {
            const empty = document.createElement("span");
            empty.className = "timeline-empty";
            empty.textContent = "当前 Episode 尚无 Subtask 标注";
            elements.timeline.replaceChildren(empty);
            return;
        }
        elements.timeline.replaceChildren(
            ...draft.subtasks.map((segment, index) => {
                const bar = document.createElement("button");
                bar.type = "button";
                bar.className = `timeline-segment ${segment.outcome}`;
                bar.style.left = `${Math.max(0, segment.start_time / duration * 100)}%`;
                bar.style.width = `${Math.max(1.5, (segment.end_time - segment.start_time) / duration * 100)}%`;
                bar.title = `${segment.description} · ${segment.start_time.toFixed(3)}–${segment.end_time.toFixed(3)}s`;
                bar.textContent = String(index + 1);
                bar.addEventListener("click", () => seekToSegment(segment));
                return bar;
            })
        );
    };

    const clearSegmentForm = () => {
        state.editingSegmentIndex = null;
        elements.segmentStart.value = "";
        elements.segmentEnd.value = "";
        elements.segmentDescription.value = "";
        elements.segmentAction.value = "";
        elements.segmentObject.value = "";
        elements.segmentOutcome.value = "success";
        elements.addSegment.textContent = "加入 Subtask 列表";
        elements.cancelSegmentEdit.hidden = true;
    };

    const renderSegments = () => {
        const episode = episodeByIndex(state.currentEpisode);
        const draft = draftForEpisode(episode);
        if (!draft.subtasks.length) {
            const empty = document.createElement("p");
            empty.className = "muted-copy";
            empty.textContent = "尚未添加 Subtask。";
            elements.segmentList.replaceChildren(empty);
            renderTimeline();
            return;
        }
        elements.segmentList.replaceChildren(
            ...draft.subtasks.map((segment, index) => {
                const item = document.createElement("div");
                item.className = "segment-item";
                const main = document.createElement("div");
                const title = document.createElement("strong");
                title.textContent = `${index + 1}. ${segment.description}`;
                const meta = document.createElement("span");
                meta.textContent = `${segment.start_time.toFixed(3)}–${segment.end_time.toFixed(3)}s · ${segment.action_type} · ${segment.object_name} · ${outcomeLabels[segment.outcome]}`;
                main.append(title, meta);
                const actions = document.createElement("div");
                actions.className = "segment-actions";
                const locate = document.createElement("button");
                locate.type = "button";
                locate.textContent = "定位";
                locate.addEventListener("click", () => seekToSegment(segment));
                const edit = document.createElement("button");
                edit.type = "button";
                edit.textContent = "编辑";
                edit.addEventListener("click", () => {
                    state.editingSegmentIndex = index;
                    elements.segmentStart.value = segment.start_time.toFixed(3);
                    elements.segmentEnd.value = segment.end_time.toFixed(3);
                    elements.segmentDescription.value = segment.description;
                    elements.segmentAction.value = segment.action_type;
                    elements.segmentObject.value = segment.object_name;
                    elements.segmentOutcome.value = segment.outcome;
                    elements.addSegment.textContent = "保存该 Subtask";
                    elements.cancelSegmentEdit.hidden = false;
                });
                const remove = document.createElement("button");
                remove.type = "button";
                remove.textContent = "删除";
                remove.addEventListener("click", () => {
                    draft.subtasks.splice(index, 1);
                    state.workingAnnotations.set(episode.episode_index, draft);
                    clearSegmentForm();
                    renderSegments();
                });
                actions.append(locate, edit, remove);
                item.append(main, actions);
                return item;
            })
        );
        renderTimeline();
    };

    const showEpisode = (episodeIndex) => {
        const episode = episodeByIndex(episodeIndex);
        if (!episode) return;
        state.currentEpisode = episodeIndex;
        state.editingSegmentIndex = null;
        const draft = draftForEpisode(episode);
        elements.episodeTitle.textContent = `Episode ${String(episodeIndex).padStart(4, "0")}`;
        const duration = episode.duration_seconds == null
            ? "时长未知"
            : `${episode.duration_seconds.toFixed(3)} 秒`;
        elements.episodeMeta.textContent = `${episode.num_frames.toLocaleString()} 帧 · ${duration} · ${episode.videos.length} 路视频`;
        elements.currentTask.textContent = effectiveTask(episode) || "未设置 task";
        elements.taskInput.value = effectiveTask(episode);
        elements.episodeOutcome.value = draft.outcome || "";
        elements.failureReason.value = draft.failure_reason || "";
        toggleFailureReason();
        clearSegmentForm();

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
        renderSegments();
        renderEpisodeList();
    };

    const renderPending = () => {
        const indexes = [...new Set([
            ...state.taskPending.keys(),
            ...state.annotationPending.keys(),
        ])].sort((a, b) => a - b);
        if (!indexes.length) {
            const empty = document.createElement("p");
            empty.className = "muted-copy";
            empty.textContent = "尚未暂存任何修改。";
            elements.pendingList.replaceChildren(empty);
            elements.publish.disabled = true;
            return;
        }

        elements.pendingList.replaceChildren(
            ...indexes.map((index) => {
                const item = document.createElement("div");
                item.className = "pending-item";
                const badge = document.createElement("span");
                badge.textContent = `E${index}`;
                const text = document.createElement("p");
                const summaries = [];
                if (state.taskPending.has(index)) summaries.push("task");
                if (state.annotationPending.has(index)) {
                    const annotation = state.annotationPending.get(index);
                    summaries.push(
                        `结果：${outcomeLabels[annotation.outcome]}，${annotation.subtasks.length} 个 Subtask`
                    );
                }
                text.textContent = summaries.join("；");
                const undo = document.createElement("button");
                undo.type = "button";
                undo.textContent = "撤销";
                undo.addEventListener("click", () => {
                    state.taskPending.delete(index);
                    state.annotationPending.delete(index);
                    state.workingAnnotations.delete(index);
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
        state.taskPending.clear();
        state.annotationPending.clear();
        state.workingAnnotations.clear();
        state.editingSegmentIndex = null;
        elements.search.value = "";
        elements.taskInput.value = "";
        elements.versionName.value = "";
        elements.versionDescription.value = "";
        renderSelectedCount();
        renderPending();
    };

    const loadWorkspace = async (datasetId) => {
        if (!datasetId) return;
        showMessage("info", "正在读取真实 Episode、task、视频索引和扩展标注…");
        elements.workspace.hidden = true;
        try {
            const payload = await requestJson(
                `/annotation/api/datasets/${encodeURIComponent(datasetId)}`
            );
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

    const captureVideoTime = (target) => {
        const video = elements.videoGrid.querySelector("video");
        if (!video) {
            showMessage("error", "当前 Episode 没有可用视频，无法获取播放时间。 ");
            return;
        }
        const episode = episodeByIndex(state.currentEpisode);
        const relative = Math.max(0, video.currentTime - Number(video.dataset.start || 0));
        const limited = Math.min(relative, Number(episode.duration_seconds || relative));
        target.value = limited.toFixed(3);
        hideMessage();
    };

    datasetSelect.addEventListener("change", () => loadWorkspace(datasetSelect.value));
    elements.search.addEventListener("input", renderEpisodeList);

    elements.selectAll.addEventListener("click", () => {
        const visible = filteredEpisodes();
        const allSelected = visible.length
            && visible.every((episode) => state.selected.has(episode.episode_index));
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
            if (episode.task === task) state.taskPending.delete(index);
            else state.taskPending.set(index, task);
        }
        hideMessage();
        renderPending();
        renderEpisodeList();
        if (state.currentEpisode != null) showEpisode(state.currentEpisode);
    });

    elements.episodeOutcome.addEventListener("change", () => {
        toggleFailureReason();
        rememberCurrentForm();
    });
    elements.failureReason.addEventListener("input", rememberCurrentForm);
    elements.captureStart.addEventListener("click", () => captureVideoTime(elements.segmentStart));
    elements.captureEnd.addEventListener("click", () => captureVideoTime(elements.segmentEnd));

    elements.addSegment.addEventListener("click", () => {
        const episode = episodeByIndex(state.currentEpisode);
        if (!episode) return;
        rememberCurrentForm();
        const draft = draftForEpisode(episode);
        const segment = {
            start_time: Number(elements.segmentStart.value),
            end_time: Number(elements.segmentEnd.value),
            description: elements.segmentDescription.value.trim(),
            action_type: elements.segmentAction.value.trim(),
            object_name: elements.segmentObject.value.trim(),
            outcome: elements.segmentOutcome.value,
        };
        if (!Number.isFinite(segment.start_time) || !Number.isFinite(segment.end_time)) {
            showMessage("error", "请填写有效的开始和结束时间。 ");
            return;
        }
        if (segment.start_time < 0 || segment.end_time <= segment.start_time) {
            showMessage("error", "结束时间必须晚于开始时间。 ");
            return;
        }
        if (segment.end_time > Number(episode.duration_seconds) + 0.000001) {
            showMessage("error", "Subtask 结束时间不能超过当前 Episode 时长。 ");
            return;
        }
        if (!segment.description || !segment.action_type || !segment.object_name) {
            showMessage("error", "请完整填写子任务描述、动作类型和对象。 ");
            return;
        }

        const next = draft.subtasks.filter((_, index) => index !== state.editingSegmentIndex);
        next.push(segment);
        next.sort((a, b) => a.start_time - b.start_time || a.end_time - b.end_time);
        if (next.some((item, index) => index > 0 && item.start_time < next[index - 1].end_time)) {
            showMessage("error", "Subtask 时间段不能重叠；可以相邻，也可以留空档。 ");
            return;
        }
        draft.subtasks = next;
        state.workingAnnotations.set(episode.episode_index, draft);
        clearSegmentForm();
        renderSegments();
        hideMessage();
    });

    elements.cancelSegmentEdit.addEventListener("click", clearSegmentForm);

    elements.saveEpisodeAnnotation.addEventListener("click", () => {
        const episode = episodeByIndex(state.currentEpisode);
        if (!episode) return;
        rememberCurrentForm();
        const annotation = annotationForPayload(draftForEpisode(episode));
        if (!annotation.outcome) {
            showMessage("error", "请选择当前 Episode 的执行结果。 ");
            return;
        }
        if (["partial", "failure"].includes(annotation.outcome) && !annotation.failure_reason) {
            showMessage("error", "部分成功或失败时必须填写原因。 ");
            elements.failureReason.focus();
            return;
        }
        if (annotation.outcome === "success") annotation.failure_reason = "";

        if (JSON.stringify(annotation) === JSON.stringify(sourceAnnotation(episode))) {
            state.annotationPending.delete(episode.episode_index);
        } else {
            state.annotationPending.set(episode.episode_index, annotation);
        }
        state.workingAnnotations.set(episode.episode_index, clone(annotation));
        renderPending();
        renderEpisodeList();
        showMessage("success", `Episode ${episode.episode_index} 已暂存，正式发布前不会写入数据集。`);
    });

    elements.clearChanges.addEventListener("click", () => {
        state.taskPending.clear();
        state.annotationPending.clear();
        state.workingAnnotations.clear();
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
        if (!state.taskPending.size && !state.annotationPending.size) return;
        const changedEpisodes = new Set([
            ...state.taskPending.keys(),
            ...state.annotationPending.keys(),
        ]).size;
        const confirmed = window.confirm(
            `将复制完整数据集并发布 ${changedEpisodes} 个 Episode 的标注。源数据不会改变，是否继续？`
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
                    changes: [...state.taskPending.entries()].map(([episodeIndex, task]) => ({
                        episode_index: episodeIndex,
                        task,
                    })),
                    annotations: [...state.annotationPending.entries()].map(
                        ([episodeIndex, annotation]) => ({
                            episode_index: episodeIndex,
                            ...annotation,
                        })
                    ),
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
            elements.publish.disabled = !state.taskPending.size && !state.annotationPending.size;
        }
    });

    loadDatasets();
})();
