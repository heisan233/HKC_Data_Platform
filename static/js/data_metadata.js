(() => {
  const browser = document.getElementById('dm-metadata-browser');
  if (!browser) return;
  const id = encodeURIComponent(browser.dataset.id);
  const base = `/data/raw/${id}`;
  const panels = Object.fromEntries([...browser.querySelectorAll('[data-meta-panel]')]
    .map(node => [node.dataset.metaPanel, node]));
  const qcRows = JSON.parse(document.getElementById('dm-episode-qc').textContent || '[]');
  const qcStatus = new Map(qcRows.map(row => [Number(row.episode_index), row.status]));
  let summary = null;
  let currentPage = null;
  let offset = 0;
  const pageSize = 20;

  const node = (tag, value = '', className = '') => {
    const element = document.createElement(tag);
    element.textContent = value == null ? '—' : String(value);
    if (className) element.className = className;
    return element;
  };
  const clear = element => element.replaceChildren();
  const format = value => {
    if (value == null) return '—';
    if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3);
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (Array.isArray(value)) return value.length > 8 ? `${value.slice(0, 8).join(', ')} …` : value.join(', ');
    return String(value);
  };
  const fetchJSON = async path => {
    const response = await fetch(base + path);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.detail || `读取失败（${response.status}）`);
    return data;
  };
  const table = (headers, rows) => {
    const wrap = node('div', '', 'dm-table-scroll');
    const element = node('table', '', 'dm-table');
    const head = node('thead');
    const header = node('tr');
    headers.forEach(label => header.append(node('th', label)));
    head.append(header);
    const body = node('tbody');
    rows.forEach(cells => {
      const row = node('tr');
      cells.forEach(cell => {
        const td = node('td');
        td.append(cell instanceof Node ? cell : node('span', cell));
        row.append(td);
      });
      body.append(row);
    });
    element.append(head, body);
    wrap.append(element);
    return wrap;
  };

  browser.querySelectorAll('[data-meta-tab]').forEach(button => button.addEventListener('click', () => {
    browser.querySelectorAll('[data-meta-tab]').forEach(item => item.classList.toggle('active', item === button));
    Object.entries(panels).forEach(([key, panel]) => { panel.hidden = key !== button.dataset.metaTab; });
    if (button.dataset.metaTab === 'episodes' && summary && !currentPage) loadEpisodes();
  }));

  const renderFields = () => {
    const target = panels.fields;
    clear(target);
    if (summary.warnings.length) {
      const warning = node('p', summary.warnings.join('；'), 'dm-meta-warning');
      target.append(warning);
    }
    for (const group of ['状态与动作', '相机', '其他字段']) {
      const fields = summary.features.filter(field => field.group === group);
      if (!fields.length) continue;
      target.append(node('h4', `${group} · ${fields.length} 个字段`, 'dm-meta-heading'));
      const list = node('div', '', 'dm-feature-grid');
      fields.forEach(field => {
        const card = node('details', '', 'dm-feature');
        if (field.name === 'action' || field.name === 'observation.state') card.open = true;
        const title = node('summary');
        title.append(node('strong', field.name), node('span', `${field.dtype || '—'} · ${JSON.stringify(field.shape)}`));
        card.append(title);
        if (field.dimensions.length) {
          const rows = field.dimensions.map(axis => [axis.index, axis.name, '未声明']);
          card.append(table(['序号', '维度名称', '单位 / 控制语义'], rows));
        } else {
          card.append(node('p', field.dtype === 'video' ? '相机视图；分辨率与帧率见上方视频预览。' :
            '此字段没有逐维名称，按元数据声明的形状展示。', 'dm-subtle'));
        }
        if (field.stats) card.append(node('p', '统计文件提供该字段的整体汇总；逐维统计需从帧数据另行计算。', 'dm-subtle'));
        list.append(card);
      });
      target.append(list);
    }
  };

  const renderTasks = () => {
    const catalog = document.getElementById('dm-task-catalog');
    const select = document.getElementById('dm-task-filter');
    clear(catalog);
    summary.tasks.forEach(task => {
      const chip = node('span', `${task.name} · ${task.episodes} Episodes`, 'dm-task-chip');
      catalog.append(chip);
      const option = node('option', task.name);
      option.value = task.name;
      select.append(option);
    });
    if (!summary.tasks.length) catalog.append(node('p', '暂无任务名称；Episode 列表仍可查看。', 'dm-subtle'));
    select.addEventListener('change', () => { offset = 0; loadEpisodes(); });
    document.getElementById('dm-prev-page').addEventListener('click', () => {
      offset = Math.max(0, offset - pageSize); loadEpisodes();
    });
    document.getElementById('dm-next-page').addEventListener('click', () => {
      offset += pageSize; loadEpisodes();
    });
  };

  const loadEpisodes = async () => {
    const target = document.getElementById('dm-episode-table');
    target.replaceChildren(node('p', '正在读取 Episode…', 'dm-subtle'));
    document.getElementById('dm-episode-detail').hidden = true;
    const params = new URLSearchParams({ offset: String(offset), limit: String(pageSize) });
    const task = document.getElementById('dm-task-filter').value;
    if (task) params.set('task', task);
    try {
      currentPage = await fetchJSON(`/episodes?${params}`);
      clear(target);
      const rows = currentPage.items.map(episode => {
        const button = node('button', '查看 Episode →', 'dm-text-button');
        button.type = 'button';
        button.addEventListener('click', () => renderEpisode(episode));
        const status = qcStatus.get(Number(episode.episode_index));
        return [episode.episode_index, episode.tasks.join('、') || '未声明', episode.num_frames,
          episode.duration_seconds == null ? '—' : `${episode.duration_seconds} 秒`,
          `${format(episode.dataset_from_index)}–${format(episode.dataset_to_index)}`,
          status || '暂无逐条质检结果', button];
      });
      target.append(rows.length ? table(['Episode', 'Task', '帧数', '时长', '全局帧范围', '质检', '操作'], rows) :
        node('p', '没有匹配的 Episode。', 'dm-empty'));
      document.getElementById('dm-episode-count').textContent = `${currentPage.total} 条 Episode`;
      document.getElementById('dm-page-label').textContent = currentPage.total ?
        `${offset + 1}–${offset + currentPage.items.length} / ${currentPage.total}` : '0 / 0';
      document.getElementById('dm-prev-page').disabled = offset === 0;
      document.getElementById('dm-next-page').disabled = offset + pageSize >= currentPage.total;
    } catch (error) {
      currentPage = null;
      target.replaceChildren(node('p', error.message, 'dm-meta-warning'));
    }
  };

  const videoURL = path => `/data/raw/${id}/video/${path.split('/').map(encodeURIComponent).join('/')}`;
  const renderEpisode = episode => {
    const target = document.getElementById('dm-episode-detail');
    clear(target);
    target.hidden = false;
    target.append(node('h4', `Episode ${episode.episode_index} · ${episode.tasks.join('、') || '任务未声明'}`));
    target.append(node('p', `${episode.num_frames} 帧 · ${format(episode.duration_seconds)} 秒 · 全局索引 ${format(episode.dataset_from_index)}–${format(episode.dataset_to_index)}`, 'dm-subtle'));
    const videos = node('div', '', 'dm-episode-videos');
    episode.videos.forEach(segment => {
      const card = node('div', '', 'dm-episode-video');
      card.append(node('strong', segment.key));
      if (!segment.path) {
        card.append(node('p', '对应视频文件缺失或路径无效', 'dm-meta-warning'));
      } else {
        const video = node('video');
        video.controls = true;
        video.preload = 'metadata';
        video.src = videoURL(segment.path);
        const start = Number(segment.from_timestamp);
        const end = Number(segment.to_timestamp);
        if (Number.isFinite(start) && start > 0) video.addEventListener('loadedmetadata', () => { video.currentTime = start; });
        if (Number.isFinite(end) && end > start) video.addEventListener('timeupdate', () => {
          if (video.currentTime > end) video.pause();
        });
        card.append(video, node('small', `文件内时间：${format(segment.from_timestamp)}–${format(segment.to_timestamp)} 秒`));
      }
      videos.append(card);
    });
    target.append(videos);
    const form = node('form', '', 'dm-frame-form');
    const label = node('label', '查看帧序号 ');
    const input = node('input');
    input.type = 'number'; input.min = '0'; input.max = String(Math.max(0, episode.num_frames - 1));
    input.value = '0'; input.required = true;
    const button = node('button', '读取帧样本', 'dm-button');
    button.type = 'submit';
    label.append(input); form.append(label, button);
    const sample = node('div', '', 'dm-frame-sample');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      sample.replaceChildren(node('p', '正在读取帧样本…', 'dm-subtle'));
      try {
        const data = await fetchJSON(`/episodes/${episode.episode_index}/sample?frame_index=${encodeURIComponent(input.value)}`);
        renderSample(sample, data);
      } catch (error) { sample.replaceChildren(node('p', error.message, 'dm-meta-warning')); }
    });
    target.append(form, sample);
    target.scrollIntoView({ block: 'nearest' });
  };

  const renderSample = (target, data) => {
    clear(target);
    target.append(node('h5', `帧 ${data.frame_index} · timestamp ${format(data.timestamp)} 秒`));
    target.append(node('p', `帧级原值：task_index ${format(data.task_index)} · reward ${format(data.next_reward)} · done ${format(data.next_done)} · success ${format(data.next_success)}`, 'dm-subtle'));
    target.append(node('p', '这些是帧级字段，不直接代表整条 Episode 的任务成功判定。', 'dm-subtle'));
    data.vectors.forEach(vector => {
      target.append(node('h5', vector.name));
      target.append(table(['序号', '维度名称', '该帧数值'], vector.values.map(axis => [axis.index, axis.name, format(axis.value)])));
    });
  };

  const renderStats = () => {
    const target = panels.stats;
    clear(target);
    target.append(node('p', '下表是 meta/stats.json 保存的字段整体汇总；如果统计值为标量，它不是逐维统计。', 'dm-subtle'));
    const rows = Object.entries(summary.stats).map(([key, values]) => [key,
      ...['count', 'mean', 'std', 'min', 'max'].map(metric => format(values?.[metric]))]);
    target.append(rows.length ? table(['字段', '计数', '均值', '标准差', '最小', '最大'], rows) :
      node('p', '暂无统计文件。', 'dm-empty'));
    const counts = new Map();
    qcRows.forEach(row => counts.set(row.status, (counts.get(row.status) || 0) + 1));
    target.append(node('p', counts.size ?
      `最近报告中的逐 Episode 质检：${[...counts].map(([status, count]) => `${status} ${count}`).join(' · ')}` :
      '暂无逐 Episode 质检结果。', 'dm-subtle'));
    target.append(node('p', '质检结果与任务成功标志分别展示，不能相互替代。', 'dm-subtle'));
  };

  const renderSource = () => {
    const target = panels.source;
    clear(target);
    [['meta/info.json', summary.info], ['meta/stats.json', summary.stats]].forEach(([name, value]) => {
      const details = node('details', '', 'dm-source-details');
      details.append(node('summary', name), node('pre', JSON.stringify(value, null, 2), 'dm-pre'));
      target.append(details);
    });
  };

  fetchJSON('/metadata').then(data => {
    summary = data;
    renderFields(); renderTasks(); renderStats(); renderSource();
  }).catch(error => {
    panels.fields.replaceChildren(node('p', error.message, 'dm-meta-warning'));
    Object.entries(panels).filter(([name]) => name !== 'fields').forEach(([, panel]) => {
      panel.replaceChildren(node('p', '元数据暂时无法读取，其他数据集详情仍可使用。', 'dm-meta-warning'));
    });
  });
})();
