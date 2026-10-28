(() => {
  const feedback = document.getElementById('dm-feedback');
  const show = (message, error = false) => {
    if (!feedback) return;
    feedback.textContent = message;
    feedback.classList.toggle('dm-feedback-error', error);
    feedback.hidden = false;
  };
  const request = async (url, options) => {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
    return data;
  };
  const json = body => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  const registerPage = document.getElementById('dm-register-page');
  if (registerPage && document.getElementById('dm-register')) {
    const checks = [...document.querySelectorAll('.dm-folder-check:not(:disabled)')];
    const all = document.getElementById('dm-select-all');
    const button = document.getElementById('dm-register');
    const count = document.getElementById('dm-selection-count');
    const update = () => {
      const selected = checks.filter(box => box.isConnected && box.checked).length;
      count.textContent = `已选 ${selected} 项`;
      button.disabled = selected === 0;
      const available = checks.filter(box => box.isConnected).length;
      all.checked = available > 0 && selected === available;
      all.indeterminate = selected > 0 && selected < available;
    };
    checks.forEach(box => box.addEventListener('change', update));
    all.addEventListener('change', () => { checks.forEach(box => { box.checked = all.checked; }); update(); });
    button.addEventListener('click', async () => {
      const folders = checks.filter(box => box.isConnected && box.checked).map(box => box.value);
      if (!folders.length) return;
      const names = Object.fromEntries(checks.filter(box => box.isConnected && box.checked).map(box => [
        box.value, box.closest('tr').querySelector('.dm-register-name').value.trim()
      ]));
      button.disabled = true;
      show(`正在登记 ${folders.length} 个目录…`);
      try {
        const result = await request('/data/api/register', { method: 'POST', ...json({ folders, names }) });
        const ok = result.registered.map(row => `${row.folder} → ${row.id}`).join('；');
        const errors = result.errors.map(row => `${row.folder}：${row.message}`).join('；');
        if (errors) show(`已登记 ${result.registered.length} 项。${ok}。未登记：${errors}`, true);
        else show(`登记完成：${ok}`);
        result.registered.forEach(row => {
          const box = checks.find(item => item.value === row.folder);
          if (box) box.closest('tr').remove();
        });
        if (result.registered.length && !errors) setTimeout(() => location.reload(), 1200);
      } catch (error) { show(error.message, true); }
      finally { update(); }
    });
  }

  const detail = document.getElementById('dm-detail-page');
  if (detail) {
    const id = encodeURIComponent(detail.dataset.id);
    const form = document.getElementById('dm-display-form');
    if (form) form.addEventListener('submit', async event => {
      event.preventDefault();
      const values = new FormData(form);
      const button = form.querySelector('button');
      button.disabled = true;
      try {
        await request(`/data/api/datasets/${id}`, { method: 'PATCH', ...json({
          name: values.get('name'), description: values.get('description'),
          tags: String(values.get('tags') || '').split(/[,，]/).map(tag => tag.trim()).filter(Boolean)
        }) });
        location.reload();
      } catch (error) { show(error.message, true); button.disabled = false; }
    });
    const scan = document.getElementById('dm-rescan');
    if (scan) scan.addEventListener('click', async () => {
      scan.disabled = true;
      try { await request(`/data/api/datasets/${id}/rescan`, { method: 'POST' }); location.reload(); }
      catch (error) { show(error.message, true); scan.disabled = false; }
    });
  }
  document.querySelectorAll('.dm-video-select').forEach(select => select.addEventListener('change', () => {
    const video = select.closest('.dm-camera').querySelector('video');
    video.pause(); video.src = select.value; video.load();
  }));

  const chartRoot = document.getElementById('dm-joint-chart');
  const chartCanvas = document.getElementById('dm-joint-canvas');
  if (chartRoot && chartCanvas) {
    const episode = document.getElementById('dm-joint-episode');
    const field = document.getElementById('dm-joint-field');
    const load = document.getElementById('dm-joint-load');
    const status = document.getElementById('dm-joint-feedback');
    const pointCount = document.getElementById('dm-joint-point-count');
    const legend = document.getElementById('dm-joint-legend');
    const palette = ['#1677b8', '#e56b3e', '#2b9b68', '#8b5fc4', '#d29a22', '#d34f79', '#208b92', '#6757bd', '#72863b', '#b65c2b', '#4f7692', '#a2467d'];
    let payload = null;
    let enabled = [];

    const render = () => {
      const bounds = chartCanvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(320, Math.floor(bounds.width));
      const height = Math.max(260, Math.floor(bounds.height));
      chartCanvas.width = Math.floor(width * ratio);
      chartCanvas.height = Math.floor(height * ratio);
      const context = chartCanvas.getContext('2d');
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      if (!payload || !payload.points.length) return;

      const active = enabled.map((visible, index) => visible ? index : -1).filter(index => index >= 0);
      const values = payload.points.flatMap(point => active.map(index => point.values[index])).filter(Number.isFinite);
      const margin = { left: 66, right: 22, top: 18, bottom: 48 };
      const innerWidth = width - margin.left - margin.right;
      const innerHeight = height - margin.top - margin.bottom;
      const maxTime = Math.max(...payload.points.map(point => point.seconds), 1e-9);
      let minValue = values.length ? Math.min(...values) : -1;
      let maxValue = values.length ? Math.max(...values) : 1;
      if (minValue === maxValue) { minValue -= 1; maxValue += 1; }
      const padding = (maxValue - minValue) * 0.06;
      minValue -= padding; maxValue += padding;
      const x = seconds => margin.left + seconds / maxTime * innerWidth;
      const y = value => margin.top + (maxValue - value) / (maxValue - minValue) * innerHeight;

      context.font = '11px sans-serif';
      context.lineWidth = 1;
      for (let tick = 0; tick <= 5; tick += 1) {
        const py = margin.top + tick / 5 * innerHeight;
        const value = maxValue - tick / 5 * (maxValue - minValue);
        context.strokeStyle = '#e9eff3';
        context.beginPath(); context.moveTo(margin.left, py); context.lineTo(width - margin.right, py); context.stroke();
        context.fillStyle = '#7890a2'; context.textAlign = 'right'; context.textBaseline = 'middle';
        context.fillText(value.toPrecision(4), margin.left - 9, py);
      }
      for (let tick = 0; tick <= 5; tick += 1) {
        const px = margin.left + tick / 5 * innerWidth;
        const value = tick / 5 * maxTime;
        context.strokeStyle = '#f0f4f6';
        context.beginPath(); context.moveTo(px, margin.top); context.lineTo(px, height - margin.bottom); context.stroke();
        context.fillStyle = '#7890a2'; context.textAlign = 'center'; context.textBaseline = 'top';
        context.fillText(`${value.toFixed(maxTime < 10 ? 2 : 1)} s`, px, height - margin.bottom + 10);
      }
      context.fillStyle = '#60798d'; context.textAlign = 'center'; context.fillText('Episode 时间（秒）', margin.left + innerWidth / 2, height - 8);
      context.save(); context.translate(15, margin.top + innerHeight / 2); context.rotate(-Math.PI / 2); context.fillText('关节原始值', 0, 0); context.restore();

      active.forEach(index => {
        context.strokeStyle = palette[index % palette.length];
        context.lineWidth = 1.6;
        context.beginPath();
        let drawing = false;
        payload.points.forEach(point => {
          const value = point.values[index];
          if (!Number.isFinite(value)) { drawing = false; return; }
          if (drawing) context.lineTo(x(point.seconds), y(value));
          else { context.moveTo(x(point.seconds), y(value)); drawing = true; }
        });
        context.stroke();
      });
    };

    const rebuildLegend = () => {
      legend.replaceChildren();
      payload.field.names.forEach((name, index) => {
        const label = document.createElement('label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox'; checkbox.checked = true;
        checkbox.addEventListener('change', () => { enabled[index] = checkbox.checked; render(); });
        const swatch = document.createElement('span');
        swatch.className = 'dm-joint-swatch'; swatch.style.background = palette[index % palette.length];
        label.append(checkbox, swatch, document.createTextNode(name));
        legend.append(label);
      });
    };

    const loadSeries = async () => {
      load.disabled = true;
      status.classList.remove('is-error');
      status.textContent = '正在读取 Episode 关节数据…';
      try {
        const id = encodeURIComponent(chartRoot.dataset.datasetId);
        const url = `/data/api/datasets/${id}/joint-series?episode_index=${encodeURIComponent(episode.value)}&field_name=${encodeURIComponent(field.value)}`;
        payload = await request(url, { method: 'GET' });
        enabled = payload.field.names.map(() => true);
        rebuildLegend(); render();
        const source = payload.time_source === 'timestamp' ? 'timestamp' : 'frame_index ÷ fps';
        status.textContent = `已读取 ${payload.total_points} 帧；时间来源：${source}${payload.sampled ? '；图表已均匀抽样显示' : ''}。`;
        pointCount.textContent = `${payload.field.width} 条曲线 · ${payload.points.length} 个显示点`;
      } catch (error) {
        payload = null; legend.replaceChildren(); render();
        status.textContent = error.message; status.classList.add('is-error'); pointCount.textContent = '';
      } finally { load.disabled = false; }
    };
    load.addEventListener('click', loadSeries);
    episode.addEventListener('change', loadSeries);
    field.addEventListener('change', loadSeries);
    window.addEventListener('resize', render);
    loadSeries();
  }
})();
