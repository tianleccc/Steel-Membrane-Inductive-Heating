'use strict';
let reactionRegions = null, reactionPoints = [], reactionKey = null, reactionBusy = false;
let liveReactionSession = null;
let archiveReactionFolder = null, archiveReactionPoints = [], archiveReactionKey = null, archiveReactionBusy = false;

function reactionSvg(tag, attributes, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  if (text) node.textContent = text;
  return node;
}
async function loadReactionRegions() {
  try {
    reactionRegions = await api('reaction/regions');
    const svg = $('reaction-overlay');
    svg.setAttribute('viewBox', `0 0 ${reactionRegions.image_size.join(' ')}`);
    svg.replaceChildren();
    for (const id of ['reaction-legend', 'archive-reaction-legend']) $(id).replaceChildren();
    for (const region of reactionRegions.coordinates) {
      const [x, y] = region.center, r = region.radius;
      svg.append(reactionSvg('circle', {cx:x, cy:y, r, fill:'none', stroke:region.color, 'stroke-width':12}));
      const dx = region.id === 2 ? r+35 : region.id === 4 ? -r-35 : 0;
      const dy = region.id === 1 ? -r-45 : region.id === 3 ? r+95 : 25;
      svg.append(reactionSvg('text', {x:x+dx, y:y+dy, fill:region.color,
        stroke:'#06140f', 'stroke-width':12, 'paint-order':'stroke', 'font-size':80,
        'font-weight':700, 'text-anchor':region.id === 2 ? 'start' : region.id === 4 ? 'end' : 'middle'}, `Region ${region.id}`));
      for (const id of ['reaction-legend', 'archive-reaction-legend']) {
        const label = element('span', `Region ${region.id}`);
        label.style.setProperty('--region-color', region.color);
        $(id).append(label);
      }
    }
    drawReactionChart(); updateReactionOverlay();
  } catch (error) { errorBox('reaction-error', error.message); }
}
function updateReactionOverlay() {
  $('reaction-overlay').toggleAttribute('hidden', !reactionRegions || !$('check-reaction-area').checked || $('preview').hidden);
}
$('check-reaction-area').onchange = updateReactionOverlay;
$('preview').addEventListener('load', updateReactionOverlay);
setInterval(updateReactionOverlay, 500);

function syncReactionFolder() {
  if (archiveReactionFolder === selectedAssay) return;
  archiveReactionFolder = selectedAssay; archiveReactionPoints = []; archiveReactionKey = null;
  $('archive-reaction-session').replaceChildren(new Option('Latest run', ''));
  $('archive-reaction-download').hidden = true;
  $('archive-reaction-folder').textContent = assayRecords.find(r => r.id === selectedAssay)?.name || '';
  $('archive-reaction-detail').textContent = 'Loading saved reaction data…';
  badge('archive-reaction-status', 'Loading'); errorBox('archive-reaction-error', null);
  drawReactionChart();
}
$('view-amplification').onclick = () => {
  syncReactionFolder(); loadArchiveReaction();
  $('archive-reaction-card').scrollIntoView({behavior:'smooth', block:'start'});
};
$('archive-reaction-session').onchange = () => {
  archiveReactionKey = null; archiveReactionPoints = []; drawReactionChart(); loadArchiveReaction();
};

async function loadCurrentReaction() {
  if ($('live').hidden || reactionBusy) return;
  const session = state?.camera.reaction_id || null;
  if (session !== liveReactionSession) {
    liveReactionSession = session; reactionPoints = []; reactionKey = null; drawReactionChart();
  }
  const key = JSON.stringify([session, state?.camera.latest, state?.camera.running, state?.camera.busy, archiveEpoch]);
  if (key === reactionKey) return;
  reactionBusy = true;
  try {
    const data = await api('reaction/current');
    if (session !== (state?.camera.reaction_id || null)) return;
    reactionPoints = data.points;
    const count = reactionPoints.length;
    badge('reaction-status', count ? `${data.acquiring ? 'Live' : 'Current run'} · ${count} photos` : 'Waiting for current acquisition', count ? 'ok' : '');
    $('reaction-detail').textContent = count ? `${assayRecords.find(r => r.id === data.assay_id)?.name || 'Unassigned'} · Current acquisition · ${localDate(reactionPoints[0].utc)} · Latest ${number(reactionPoints[count-1].time_min,2)} min` : 'Start an assay or timed acquisition. Saved runs are available inside their Photo archive folders.';
    errorBox('reaction-error', null); reactionKey = key; drawReactionChart();
  } catch (error) { errorBox('reaction-error', error.message); }
  finally { reactionBusy = false; }
}

