'use strict';
let reactionRegions = null, reactionPoints = [], reactionKey = null, reactionBusy = false;
let reactionFolder = 'unassigned', reactionGeneration = 0, reactionCatalog = '';

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
    svg.replaceChildren(); $('reaction-legend').replaceChildren();
    for (const region of reactionRegions.coordinates) {
      const [x, y] = region.center, r = region.radius;
      svg.append(reactionSvg('circle', {cx:x, cy:y, r, fill:'none', stroke:region.color, 'stroke-width':12}));
      const dx = region.id === 2 ? r+35 : region.id === 4 ? -r-35 : 0;
      const dy = region.id === 1 ? -r-45 : region.id === 3 ? r+95 : 25;
      svg.append(reactionSvg('text', {x:x+dx, y:y+dy, fill:region.color,
        stroke:'#06140f', 'stroke-width':12, 'paint-order':'stroke', 'font-size':80,
        'font-weight':700, 'text-anchor':region.id === 2 ? 'start' : region.id === 4 ? 'end' : 'middle'}, `Region ${region.id}`));
      const label = element('span', `Region ${region.id}`);
      label.style.setProperty('--region-color', region.color);
      $('reaction-legend').append(label);
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

function resetReactionView() {
  reactionGeneration++; reactionKey = null; reactionPoints = [];
  $('reaction-download').hidden = true;
  badge('reaction-status', 'Loading');
  $('reaction-detail').textContent = 'Loading reaction measurements…';
  drawReactionChart();
}
function chooseReactionFolder(id, follow = false) {
  reactionFolder = id; $('reaction-assay').value = id;
  $('reaction-follow').checked = follow;
  $('reaction-session').replaceChildren(new Option('Latest run', ''));
  resetReactionView(); pollReaction(false);
}
$('reaction-assay').onchange = () => chooseReactionFolder($('reaction-assay').value);
$('reaction-session').onchange = () => {
  $('reaction-follow').checked = false; resetReactionView(); pollReaction(false);
};
$('reaction-follow').onchange = () => {
  $('reaction-session').value = ''; resetReactionView(); pollReaction(false);
};
$('folder-reaction').onclick = () => {
  showTab('live'); chooseReactionFolder(selectedAssay);
  $('reaction-card').scrollIntoView({behavior:'smooth', block:'start'});
};

async function pollReaction(schedule = true) {
  let acquired = false;
  try {
    if (document.hidden || $('live').hidden || reactionBusy) return;
    const catalog = JSON.stringify(assayRecords.map(r => [r.id,r.name,r.operator]));
    if (catalog !== reactionCatalog && assayRecords.length) {
      $('reaction-assay').replaceChildren(...assayRecords.map(r => new Option(`${r.name}${r.operator ? ' · '+r.operator : ''}`, r.id)));
      if (!assayRecords.some(r => r.id === reactionFolder)) { reactionFolder = 'unassigned'; resetReactionView(); }
      $('reaction-assay').value = reactionFolder; reactionCatalog = catalog;
    }
    const follow = $('reaction-follow').checked;
    const activeFolder = state?.assay.active?.id || state?.camera.assay_id || 'unassigned';
    if (follow && reactionFolder !== activeFolder) {
      reactionFolder = activeFolder; $('reaction-assay').value = activeFolder;
      $('reaction-session').value = ''; resetReactionView();
    }
    const session = $('reaction-session').value;
    const key = JSON.stringify([reactionFolder, session, follow, state?.camera.latest, state?.camera.reaction_id, archiveEpoch]);
    if (key === reactionKey) return;
    reactionBusy = true;
    acquired = true;
    const generation = reactionGeneration, folder = reactionFolder;
    const data = await api(`reaction?assay_id=${encodeURIComponent(folder)}${session ? '&session_id='+encodeURIComponent(session) : ''}`);
    if (generation !== reactionGeneration) return;
    $('reaction-session').replaceChildren(new Option('Latest run', ''), ...data.sessions.map(s => new Option(`${localDate(s.utc)} · ${s.count} photos`, s.id)));
    $('reaction-session').value = session;
    const pending = follow && state?.camera.reaction_id && state.camera.reaction_id !== data.session_id;
    reactionPoints = pending ? [] : data.points;
    const count = reactionPoints.length;
    badge('reaction-status', count ? `${count} photos analyzed` : 'Waiting for photos', count ? 'ok' : '');
    $('reaction-detail').textContent = count ? `${$('reaction-assay').selectedOptions[0]?.textContent || folder} · ${localDate(reactionPoints[0].utc)} · Latest ${number(reactionPoints[count-1].time_min,2)} min` : 'Start an assay or timed acquisition to record a new curve. Earlier photos without reaction logs are not analyzed retroactively.';
    $('reaction-download').hidden = !count;
    if (count) $('reaction-download').href = `/reaction.csv?assay_id=${encodeURIComponent(folder)}&session_id=${encodeURIComponent(data.session_id)}`;
    errorBox('reaction-error', null); reactionKey = key; drawReactionChart();
  } catch (error) {
    badge('reaction-status', 'Could not update', 'error'); errorBox('reaction-error', error.message);
  } finally {
    if (acquired) reactionBusy = false;
    if (schedule) setTimeout(pollReaction, 2000);
  }
}

function drawReactionChart() {
  const canvas = $('reaction-chart'), bounds = canvas.getBoundingClientRect();
  if (!bounds.width) return;
  const dpr = window.devicePixelRatio || 1, w = bounds.width, h = bounds.height;
  canvas.width = w*dpr; canvas.height = h*dpr;
  const ctx = canvas.getContext('2d'); ctx.scale(dpr,dpr);
  const left = w < 500 ? 64 : 80, right = w-18, top = 30, bottom = h-48;
  const end = reactionPoints.reduce((end,p) => Math.max(end,p.time_min),1);
  let peak = 1;
  for (const point of reactionPoints) for (const value of point.corrected) peak = Math.max(peak,value);
  const high = peak*1.1, x = v => left+v/end*(right-left), y = v => bottom-v/high*(bottom-top);
  ctx.font = '11px sans-serif'; ctx.fillStyle = '#637468';
  ctx.fillText('Corrected fluorescence (a.u.)', left, 15);
  for (let i=0; i<=4; i++) {
    const value = high*i/4;
    ctx.strokeStyle = '#e8ede7'; ctx.beginPath(); ctx.moveTo(left,y(value)); ctx.lineTo(right,y(value)); ctx.stroke();
    const tick = high >= 1000000 ? (value/1000000).toFixed(2)+'M' : high >= 10000 ? (value/1000).toFixed(1)+'k' : Math.round(value).toLocaleString();
    ctx.textAlign = 'right'; ctx.fillText(tick, left-8,y(value)+4);
    ctx.textAlign = 'center'; ctx.fillText((end*i/4).toFixed(1), x(end*i/4), bottom+20);
  }
  ctx.fillText('Time since first photo (min)', (left+right)/2,h-6);
  if (!reactionPoints.length) {
    ctx.fillText('Waiting for saved reaction photos', (left+right)/2,(top+bottom)/2); return;
  }
  (reactionRegions?.coordinates || []).forEach((region,i) => {
    ctx.strokeStyle = ctx.fillStyle = region.color; ctx.lineWidth = 2;
    ctx.beginPath(); reactionPoints.forEach((p,j) => j ? ctx.lineTo(x(p.time_min),y(p.corrected[i])) : ctx.moveTo(x(p.time_min),y(p.corrected[i]))); ctx.stroke();
    const point = reactionPoints[reactionPoints.length-1]; ctx.beginPath(); ctx.arc(x(point.time_min),y(point.corrected[i]),3,0,2*Math.PI); ctx.fill();
  });
}
new ResizeObserver(drawReactionChart).observe($('reaction-chart'));
loadReactionRegions(); pollReaction();
