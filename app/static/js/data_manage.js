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
})();
