document.querySelectorAll('.an-create').forEach(button => {
  button.addEventListener('click', async () => {
    const notice = document.getElementById('an-notice');
    button.disabled = true;
    button.textContent = '创建中…';
    try {
      const response = await fetch('/annotation/api/tasks', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({dataset_id: button.dataset.datasetId})
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || '创建任务失败');
      location.href = result.url;
    } catch (error) {
      notice.textContent = error.message;
      notice.classList.add('error');
      notice.hidden = false;
      button.disabled = false;
      button.textContent = '创建标注任务';
    }
  });
});
