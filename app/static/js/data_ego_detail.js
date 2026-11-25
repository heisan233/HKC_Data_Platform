(() => {
    const root = document.getElementById("dm-detail-page");
    const clipSelect = document.getElementById("ego-clip");
    if (!root || !clipSelect) return;
    const id = root.dataset.id;
    const taskSelect = document.getElementById("ego-task");
    const video = document.getElementById("ego-video");
    video.addEventListener("contextmenu", (event) => event.preventDefault());
    const pose = document.getElementById("ego-pose");
    const count = document.getElementById("ego-count");
    const pageLabel = document.getElementById("ego-page");
    const videoStatus = document.getElementById("ego-video-status");
    const previous = document.getElementById("ego-prev");
    const next = document.getElementById("ego-next");
    let page = 1;
    let pages = 1;
    let clips = [];
    let selection = 0;

    const selectClip = async () => {
        const currentSelection = ++selection;
        const clip = clips[Number(clipSelect.value)];
        video.onerror = null;
        video.onloadedmetadata = null;
        video.onloadeddata = null;
        video.removeAttribute("src");
        video.load();
        pose.replaceChildren();
        if (!clip) {
            videoStatus.textContent = "当前筛选下没有片段。";
            return;
        }
        const videoPath = clip.video.split("/").map(encodeURIComponent).join("/");
        const posePath = clip.pose.split("/").map(encodeURIComponent).join("/");
        const previewUrl = `/data/ego/${encodeURIComponent(id)}/preview/${videoPath}`;
        videoStatus.classList.remove("is-error");
        videoStatus.textContent = "正在准备浏览器兼容预览，首次加载可能需要片刻…";
        video.onloadedmetadata = () => {
            if (currentSelection !== selection) return;
            videoStatus.textContent = "兼容预览已就绪（前 2 分钟）。";
        };
        video.onloadeddata = () => {
            if (currentSelection !== selection || !Number.isFinite(video.duration)) return;
            // Show an actual frame before the user presses play.
            video.currentTime = Math.min(0.1, video.duration / 2);
        };
        video.onerror = () => {
            if (currentSelection !== selection) return;
            videoStatus.classList.add("is-error");
            videoStatus.textContent = "兼容预览加载失败，请稍后重试。";
        };
        video.src = previewUrl;
        video.load();
        pose.textContent = "正在读取 HDF5 摘要…";
        try {
            const response = await fetch(`/data/ego/${encodeURIComponent(id)}/pose/${posePath}`);
            const data = await response.json();
            if (currentSelection !== selection) return;
            if (!response.ok) throw new Error(data.detail || "无法读取 HDF5");
            pose.replaceChildren();
            for (const [key, value] of Object.entries(data.attributes || {})) {
                const row = document.createElement("div");
                const label = document.createElement("span");
                const detail = document.createElement("strong");
                label.textContent = key;
                detail.textContent = value;
                row.append(label, detail);
                pose.append(row);
            }
            for (const field of data.datasets || []) {
                const row = document.createElement("div");
                const label = document.createElement("span");
                const detail = document.createElement("strong");
                label.textContent = field.name;
                detail.textContent = `${field.dtype} ${JSON.stringify(field.shape)}`;
                row.append(label, detail);
                pose.append(row);
            }
        } catch (error) {
            if (currentSelection === selection) pose.textContent = error.message;
        }
    };

    const loadPage = async () => {
        const query = new URLSearchParams({ page: String(page), task: taskSelect.value });
        let data;
        try {
            const response = await fetch(`/data/ego/${encodeURIComponent(id)}/clips?${query}`);
            if (!response.ok) throw new Error("片段列表无法读取");
            data = await response.json();
        } catch (error) {
            count.textContent = error.message;
            return;
        }
        clips = data.clips;
        pages = Math.max(1, Math.ceil(data.total / 25));
        count.textContent = `共 ${data.total} 个片段`;
        pageLabel.textContent = `${page} / ${pages}`;
        previous.disabled = page <= 1;
        next.disabled = page >= pages;
        clipSelect.replaceChildren();
        clips.forEach((clip, index) => {
            const option = document.createElement("option");
            option.value = String(index);
            option.textContent = `${clip.task} / ${clip.video.split("/").pop()}`;
            clipSelect.append(option);
        });
        selectClip();
    };
    taskSelect.addEventListener("change", () => { page = 1; loadPage(); });
    clipSelect.addEventListener("change", selectClip);
    previous.addEventListener("click", () => {
        if (page > 1) { page--; loadPage(); }
    });
    next.addEventListener("click", () => {
        if (page < pages) { page++; loadPage(); }
    });
    loadPage();
})();
