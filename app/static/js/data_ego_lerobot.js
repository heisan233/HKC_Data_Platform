(() => {
  const root = document.getElementById('dm-ego-browser');
  if (!root) return;
  const datasetId = encodeURIComponent(root.dataset.datasetId);
  const items = document.getElementById('ego-episode-items');
  const pageCount = document.getElementById('ego-page-count');
  const pageLabel = document.getElementById('ego-page-label');
  const previous = document.getElementById('ego-prev-page');
  const next = document.getElementById('ego-next-page');
  const title = document.getElementById('ego-selected-title');
  const range = document.getElementById('ego-video-range');
  const video = document.getElementById('ego-episode-video');
  const videoStatus = document.getElementById('ego-video-status');
  const field = document.getElementById('ego-vector-field');
  const equality = document.getElementById('ego-equality');
  const count = document.getElementById('ego-vector-count');
  const legend = document.getElementById('ego-vector-legend');
  const status = document.getElementById('ego-vector-status');
  const canvas = document.getElementById('ego-vector-canvas');
  const palette = ['#2376c0', '#db7048', '#2b9a73', '#8c63bd'];
  let page = 1;
  let current = null;
  let series = null;
  let dimensions = [0, 1, 2];
  let requestNumber = 0;

  const getJson = async (url) => {
    const response = await fetch(url);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '读取失败');
    return data;
  };
  const formatTime = seconds => Number.isFinite(seconds) ? `${seconds.toFixed(1)} s` : '—';

  const renderChart = () => {
    const width = Math.max(320, Math.floor(canvas.getBoundingClientRect().width));
    const height = 290;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(width * ratio);
    canvas.height = Math.floor(height * ratio);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    if (!series || !series.points.length || !dimensions.length) return;
    const points = series.points;
    const values = points.flatMap(point => dimensions.map(index => point.values[index])).filter(Number.isFinite);
    if (!values.length) return;
    const margin = {left: 56, right: 20, top: 18, bottom: 42};
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const maxSeconds = Math.max(points[points.length - 1].seconds, 0.001);
    let low = Math.min(...values), high = Math.max(...values);
    if (low === high) { low -= 0.5; high += 0.5; }
    const pad = (high - low) * 0.06;
    low -= pad; high += pad;
    const x = seconds => margin.left + seconds / maxSeconds * plotWidth;
    const y = value => margin.top + (high - value) / (high - low) * plotHeight;
    ctx.font = '11px sans-serif';
    for (let tick = 0; tick <= 4; tick++) {
      const py = margin.top + tick * plotHeight / 4;
      const value = high - tick * (high - low) / 4;
      ctx.strokeStyle = '#e8eef2';
      ctx.beginPath(); ctx.moveTo(margin.left, py); ctx.lineTo(width - margin.right, py); ctx.stroke();
      ctx.fillStyle = '#71899a'; ctx.textAlign = 'right';
      ctx.fillText(value.toPrecision(3), margin.left - 7, py + 3);
      const px = margin.left + tick * plotWidth / 4;
      ctx.fillText(`${(tick * maxSeconds / 4).toFixed(1)}s`, px + 10, height - 19);
    }
    dimensions.forEach((dimension, slot) => {
      ctx.strokeStyle = palette[slot]; ctx.lineWidth = 1.8;
      ctx.beginPath();
      let started = false;
      points.forEach(point => {
        const value = point.values[dimension];
        if (!Number.isFinite(value)) { started = false; return; }
        if (started) ctx.lineTo(x(point.seconds), y(value));
        else { ctx.moveTo(x(point.seconds), y(value)); started = true; }
      });
      ctx.stroke();
    });
    ctx.fillStyle = '#617b8e'; ctx.textAlign = 'center';
    ctx.fillText('Episode 时间（秒）', margin.left + plotWidth / 2, height - 4);
  };

  const buildLegend = () => {
    legend.replaceChildren();
    if (!series) return;
    for (let index = 0; index < series.width; index++) {
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox'; checkbox.checked = dimensions.includes(index);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked && dimensions.length >= 4) {
          checkbox.checked = false;
          status.textContent = '一次最多选择 4 个维度，便于看清曲线。';
          return;
        }
        dimensions = checkbox.checked ? [...dimensions, index] : dimensions.filter(value => value !== index);
        buildLegend(); renderChart();
      });
      const swatch = document.createElement('span');
      swatch.style.background = checkbox.checked ? palette[dimensions.indexOf(index)] : '#c7d5de';
      label.append(checkbox, swatch, document.createTextNode(`维度 ${index}`));
      legend.append(label);
    }
  };

  const loadSeries = async () => {
    if (!current) return;
    const serial = ++requestNumber;
    status.textContent = '正在读取所选 Episode 的状态与动作…';
    equality.textContent = '正在比较两列';
    try {
      const query = new URLSearchParams({episode_index: String(current.episode_index), field_name: field.value});
      const data = await getJson(`/data/api/ego/${datasetId}/vector-series?${query}`);
      if (serial !== requestNumber) return;
      series = data;
      dimensions = [0, 1, 2].filter(index => index < data.width);
      buildLegend(); renderChart();
      count.textContent = `${data.total_points} 帧 · ${data.width} 维`;
      equality.textContent = data.state_action_equal === null ? '缺少对比字段' :
        data.state_action_equal ? '本片段 state / action 数值相同' : '本片段 state / action 存在差异';
      status.textContent = `${data.sampled ? '曲线已抽样显示；' : ''}点击曲线可定位到片段视频的对应时间。`;
    } catch (error) {
      if (serial !== requestNumber) return;
      series = null; legend.replaceChildren(); renderChart();
      equality.textContent = '比较不可用'; status.textContent = error.message; count.textContent = '';
    }
  };

  const selectEpisode = episode => {
    current = episode;
    title.textContent = `Episode ${episode.episode_index}`;
    range.textContent = `${episode.frames ?? '—'} 帧 · ${formatTime(episode.duration_seconds)}`;
    for (const button of items.querySelectorAll('button')) {
      button.classList.toggle('is-selected', Number(button.dataset.episode) === episode.episode_index);
    }
    video.pause(); video.removeAttribute('src'); video.load();
    if (episode.video) {
      video.src = episode.video.url;
      videoStatus.textContent = `正在准备 ${episode.video.camera} 的片段预览…`;
      video.load();
    } else videoStatus.textContent = '此片段没有可预览的视频。';
    loadSeries();
  };

  const loadPage = async () => {
    items.textContent = '正在读取片段…';
    try {
      const data = await getJson(`/data/api/ego/${datasetId}/episodes?page=${page}`);
      items.replaceChildren();
      const pages = Math.max(1, Math.ceil(data.total / data.page_size));
      pageCount.textContent = `共 ${data.total} 个 Episode`;
      pageLabel.textContent = `${page} / ${pages}`;
      previous.disabled = page <= 1; next.disabled = page >= pages;
      for (const episode of data.items) {
        const button = document.createElement('button');
        button.type = 'button'; button.dataset.episode = String(episode.episode_index);
        button.className = 'dm-ego-episode-item';
        const main = document.createElement('span');
        const number = document.createElement('strong'); number.textContent = `Episode ${episode.episode_index}`;
        const task = document.createElement('small'); task.textContent = episode.task;
        main.append(number, task);
        const meta = document.createElement('span'); meta.textContent = `${episode.frames ?? '—'} 帧 · ${formatTime(episode.duration_seconds)}`;
        button.append(main, meta);
        button.addEventListener('click', () => selectEpisode(episode));
        items.append(button);
      }
      if (data.items.length) selectEpisode(data.items[0]);
      else items.textContent = '此页没有片段。';
    } catch (error) {
      items.textContent = error.message;
    }
  };
  video.addEventListener('contextmenu', event => event.preventDefault());
  video.addEventListener('loadeddata', () => { videoStatus.textContent = '片段预览已就绪。'; });
  video.addEventListener('error', () => { videoStatus.textContent = '视频预览失败，请稍后重试。'; });
  field.addEventListener('change', loadSeries);
  previous.addEventListener('click', () => { if (page > 1) { page--; loadPage(); } });
  next.addEventListener('click', () => { page++; loadPage(); });
  canvas.addEventListener('click', event => {
    if (!series || !current || !current.video || !Number.isFinite(video.duration)) return;
    const bounds = canvas.getBoundingClientRect();
    const relative = Math.max(0, Math.min(1, (event.clientX - bounds.left - 56) / (bounds.width - 76)));
    const end = series.points[series.points.length - 1].seconds;
    video.currentTime = Math.min(video.duration, relative * end);
  });
  window.addEventListener('resize', renderChart);
  loadPage();
})();
