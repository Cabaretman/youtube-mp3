const form = document.querySelector('#convert-form');
const jobsElement = document.querySelector('#jobs');
const formError = document.querySelector('#form-error');
const queueError = document.querySelector('#queue-error');
const connection = document.querySelector('#connection');
const active = new Set(['queued', 'fetching', 'downloading', 'converting']);
const labels = {queued: 'In queue', fetching: 'Getting video details', downloading: 'Downloading audio', converting: 'Encoding MP3', ready: 'Ready to download', failed: 'Couldn’t convert', cancelled: 'Cancelled', expired: 'File expired'};
let jobs = [];

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check your link and quality, then try again.');
  return data;
}

function showError(element, message) {
  element.textContent = message;
  element.hidden = !message;
}

function button(text, handler) {
  const element = document.createElement('button');
  element.type = 'button';
  element.textContent = text;
  element.className = 'secondary';
  element.addEventListener('click', async () => {
    element.disabled = true;
    try { await handler(); await refresh(); }
    catch (error) { showError(queueError, error.message); }
    finally { element.disabled = false; }
  });
  return element;
}

function render() {
  document.querySelector('#count').textContent = jobs.length;
  if (!jobs.length) return;
  const focusedJob = document.activeElement.closest?.('[data-id]')?.dataset.id;
  const fragment = document.createDocumentFragment();
  for (const job of jobs) {
    const card = document.querySelector('#job-template').content.firstElementChild.cloneNode(true);
    card.dataset.id = job.id;
    const percent = typeof job.progress === 'number' ? Math.round(job.progress) : null;
    card.querySelector('.job-status').textContent = labels[job.state] + (active.has(job.state) && percent !== null ? ` · ${percent}%` : '');
    card.querySelector('h3').textContent = job.title || 'YouTube audio';
    const metadata = [`${job.bitrate} kbps`, 'MP3'];
    if (job.duration) {
      const minutes = Math.floor(job.duration / 60);
      metadata.push(minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m ${Math.floor(job.duration % 60)}s`);
    }
    if (job.size) metadata.push(`${(job.size / 1024 / 1024).toFixed(1)} MB`);
    card.querySelector('.job-meta').textContent = metadata.join(' · ');
    const progress = card.querySelector('progress');
    progress.hidden = !active.has(job.state);
    if (percent !== null) progress.value = percent;
    const actions = card.querySelector('.job-actions');
    if (job.state === 'ready') {
      const link = document.createElement('a');
      link.className = 'download';
      link.textContent = 'Download';
      link.href = `/api/jobs/${job.id}/download`;
      actions.append(link);
    } else if (active.has(job.state)) {
      actions.append(button('Cancel', () => api(`/api/jobs/${job.id}/cancel`, {method: 'POST'})));
    } else {
      actions.append(button('Try again', () => submit(job.url, job.bitrate)));
    }
    const error = card.querySelector('.job-error');
    showError(error, job.error || '');
    fragment.append(card);
  }
  jobsElement.replaceChildren(fragment);
  if (focusedJob) jobsElement.querySelector(`[data-id="${focusedJob}"] .job-actions > *`)?.focus();
}

async function submit(url, bitrate) {
  await api('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url, bitrate: Number(bitrate)})});
}

async function refresh() {
  try {
    const latest = await api('/api/jobs');
    connection.textContent = 'Running locally';
    connection.classList.remove('offline');
    showError(queueError, '');
    if (JSON.stringify(latest) !== JSON.stringify(jobs)) { jobs = latest; render(); }
  } catch {
    connection.textContent = 'App offline';
    connection.classList.add('offline');
    showError(queueError, 'Connection lost. Keep the local app running. Reconnecting automatically…');
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  const submitButton = document.querySelector('#submit');
  submitButton.disabled = true;
  showError(formError, '');
  try {
    await submit(document.querySelector('#url').value, document.querySelector('#bitrate').value);
    document.querySelector('#url').value = '';
    await refresh();
  } catch (error) { showError(formError, error.message); }
  finally { submitButton.disabled = false; }
});

async function poll() {
  await refresh();
  setTimeout(poll, document.hidden ? 5000 : 1500);
}
poll();