async function loadArchiveReaction() {
  syncReactionFolder();
  if ($('gallery').hidden || !selectedAssay || archiveReactionBusy) return;
  const folder = selectedAssay, session = $('archive-reaction-session').value;
  const key = JSON.stringify([folder, session, state?.camera.latest, archiveEpoch]);
  if (key === archiveReactionKey) return;
  archiveReactionBusy = true;
  try {
    const data = await api(`reaction?assay_id=${encodeURIComponent(folder)}${session ? '&session_id='+encodeURIComponent(session) : ''}`);
    if (folder !== selectedAssay || session !== $('archive-reaction-session').value) return;
    $('archive-reaction-session').replaceChildren(new Option('Latest run', ''), ...data.sessions.map(s => new Option(`${localDate(s.utc)} · ${s.count} photos`, s.id)));
    $('archive-reaction-session').value = session;
    archiveReactionPoints = data.points;
    const count = data.points.length;
    badge('archive-reaction-status', count ? `${count} photos · Selected run` : 'No recorded reaction data', count ? 'ok' : '');
    $('archive-reaction-detail').textContent = count ? `${localDate(data.points[0].utc)} · ${number(data.points[count-1].time_min,2)} min · Region colors match the preview.` : 'This folder has no recorded reaction measurements. Earlier photos are not analyzed retroactively.';
    $('archive-reaction-download').hidden = !data.sessions.length;
    $('archive-reaction-download').href = `/reaction.csv?assay_id=${encodeURIComponent(folder)}`;
    errorBox('archive-reaction-error', null); archiveReactionKey = key; drawReactionChart();
  } catch (error) {
    if (folder === selectedAssay) errorBox('archive-reaction-error', error.message);
  } finally { archiveReactionBusy = false; }
}
async function pollReaction() {
  try {
    if (!document.hidden) { await loadCurrentReaction(); await loadArchiveReaction(); }
  } finally { setTimeout(pollReaction, 2000); }
}

function drawReactionChart() {
  drawFluorescenceChart('reaction-chart', reactionPoints, 'corrected');
  drawFluorescenceChart('archive-raw-chart', archiveReactionPoints, 'raw');
  drawFluorescenceChart('archive-corrected-chart', archiveReactionPoints, 'corrected');
}
function drawFluorescenceChart(id, points, field) {
  const canvas = $(id), bounds = canvas.getBoundingClientRect();
  if (!bounds.width) return;
  const dpr = window.devicePixelRatio || 1, w = bounds.width, h = bounds.height;
  canvas.width = w*dpr; canvas.height = h*dpr;
  const ctx = canvas.getContext('2d'); ctx.scale(dpr,dpr);
  const left = w < 500 ? 64 : 80, right = w-18, top = 30, bottom = h-48;
  const end = points.reduce((end,p) => Math.max(end,p.time_min),1);
  let peak = 1;
  for (const point of points) for (const value of point[field]) peak = Math.max(peak,value);
  const high = peak*1.1, x = v => left+v/end*(right-left), y = v => bottom-v/high*(bottom-top);
  ctx.font = '11px sans-serif'; ctx.fillStyle = '#637468';
  ctx.fillText(`${field === 'raw' ? 'Raw' : 'Corrected'} fluorescence (a.u.)`, left, 15);
  for (let i=0; i<=4; i++) {
    const value = high*i/4;
    ctx.strokeStyle = '#e8ede7'; ctx.beginPath(); ctx.moveTo(left,y(value)); ctx.lineTo(right,y(value)); ctx.stroke();
    const tick = high >= 1000000 ? (value/1000000).toFixed(2)+'M' : high >= 10000 ? (value/1000).toFixed(1)+'k' : Math.round(value).toLocaleString();
    ctx.textAlign = 'right'; ctx.fillText(tick, left-8,y(value)+4);
    ctx.textAlign = 'center'; ctx.fillText((end*i/4).toFixed(1), x(end*i/4), bottom+20);
  }
  ctx.fillText('Time since first photo (min)', (left+right)/2,h-6);
  if (!points.length) {
    ctx.fillText(id === 'reaction-chart' ? 'Waiting for current acquisition' : 'No recorded reaction data', (left+right)/2,(top+bottom)/2); return;
  }
  (reactionRegions?.coordinates || []).forEach((region,i) => {
    ctx.strokeStyle = ctx.fillStyle = region.color; ctx.lineWidth = 2;
    ctx.beginPath(); points.forEach((p,j) => j ? ctx.lineTo(x(p.time_min),y(p[field][i])) : ctx.moveTo(x(p.time_min),y(p[field][i]))); ctx.stroke();
    const point = points[points.length-1]; ctx.beginPath(); ctx.arc(x(point.time_min),y(point[field][i]),3,0,2*Math.PI); ctx.fill();
  });
}
const reactionResize = new ResizeObserver(drawReactionChart);
for (const id of ['reaction-chart','archive-raw-chart','archive-corrected-chart']) reactionResize.observe($(id));
loadReactionRegions(); pollReaction();
