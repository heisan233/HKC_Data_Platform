(() => {
  const form = document.getElementById('conversion-form');
  if (!form) return;
  const source = document.getElementById('conversion-source');
  const feedback = document.getElementById('conversion-feedback');
  const rows = document.getElementById('conversion-tasks');
  const empty = document.getElementById('conversion-empty');
  const button = document.getElementById('conversion-submit');
  const count = document.getElementById('conversion-count');
  const exportSource = document.getElementById('export-source');
  const exportButton = document.getElementById('export-download');
  const exportEmpty = document.getElementById('export-empty');
  let submitting = false;

  const request = async (url, options) => {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
    return data;
  };
  const cell = (row, value, small) => {
    const td = row.insertCell();
    if (small === undefined) td.textContent = value;
    else {
      const strong = document.createElement('strong'); strong.textContent = value; td.append(strong);
      const note = document.createElement('small'); note.textContent = small; td.append(note);
    }
    return td;
  };
  const link = (parent, href, label) => {
    const a = document.createElement('a'); a.href = href; a.textContent = label; parent.append(a);
  };
  const renderTasks = tasks => {
    count.textContent = `${tasks.length} 个任务`;
    rows.replaceChildren();
    if (!tasks.length) {
      const td = cell(rows.insertRow(), '暂无转换任务。创建任务后可在这里查看进度和结果。');
      td.colSpan = 5; td.className = 'conversion-no-tasks';
      return;
    }
    for (const task of tasks) {
      const row = rows.insertRow();
      cell(row, task.id, 'LeRobot v3.0 → HDF5');
      cell(row, task.source_dataset_id, task.source_name);
      const progress = cell(row, `${task.completed_episodes}/${task.total_episodes} Episodes`);
      const track = document.createElement('div'); track.className = 'conversion-progress';
      const fill = document.createElement('span');
      fill.style.width = `${task.total_episodes ? Math.min(100, 100 * task.completed_episodes / task.total_episodes) : 0}%`;
      track.append(fill); progress.append(track);
      const status = cell(row, '');
      const badge = document.createElement('span');
      badge.className = `conversion-status conversion-status-${task.status.toLowerCase()}`;
      badge.textContent = ({ QUEUED: '排队中', RUNNING: '转换中', COMPLETED: '已完成', FAILED: '失败' })[task.status] || task.status;
      status.append(badge);
      const result = cell(row, '');
      if (task.status === 'COMPLETED') {
        const label = document.createElement('strong');
        label.textContent = `${task.id} · HDF5`;
        result.append(label);
        const download = document.createElement('span');
        download.className = 'conversion-row-export';
        link(download, `/export/archive/${encodeURIComponent(task.id)}`, '导出数据集 ↓');
        result.append(download);
      } else if (task.error) {
        result.textContent = task.error; result.className = 'conversion-error';
      } else result.textContent = '—';
    }
  };
  const refresh = async () => {
    const [sources, tasks, exportables] = await Promise.all([
      request('/export/api/datasets'), request('/export/api/conversions'),
      request('/export/api/exportable-datasets')
    ]);
    const selected = source.value;
    source.replaceChildren();
    for (const item of sources) {
      const option = document.createElement('option');
      option.value = item.dataset_id;
      option.textContent = `${item.dataset_id} · ${item.name} · ${item.total_episodes} Episodes`;
      source.append(option);
    }
    if (sources.some(item => item.dataset_id === selected)) source.value = selected;
    source.disabled = !sources.length;
    button.disabled = !sources.length || submitting;
    empty.hidden = !!sources.length;
    const selectedExport = exportSource.value;
    exportSource.replaceChildren();
    for (const item of exportables) {
      const option = document.createElement('option');
      option.value = item.dataset_id;
      option.textContent = `${item.dataset_id} · ${item.name}（${item.kind_label}）`;
      exportSource.append(option);
    }
    if (exportables.some(item => item.dataset_id === selectedExport)) exportSource.value = selectedExport;
    exportSource.disabled = !exportables.length;
    exportButton.disabled = !exportables.length;
    exportEmpty.hidden = !!exportables.length;
    renderTasks(tasks);
  };
  exportButton.addEventListener('click', () => {
    if (!exportSource.value) return;
    window.location.href = `/export/archive/${encodeURIComponent(exportSource.value)}`;
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const profile = form.querySelector('input[name=profile_id]:checked');
    if (!profile || !source.value) return;
    submitting = true; button.disabled = true;
    try {
      const task = await request('/export/api/conversions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: profile.value, source_dataset_id: source.value })
      });
      feedback.textContent = `转换任务 ${task.id} 已创建，进度会在下方更新。`;
      feedback.classList.remove('error'); feedback.hidden = false;
      await refresh();
    } catch (error) {
      feedback.textContent = error.message; feedback.classList.add('error'); feedback.hidden = false;
    } finally {
      submitting = false; button.disabled = source.disabled;
    }
  });
  setInterval(() => refresh().catch(error => {
    feedback.textContent = error.message; feedback.classList.add('error'); feedback.hidden = false;
  }), 3000);
})();
