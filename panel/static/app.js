'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="panel-token"]').content;
let state = null, online = false, lightWanted = false, toastTimer;
let page = 0, photos = [], total = 0, selected = 0, selectedAssay = null;
let assayRecords = [], lastTrash = null, archiveEpoch = 0, searchTimer;
const number = (v, d = 1) => v == null ? '—' : Number(v).toFixed(d);
const duration = seconds => {
  const s = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};
const localDate = value => value ? new Date(value).toLocaleString('en-US', {hour12: false}) : '—';
function notify(text) {
  $('message').textContent = text;
  $('message').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('message').hidden = true, 6500);
}
async function api(path, data) {
  const options = data === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Panel-Token': token},
    body: JSON.stringify(data)
  };
  const response = await fetch('/api/' + path, {...options, signal: AbortSignal.timeout(8000)});
  const result = await response.json().catch(() => ({error: 'Request failed. Refresh the page and try again.'}));
  if (!response.ok) throw Error(result.error || 'Request failed');
  return result;
}
async function act(path, data = {}, message) {
  try {
    const result = await api(path, data);
    if (message) notify(message);
    await status();
    return result;
  } catch (error) { notify(error.message); return null; }
}
function errorBox(id, text) { $(id).textContent = text || ''; $(id).hidden = !text; }
function badge(id, text, kind = '') { $(id).textContent = text; $(id).className = 'pill ' + kind; }
function gains() {
  const values = {};
  for (const key of ['kp', 'ki', 'kd']) {
    if (!$(key).reportValidity()) throw Error('Check the PID settings below.');
    values[key] = Number($(key).value);
  }
  return values;
}
async function status() {
  try {
    state = await api('status'); online = true;
    const h = state.heater, c = state.camera, a = state.assay, active = a.active;
    badge('connection', '● Connected', 'ok');
    $('disk').textContent = `Free storage: ${state.free_gb.toFixed(1)} GB`;
    $('temp').textContent = number(h.temperature);
    $('ambient').textContent = number(h.ambient) + ' °C';
    $('duty').textContent = number(h.duty, 0) + ' %';
    $('remaining').textContent = h.active ? duration(h.remaining_s) : '—';
    badge('heater-status', h.active ? 'Heating' : h.fault ? 'Fault · Output off' : h.ready ? 'Standby' : 'Sensor not ready', h.fault ? 'error' : h.ready ? 'ok' : '');
    badge('camera-status', c.ready ? (c.running ? 'Acquiring' : 'Camera ready') : 'Camera not ready', c.ready ? 'ok' : 'error');
    errorBox('heater-error', h.fault || (!h.enabled ? 'After checking the wiring, enable heating in the device configuration.' : null));
    errorBox('camera-error', c.error);
    $('start-heat').disabled = !h.ready || !h.enabled || !!h.fault || h.active || !!active;
    $('start-camera').disabled = !c.ready || c.running || c.busy || c.pending || !!active;
    $('capture').disabled = $('start-camera').disabled;
    $('light').disabled = !c.ready || !!active;
    $('stop-heat').disabled = $('stop-camera').disabled = !!active;
    $('pid-fields').disabled = h.active || !!active;
    $('reset').disabled = !!active;
    $('pid-used').hidden = !h.active;
    if (h.active) $('pid-used').textContent = `Running PID: Kp ${h.gains.kp} · Ki ${h.gains.ki} · Kd ${h.gains.kd}`;
    $('progress').max = c.count || 1;
    $('progress').value = c.captured;
    $('capture-progress').textContent = c.running ? `Acquiring · ${c.captured} / ${c.count} photos` : c.count ? `${c.captured >= c.count ? 'Complete' : 'Stopped'} · ${c.captured} / ${c.count} photos` : 'No acquisition started';
    $('light-note').textContent = c.preview_light ? 'Light on · Photobleaching risk' : 'Off · Auto-on during capture';
    if (!c.preview_light && !lightWanted) $('light').checked = false;
    $('assay-fields').disabled = !!active;
    $('start-assay').disabled = !!active || !h.ready || !h.enabled || !!h.fault || h.active || !c.ready || c.running || c.busy || c.pending;
    $('stop-assay').disabled = !active;
    const display = active || a.last;
    badge('assay-status', active ? 'Running · ' + duration(a.remaining_s) + ' left' : display ? display.status.charAt(0).toUpperCase() + display.status.slice(1) : 'Ready to configure', active ? 'ok' : display?.status === 'failed' ? 'error' : '');
    $('assay-run').hidden = !display;
    if (display) {
      $('assay-run-name').textContent = `${display.name} · ${display.operator}`;
      $('assay-run-detail').textContent = active ? `${c.captured} photos saved · ${display.target} °C target` : `${display.status} · ${display.captured ?? 0} photos captured`;
      $('assay-progress').value = active ? Math.min(1, 1 - a.remaining_s / (display.duration_minutes * 60)) : display.status === 'completed' ? 1 : 0;
    }
    errorBox('assay-error', a.error || (!active && display?.reason ? display.reason : null));
  } catch (error) {
    online = false;
    badge('connection', 'Disconnected', 'error');
    for (const id of ['start-heat', 'start-camera', 'capture', 'light', 'start-assay']) $(id).disabled = true;
    $('temp').textContent = '—'; $('remaining').textContent = 'Unknown';
    badge('heater-status', 'Status unknown', 'error');
    errorBox('heater-error', 'Connection lost. Active heating or assay jobs may still be running.');
  }
}
async function poll() { await status(); setTimeout(poll, 1500); }
async function preview() {
  if (!document.hidden && !$('live').hidden) {
    try {
      const response = await fetch('/preview.jpg?t=' + Date.now(), {signal: AbortSignal.timeout(5000)});
      if (!response.ok) throw Error();
      const old = $('preview').src;
      $('preview').src = URL.createObjectURL(await response.blob());
      $('preview').hidden = false; $('preview-empty').hidden = true;
      if (old.startsWith('blob:')) URL.revokeObjectURL(old);
    } catch { $('preview').hidden = true; $('preview-empty').hidden = false; }
  }
  setTimeout(preview, 400);
}
$('heat-form').onsubmit = event => {
  event.preventDefault();
  try { act('heater/start', {target: Number($('target').value), duration_minutes: Number($('duration').value), pid: gains()}, 'Timed heating started'); }
  catch (error) { notify(error.message); }
};
$('assay-form').onsubmit = async event => {
  event.preventDefault();
  try {
    const result = await act('assays/start', {name: $('assay-name').value.trim(), operator: $('assay-operator').value.trim(), duration_minutes: Number($('assay-duration').value), target: Number($('assay-target').value), interval: Number($('assay-interval').value), pid: gains()}, 'Assay started. Photos and logs will be saved together.');
    if (result) { lightWanted = false; $('light').checked = false; await refreshCatalog(); }
  } catch (error) { notify(error.message); }
};
$('capture-form').onsubmit = event => {
  event.preventDefault();
  act('camera/start', {interval: Number($('interval').value), count: Number($('count').value)}, 'Timed acquisition started');
};
$('stop-heat').onclick = () => act('heater/stop', {}, 'Heating stopped');
$('stop-camera').onclick = () => { lightWanted = false; $('light').checked = false; act('camera/stop', {}, 'Acquisition and excitation light stopped'); };
$('stop-assay').onclick = () => act('assays/stop', {}, 'Assay stopped. Saved data is retained.');
$('stop-all').onclick = () => { lightWanted = false; $('light').checked = false; act('stop', {}, 'All instruments stopped'); };
$('reset').onclick = () => act('heater/reset', {}, 'Heating fault cleared');
$('capture').onclick = () => act('camera/capture', {}, 'Capture queued. Saved under Unassigned.');
$('light').onchange = async () => {
  lightWanted = $('light').checked;
  try { await api('camera/light', {enabled: lightWanted}); }
  catch (error) { lightWanted = false; $('light').checked = false; notify(error.message); }
};
setInterval(async () => {
  if (lightWanted && !document.hidden) {
    try { await api('camera/light', {enabled: true, renew: true}); }
    catch { lightWanted = false; $('light').checked = false; }
  }
}, 5000);
document.addEventListener('visibilitychange', () => {
  if (document.hidden && lightWanted) {
    lightWanted = false; $('light').checked = false;
    api('camera/light', {enabled: false}).catch(() => {});
  }
});
function estimate() {
  const seconds = Math.max(0, (Number($('count').value) - 1) * Number($('interval').value));
  $('capture-estimate').textContent = `Manual plan: ${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} sec`;
  const interval = Number($('assay-interval').value), length = Number($('assay-duration').value) * 60;
  $('assay-plan').textContent = interval >= 2 && length > 0 ? `Up to ${Math.ceil(length / interval)} photos · First capture immediately · Uses PID settings below` : 'Set a duration and photo interval.';
}
for (const id of ['count', 'interval', 'assay-duration', 'assay-interval']) $(id).oninput = estimate;
function showTab(name) {
  document.querySelectorAll('.tab').forEach(button => button.classList.toggle('active', button.dataset.tab === name));
  document.querySelectorAll('.tab-view').forEach(view => view.hidden = view.id !== name);
}
document.querySelectorAll('.tab').forEach(button => button.onclick = async () => {
  showTab(button.dataset.tab);
  if (button.dataset.tab === 'gallery') await refreshGallery();
  if (button.dataset.tab === 'logs') { await refreshCatalog(); await loadLogs(); }
});
function element(tag, text, className) {
  const item = document.createElement(tag);
  if (text != null) item.textContent = text;
  if (className) item.className = className;
  return item;
}
function options(id, values, first) {
  const select = $(id), chosen = select.value;
  select.replaceChildren(new Option(first, ''));
  values.forEach(([value, text]) => select.add(new Option(text, value)));
  if ([...select.options].some(option => option.value === chosen)) select.value = chosen;
}
async function refreshCatalog() {
  try {
    const data = await api('assays');
    assayRecords = data.items;
    $('photo-count').textContent = assayRecords.reduce((sum, record) => sum + record.photo_count, 0);
    const operators = data.operators.map(name => [name, name]);
    options('archive-operator', operators, 'All operators');
    options('log-operator', operators, 'All operators');
    $('operator-names').replaceChildren(...data.operators.map(name => new Option(name, name)));
    updateLogOptions();
  } catch (error) { notify(error.message); }
}
function updateLogOptions() {
  const operator = $('log-operator').value;
  options('log-assay', assayRecords.filter(r => !operator || r.operator === operator).map(r => [r.id, `${r.name}${r.operator ? ' · ' + r.operator : ''}${r.started_at ? ' · ' + localDate(r.started_at) : ''}`]), 'All assays');
}
function renderFolders() {
  const operator = $('archive-operator').value, query = $('assay-search').value.trim().toLowerCase();
  const records = assayRecords.filter(r => (!operator || r.operator === operator) && (!query || `${r.name} ${r.operator}`.toLowerCase().includes(query)));
  $('folder-grid').replaceChildren();
  for (const record of records) {
    const card = element('button', null, 'folder-card');
    card.append(element('span', record.id === 'unassigned' ? 'MANUAL CAPTURES' : 'ASSAY FOLDER', 'eyebrow'), element('h3', record.name), element('p', record.operator || 'Standalone captures & earlier data'), element('small', record.started_at ? localDate(record.started_at) : 'Not assigned to an assay'));
    const foot = element('div', null, 'folder-card-foot');
    foot.append(element('span', `${record.photo_count} photos · ${record.log_count} logs`), element('span', record.status, 'pill'));
    card.append(foot); card.onclick = () => openFolder(record.id);
    $('folder-grid').append(card);
  }
  if (!records.length) $('folder-grid').append(element('div', 'No matching assays. Try another operator or search.', 'empty'));
}
function folderView() {
  const record = assayRecords.find(r => r.id === selectedAssay);
  $('folder-filters').hidden = $('folder-grid').hidden = !!selectedAssay;
  $('photo-browser').hidden = !selectedAssay;
  $('gallery-title').textContent = record ? record.name : 'Assay folders';
  $('gallery-description').textContent = record ? `${record.operator || 'Independent instrument use'}${record.started_at ? ' · ' + localDate(record.started_at) : ''}` : 'Find your experiments by operator or assay name.';
  if (record) {
    $('folder-summary').textContent = record.id === 'unassigned' ? 'Photos and logs from independent controls, including data captured before assay folders were introduced.' : `${record.status.toUpperCase()} · ${record.duration_minutes} min · ${record.target} °C · Photo interval ${record.interval_s} sec · PID ${record.pid.kp} / ${record.pid.ki} / ${record.pid.kd}${record.reason ? ' · ' + record.reason : ''}`;
    $('delete-assay').hidden = record.id === 'unassigned';
    $('delete-assay').disabled = ['running', 'starting'].includes(record.status);
  }
}
async function openFolder(id) { selectedAssay = id; page = 0; $('filter-date').value = ''; folderView(); await loadGallery(); await refreshUSB(); }
async function refreshGallery() {
  await refreshCatalog();
  if (selectedAssay && !assayRecords.some(r => r.id === selectedAssay)) selectedAssay = null;
  folderView();
  if (selectedAssay) await loadGallery(); else renderFolders();
}
function photoUrl(photo, thumbnail = false, download = false) {
  return `/photos/${photo.id}${thumbnail ? '.thumb' : ''}.jpg?assay_id=${encodeURIComponent(photo.assay_id)}${download ? '&download=1' : ''}`;
}
async function loadGallery() {
  if (!selectedAssay) return;
  const epoch = ++archiveEpoch;
  try {
    let data = await api(`photos?assay_id=${encodeURIComponent(selectedAssay)}&page=${page}&date=${$('filter-date').value}`);
    if (epoch !== archiveEpoch) return;
    if (page > 0 && !data.items.length) { page = Math.max(0, Math.ceil(data.total / 24) - 1); return loadGallery(); }
    photos = data.items; total = data.total;
    $('gallery-grid').replaceChildren();
    if (!photos.length) $('gallery-grid').append(element('div', 'No photos in this folder for the selected date.', 'empty'));
    photos.forEach((photo, i) => {
      const card = element('button', null, 'photo-card'), img = element('img');
      img.src = photoUrl(photo, true); img.alt = 'Photo ' + localDate(photo.utc); img.loading = 'lazy';
      const info = element('div', localDate(photo.utc), 'photo-info');
      info.append(element('small', `${photo.temperature == null ? 'Temperature not recorded' : number(photo.temperature) + ' °C'} · View original ↗`));
      card.append(img, info); card.onclick = () => showPhoto(i); $('gallery-grid').append(card);
    });
    $('page-info').textContent = `Page ${page + 1} / ${Math.max(1, Math.ceil(total / 24))} · ${total} photos`;
    $('prev-page').disabled = page === 0; $('next-page').disabled = (page + 1) * 24 >= total;
  } catch (error) { notify(error.message); }
}
function showPhoto(i) {
  if (!photos[i]) return;
  selected = i; const photo = photos[i];
  $('full-photo').src = photoUrl(photo);
  $('photo-title').textContent = `${localDate(photo.utc)} · ${photo.temperature == null ? 'No temperature' : number(photo.temperature) + ' °C'}`;
  $('download-photo').href = photoUrl(photo, false, true);
  $('previous-photo').disabled = i === 0 && page === 0;
  $('next-photo').disabled = i === photos.length - 1 && (page + 1) * 24 >= total;
  $('delete-photo').disabled = state?.assay.active?.id === photo.assay_id;
  if (!$('photo-dialog').open) $('photo-dialog').showModal();
}
$('close-photo').onclick = () => $('photo-dialog').close();
$('previous-photo').onclick = async () => { if (selected > 0) showPhoto(selected - 1); else if (page > 0) { page--; await loadGallery(); showPhoto(photos.length - 1); } };
$('next-photo').onclick = async () => { if (selected < photos.length - 1) showPhoto(selected + 1); else if ((page + 1) * 24 < total) { page++; await loadGallery(); showPhoto(0); } };
$('photo-dialog').onkeydown = event => { if (event.key === 'ArrowLeft' && !$('previous-photo').disabled) $('previous-photo').click(); if (event.key === 'ArrowRight' && !$('next-photo').disabled) $('next-photo').click(); };
$('prev-page').onclick = () => { page--; loadGallery(); };
$('next-page').onclick = () => { page++; loadGallery(); };
$('filter-date').onchange = () => { page = 0; loadGallery(); };
$('back-folders').onclick = () => { selectedAssay = null; archiveEpoch++; refreshGallery(); };
$('refresh-gallery').onclick = refreshGallery;
$('archive-operator').onchange = renderFolders;
$('assay-search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(renderFolders, 150); };
$('folder-logs').onclick = async () => { showTab('logs'); await refreshCatalog(); $('log-operator').value = ''; updateLogOptions(); $('log-assay').value = selectedAssay; await loadLogs(); };
async function deleteItem(kind, assayId, name, label) {
  if (!confirm(`Move ${label} to Trash?${kind === 'assay' ? ' All photos and logs in this assay will be moved together.' : ''} You can undo this deletion.`)) return false;
  try {
    const result = await api('archive/delete', {kind, assay_id: assayId, name});
    lastTrash = result.trash_id; $('undo-bar').hidden = false;
    notify('Moved to Trash. Use Undo deletion to restore.');
    return true;
  } catch (error) { notify(error.message); return false; }
}
$('delete-photo').onclick = async () => { const photo = photos[selected]; if (photo && await deleteItem('photos', photo.assay_id, photo.id + '.jpg', 'this photo and its metadata')) { $('photo-dialog').close(); await refreshGallery(); } };
$('delete-assay').onclick = async () => { const record = assayRecords.find(r => r.id === selectedAssay); if (record && await deleteItem('assay', record.id, null, `assay "${record.name}"`)) { selectedAssay = null; await refreshGallery(); } };
$('undo-delete').onclick = async () => { if (!lastTrash) return; const result = await act('archive/restore', {trash_id: lastTrash}, 'Deleted item restored'); if (result) { lastTrash = null; $('undo-bar').hidden = true; await refreshGallery(); await loadLogs(); } };
$('dismiss-undo').onclick = () => $('undo-bar').hidden = true;
async function loadLogs() {
  try {
    const data = await api(`logs?assay_id=${encodeURIComponent($('log-assay').value)}&operator=${encodeURIComponent($('log-operator').value)}`);
    $('log-list').replaceChildren();
    if (!data.items.length) $('log-list').append(element('div', 'No matching temperature logs. Start a heating run or assay to create one.', 'empty'));
    for (const log of data.items) {
      const row = element('div', null, 'log-row'), details = element('div', null, 'log-details');
      details.append(element('strong', log.assay_name), element('small', log.operator || 'Independent heating'), element('span', log.name));
      const actions = element('div', null, 'log-actions'), link = element('a', 'Download CSV ↓');
      link.href = `/logs/${log.name}?assay_id=${encodeURIComponent(log.assay_id)}`;
      const remove = element('button', 'Delete', 'danger');
      remove.disabled = state?.assay.active?.id === log.assay_id || (state?.heater.active && (state.heater.assay_id || 'unassigned') === log.assay_id);
      remove.onclick = async () => { if (await deleteItem('logs', log.assay_id, log.name, `log "${log.name}"`)) await loadLogs(); };
      actions.append(link, remove); row.append(details, actions); $('log-list').append(row);
    }
  } catch (error) { notify(error.message); }
}
$('refresh-logs').onclick = async () => { await refreshCatalog(); await loadLogs(); };
$('log-operator').onchange = () => { updateLogOptions(); loadLogs(); };
$('log-assay').onchange = loadLogs;
function drawChart(points){const cv=$('chart'),box=cv.getBoundingClientRect();if(!box.width)return;const dpr=window.devicePixelRatio||1;cv.width=box.width*dpr;cv.height=box.height*dpr;const ctx=cv.getContext('2d');ctx.scale(dpr,dpr);const w=box.width,h=box.height,left=30,top=10,bottom=h-25;points=points.filter(p=>p.t>Date.now()/1000-600);const values=points.flatMap(p=>[p.temperature,...(p.target!=null?[p.target]:[])]);const low=values.length?Math.floor(Math.min(...values)/10)*10:0,high=values.length?Math.max(low+10,Math.ceil(Math.max(...values)/10)*10):100;const y=v=>bottom-(v-low)/(high-low)*(bottom-top);ctx.font='9px sans-serif';ctx.fillStyle='#94a091';for(let i=0;i<=3;i++){const v=low+(high-low)*i/3;ctx.strokeStyle='#edf0e9';ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(w,y(v));ctx.stroke();ctx.fillText(Math.round(v),0,y(v)+3);}if(!points.length){ctx.fillStyle='#9aa597';ctx.textAlign='center';ctx.fillText('Waiting for valid temperature readings',w/2,h/2);return;}const end=Date.now()/1000;const x=t=>left+(t-(end-600))/600*(w-left);ctx.strokeStyle='#c4b48b';ctx.setLineDash([4,4]);ctx.beginPath();let started=false;for(const p of points){if(p.target!=null){if(!started)ctx.moveTo(x(p.t),y(p.target));else ctx.lineTo(x(p.t),y(p.target));started=true;}}ctx.stroke();ctx.setLineDash([]);ctx.strokeStyle='#4f8a67';ctx.lineWidth=1.8;ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(x(p.t),y(p.temperature)):ctx.moveTo(x(p.t),y(p.temperature)));ctx.stroke();}
async function chart(){try{drawChart(await api('history'));}catch{}setTimeout(chart,4000);}

