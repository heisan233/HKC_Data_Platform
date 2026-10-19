const workspace = document.getElementById('an-workspace');
const taskId = workspace.dataset.taskId;
const datasetId = workspace.dataset.datasetId;
const writable = workspace.dataset.writable === 'true';
const phases = JSON.parse(document.getElementById('an-phase-data').textContent);
const statusNames = {DRAFT:'草稿',SUBMITTED:'待审核',APPROVED:'已通过',REWORK:'待返工'};
const statusClasses = {DRAFT:'an-active',SUBMITTED:'an-review',APPROVED:'an-pass',REWORK:'an-rework'};
let offset = 0;
let total = 0;
let currentIndex = null;
let currentEpisode = null;
let currentItem = null;
let itemsByIndex = {};
let dirty = false;
const pageSize = 20;

async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || '操作失败');
  return data;
}
function notify(message, error=false) {
  const box = document.getElementById('an-notice');
  box.textContent = message;
  box.hidden = false;
  box.classList.toggle('error', error);
}
function updateBadge(element, status) {
  element.className = `an-badge ${statusClasses[status] || 'an-active'}`;
  element.textContent = statusNames[status] || '待标注';
}
function addPhase(value={}) {
  const list = document.getElementById('an-phase-list');
  const row = document.createElement('div'); row.className = 'an-phase-row';
  const select = document.createElement('select'); select.setAttribute('aria-label','动作阶段');
  phases.forEach(phase => {
    const option = document.createElement('option'); option.value = phase.code;
    option.textContent = phase.name; select.append(option);
  });
  select.value = value.code || phases[0].code;
  const start = document.createElement('input'); start.type = 'number'; start.min = '0';
  start.placeholder = '起始帧'; start.setAttribute('aria-label','起始帧');
  start.value = value.start_frame ?? '';
  const end = document.createElement('input'); end.type = 'number'; end.min = '1';
  end.placeholder = '结束帧'; end.setAttribute('aria-label','结束帧，不包含');
  end.value = value.end_frame ?? '';
  const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '移除';
  remove.addEventListener('click', () => { row.remove(); dirty = true; });
  [select,start,end].forEach(input => input.addEventListener('input', () => { dirty = true; }));
  row.append(select,start,end,remove); list.append(row);
  row.querySelectorAll('input,select,button').forEach(control => { control.disabled = !writable || ['SUBMITTED','APPROVED'].includes(currentItem?.status); });
}
function collectDraft() {
  const segments = [...document.querySelectorAll('.an-phase-row')].map(row => {
    const [select,start,end] = row.querySelectorAll('select,input');
    if (start.value === '' || end.value === '') throw new Error('请填写动作阶段的起始帧和结束帧');
    return {code:select.value,start_frame:Number(start.value),end_frame:Number(end.value)};
  });
  return {instruction:document.getElementById('an-instruction').value,
    outcome:document.getElementById('an-outcome').value || null, phases:segments,
    notes:document.getElementById('an-notes').value};
}
function setVideo(segments) {
  const tabs = document.getElementById('an-camera-tabs'); tabs.replaceChildren();
  const video = document.getElementById('an-video');
  const empty = document.getElementById('an-video-empty');
  const available = segments.filter(segment => segment.path);
  video.pause(); video.removeAttribute('src'); video.load(); video.hidden = true;
  empty.hidden = false;
  empty.textContent = available.length ? '选择一个视角播放视频' : '此 Episode 暂无可预览视频';
  available.forEach((segment,index) => {
    const tab = document.createElement('button'); tab.type = 'button';
    tab.textContent = segment.key.replace('observation.images.','');
    tab.addEventListener('click', () => {
      tabs.querySelectorAll('button').forEach(button => button.classList.remove('active'));
      tab.classList.add('active');
      video.src = `/data/raw/${encodeURIComponent(datasetId)}/video/${segment.path.split('/').map(encodeURIComponent).join('/')}`;
      video.hidden = false; empty.hidden = true;
    });
    tabs.append(tab); if(index===0) tab.click();
  });
}
function displayItem(item, episode) {
  currentItem = item; currentEpisode = episode; dirty = false;
  document.getElementById('an-episode-title').textContent = `Episode ${episode.episode_index}`;
  const taskText = episode.tasks?.length ? ` · 原任务：${episode.tasks.join(' / ')}` : '';
  document.getElementById('an-episode-meta').textContent = `${episode.num_frames ?? '—'} 帧 · ${episode.duration_seconds ?? '—'} 秒${taskText}`;
  updateBadge(document.getElementById('an-item-status'), item.status);
  document.getElementById('an-instruction').value = item.instruction || episode.tasks?.[0] || '';
  document.getElementById('an-outcome').value = item.outcome || '';
  document.getElementById('an-notes').value = item.notes || '';
  document.getElementById('an-phase-list').replaceChildren();
  (item.phases || []).forEach(addPhase);
  const locked = !writable || ['SUBMITTED','APPROVED'].includes(item.status);
  document.querySelectorAll('#an-form textarea,#an-form select,#an-form input').forEach(control => { control.disabled = locked; });
  document.getElementById('an-add-phase').disabled = locked;
  document.getElementById('an-save').disabled = locked;
  document.getElementById('an-submit').disabled = locked;
  const reason = document.getElementById('an-review-reason');
  reason.hidden = !item.review_reason; reason.textContent = item.review_reason ? `驳回原因：${item.review_reason}` : '';
  document.getElementById('an-review-actions').hidden = !writable || item.status !== 'SUBMITTED';
  setVideo(episode.videos || []);
  document.querySelectorAll('.an-episode-row').forEach(row => row.classList.toggle('active', Number(row.dataset.index) === episode.episode_index));
}
async function openEpisode(index) {
  if (dirty && !confirm('当前 Episode 有未保存的修改，仍要切换吗？')) return;
  try {
    const data = await request(`/annotation/api/tasks/${taskId}/items/${index}`);
    currentIndex = index; displayItem(data.item,data.episode);
  } catch (error) { notify(error.message,true); }
}
async function refreshTask() {
  const data = await request(`/annotation/api/tasks/${taskId}`);
  itemsByIndex = Object.fromEntries(data.items.map(item => [item.episode_index,item]));
  document.getElementById('an-approved-count').textContent = `${data.task.counts.approved} / ${data.task.counts.total}`;
  return data;
}
async function loadPage(nextOffset=0) {
  try {
    const [data] = await Promise.all([
      request(`/data/raw/${encodeURIComponent(datasetId)}/episodes?offset=${nextOffset}&limit=${pageSize}`),
      refreshTask()
    ]);
    offset = nextOffset; total = data.total;
    const list = document.getElementById('an-episode-list'); list.replaceChildren();
    data.items.forEach(episode => {
      const row = document.createElement('button'); row.type = 'button'; row.className = 'an-episode-row';
      row.dataset.index = episode.episode_index;
      const info = document.createElement('span');
      const title = document.createElement('strong'); title.textContent = `Episode ${episode.episode_index}`;
      const detail = document.createElement('small'); detail.textContent = `${episode.num_frames ?? '—'} 帧 · ${episode.tasks?.[0] || '无任务描述'}`;
      info.append(title,detail);
      const badge = document.createElement('span'); updateBadge(badge,itemsByIndex[episode.episode_index]?.status || 'DRAFT');
      row.append(info,badge); row.addEventListener('click', () => openEpisode(episode.episode_index));
      list.append(row);
    });
    document.getElementById('an-page-label').textContent = total ? `${offset+1}–${Math.min(offset+pageSize,total)} / ${total}` : '无 Episode';
    document.getElementById('an-prev').disabled = offset === 0;
    document.getElementById('an-next').disabled = offset + pageSize >= total;
    const visible = data.items.some(episode => episode.episode_index === currentIndex);
    if (data.items.length && !visible) await openEpisode(data.items[0].episode_index);
    else document.querySelectorAll('.an-episode-row').forEach(row =>
      row.classList.toggle('active', Number(row.dataset.index) === currentIndex));
  } catch (error) { notify(error.message,true); }
}
async function saveDraft() {
  if (currentIndex === null) throw new Error('请先选择 Episode');
  const result = await request(`/annotation/api/tasks/${taskId}/items/${currentIndex}`,{
    method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(collectDraft())
  });
  currentItem = result; dirty = false; notify('草稿已保存');
  await refreshTask();
  const row = document.querySelector(`.an-episode-row[data-index="${currentIndex}"] .an-badge`);
  if(row) updateBadge(row,result.status);
  return result;
}
document.getElementById('an-prev').addEventListener('click', () => { if (!dirty || confirm('放弃未保存的修改？')) { dirty=false; loadPage(Math.max(0,offset-pageSize)); } });
document.getElementById('an-next').addEventListener('click', () => { if (!dirty || confirm('放弃未保存的修改？')) { dirty=false; loadPage(offset+pageSize); } });
document.getElementById('an-add-phase').addEventListener('click', () => { addPhase(); dirty = true; });
document.querySelectorAll('#an-form textarea,#an-form select').forEach(input => input.addEventListener('input', () => { dirty=true; }));
document.getElementById('an-save').addEventListener('click', async () => { try { await saveDraft(); } catch(error) { notify(error.message,true); } });
document.getElementById('an-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    await saveDraft();
    const item = await request(`/annotation/api/tasks/${taskId}/items/${currentIndex}/submit`,{method:'POST'});
    displayItem(item,currentEpisode); await loadPage(offset); notify('已提交审核');
  } catch(error) { notify(error.message,true); }
});
async function review(decision) {
  const reason = decision === 'REJECT' ? prompt('请填写驳回原因：') : '';
  if(reason === null) return;
  try {
    const item = await request(`/annotation/api/tasks/${taskId}/items/${currentIndex}/review`,{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision,reason})
    });
    displayItem(item,currentEpisode); await loadPage(offset);
    notify(decision === 'APPROVE' ? '审核通过' : '已驳回，等待返工');
  } catch(error) { notify(error.message,true); }
}
document.getElementById('an-approve').addEventListener('click', () => review('APPROVE'));
document.getElementById('an-reject').addEventListener('click', () => review('REJECT'));
loadPage();
