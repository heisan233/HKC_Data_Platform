(() => {
  const page = document.getElementById('dm-reports-page');
  if (!page) return;
  const feedback = document.getElementById('dm-report-feedback');
  page.addEventListener('click', async event => {
    const button = event.target.closest('.dm-delete-report, .dm-restore-report');
    if (!button || button.disabled) return;
    const filename = button.dataset.filename;
    const restoring = button.classList.contains('dm-restore-report');
    const message = restoring
      ? `确定恢复报告「${filename}」吗？`
      : `确定将报告「${filename}」移入回收站吗？质检任务和人工审核记录会保留。`;
    if (!window.confirm(message)) return;
    button.disabled = true;
    feedback.hidden = true;
    try {
      const base = `/data/reports/${encodeURIComponent(button.dataset.datasetId)}/${encodeURIComponent(filename)}`;
      const url = restoring
        ? `/data/reports/trash/${encodeURIComponent(button.dataset.datasetId)}/${encodeURIComponent(filename)}/restore`
        : base;
      const response = await fetch(url, { method: restoring ? 'POST' : 'DELETE' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `${restoring ? '恢复' : '移除'}失败（${response.status}）`);
      location.reload();
    } catch (error) {
      feedback.textContent = error.message;
      feedback.classList.add('dm-feedback-error');
      feedback.hidden = false;
      button.disabled = false;
    }
  });
})();