poll(); preview(); refreshCatalog(); chart(); estimate();

// Export jobs run on the Pi. Polling never holds the hardware control lock.
let exportTimer = null, currentExport = null;
async function refreshUSB() {
  try {
    const data = await api('usb');
    options('usb-drive', data.items.map(d => [d.id, `${d.label} · ${(d.free_bytes / 1e9).toFixed(1)} GB free`]), 'Select a USB drive');
    if (data.items.length === 1) $('usb-drive').value = data.items[0].id;
    $('usb-note').textContent = data.items.length ? 'Wait for Export complete, then safely eject the drive using the Pi desktop before unplugging.' : 'No writable USB drive found. Insert one into the Raspberry Pi and open it in the Pi file manager to mount it, then click Refresh USB drives.';
  } catch (error) { $('usb-note').textContent = error.message; }
}
function displayExport(job) {
  currentExport = job;
  const running = job?.status === 'running';
  $('download-folder').disabled = $('export-usb').disabled = !!running;
  $('export-status').hidden = !job;
  $('export-download').hidden = !job || job.mode !== 'photos' || job.status !== 'complete';
  if (!job) return;
  $('export-status').textContent = job.status === 'running' ? `Exporting ${job.name}: ${job.completed} / ${job.total} files.${job.mode === 'usb' ? ' Keep the USB connected.' : ''}` : job.status === 'failed' ? `Export failed: ${job.error}` : job.mode === 'usb' ? `Export complete: ${job.destination}. You can now safely eject the USB drive from the Pi desktop.` : `ZIP ready: ${job.name}. Click Download ready ZIP below to save it to this computer.`;
  if (job.mode === 'photos' && job.status === 'complete') $('export-download').href = `/exports/${job.id}/download`;
  clearTimeout(exportTimer);
  if (running) exportTimer = setTimeout(pollExport, 1000);
}
async function pollExport() {
  try { displayExport((await api('exports/current')).job); }
  catch (error) {
    $('export-status').hidden = false;
    $('export-status').textContent = 'Connection lost while checking export. Keep the USB connected; reconnect to check completion.';
    exportTimer = setTimeout(pollExport, 3000);
  }
}
async function beginExport(mode) {
  if (!selectedAssay) return;
  const driveId = $('usb-drive').value;
  if (mode === 'usb' && !driveId) { notify('Select a USB drive connected to the Raspberry Pi first.'); return; }
  $('download-folder').disabled = $('export-usb').disabled = true;
  try { displayExport(await api('exports', {assay_id: selectedAssay, mode, drive_id: driveId})); }
  catch (error) { notify(error.message); displayExport(currentExport); }
}
$('download-folder').onclick = () => beginExport('photos');
$('export-usb').onclick = () => beginExport('usb');
$('refresh-usb').onclick = refreshUSB;
pollExport();
