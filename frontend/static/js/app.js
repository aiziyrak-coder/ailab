/* MedLab AI – Laboratoriya Tahlil Tizimi */
/* apiPath, getCookie, csrfHeaders — auth.js (index.html da oldin yuklanadi) */

// ─── State ───────────────────────────────────────────────────────────────────
let currentLab   = 'hematology';
let currentView  = 'camera';
let uploadedFiles = [];   // ko'p fayl
let cameraRunning = false;

const LAB_META = {
  hematology: { icon:'🩸', name:'Gematologiya Natijasi',  color:'var(--hema)'  },
  urine:      { icon:'🧪', name:'Siydik Tahlili Natijasi', color:'var(--urine)' },
  coprology:  { icon:'🦠', name:'Koprologiya Natijasi',   color:'var(--copro)' },
  spermogram: { icon:'🔵', name:'Sperma Analiz Natijasi', color:'var(--sperm)' },
  smear:      { icon:'🌸', name:'Mazok: sitologiya + flora', color:'var(--smear)' },
  csf:        { icon:'🧠', name:'Likvor (OMS) natijasi',     color:'var(--csf)'   },
  lymph:      { icon:'💠', name:'Limfa suyuqligi natijasi',  color:'var(--lymph)' },
  le_cell:    { icon:'✴️', name:'LE-hujayra tahlili',        color:'var(--le)'    },
  prostata_sok: { icon:'🔷', name:'Prostata SOK natijasi',   color:'var(--prost)' },
  myelogram: { icon:'🦴', name:'Miyelogramma natijasi', color:'var(--myelo)' },
  blood_parasites: { icon:'🦟', name:'Qon parazitlari tahlili', color:'var(--parasite)' },
  afb_microscopy: { icon:'🔬', name:'KOCH / AFB mikroskopiyasi', color:'var(--afb)' },
  mycology: { icon:'🍄', name:'Mikologiya tahlili', color:'var(--myco)' },
  effusion_cytology: { icon:'💧', name:'Effuziya sitologiyasi', color:'var(--effusion)' },
};

const LAB_TITLE = {
  hematology: '🩸 Gematologiya Tahlili',
  urine:      '🧪 Siydik Analizi Tahlili',
  coprology:  '🦠 Koprologiya Tahlili',
  spermogram: '🔵 Sperma Analiz',
  smear:      '🌸 Mazok: sitologiya va flora (alohida)',
  csf:        '🧠 Likvor: orqa miya suyuqligi tahlili',
  lymph:      '💠 Limfa suyuqligi mikroskopiyasi',
  le_cell:    '✴️ LE-hujayra (lupus hujayrasi) tahlili',
  prostata_sok: '🔷 Prostata SOK (suyuqlik) mikroskopiyasi',
  myelogram: '🦴 Miyelogramma (suyak mozgi qoni)',
  blood_parasites: '🦟 Qon parazitlari (malariya, mikrofilariya, boshqalar)',
  afb_microscopy: '🔬 KOCH / kislotalik tikanlar (AFB)',
  mycology: '🍄 Chuqur mikologiya (zamburug‘, gifa, maya)',
  effusion_cytology: '💧 Effuziya sitologiyasi (pleura, periton, perikard)',
};

// ─── Lab tanlash ─────────────────────────────────────────────────────────────
function selectLab(lab) {
  if (!LAB_META[lab]) lab = 'hematology';
  currentLab = lab;
  document.querySelectorAll('.lab-tab').forEach(t => t.classList.remove('active'));
  const tab = document.querySelector('[data-lab="' + lab + '"]');
  if (tab) tab.classList.add('active');

  const m = LAB_META[lab];
  document.getElementById('labTitle').textContent      = LAB_TITLE[lab];
  document.getElementById('resultLabIcon').textContent = m.icon;
  document.getElementById('resultLabName').textContent = m.name;
  document.getElementById('resultLabName').style.color = m.color;
}

// ─── Mikroskop holati ────────────────────────────────────────────────────────
function parseMagNum(s) {
  if (!s) return NaN;
  const m = String(s).match(/[\d.]+/);
  return m ? parseFloat(m[0]) : NaN;
}

function getOcularStr() {
  const sel = document.getElementById('microOcularSel');
  if (!sel) return '';
  if (sel.value === '__custom__') return (document.getElementById('microOcularInp')?.value || '').trim();
  return (sel.value || '').trim();
}

function getObjectiveStr() {
  const sel = document.getElementById('microObjSel');
  if (!sel) return '';
  if (sel.value === '__custom__') return (document.getElementById('microObjInp')?.value || '').trim();
  return (sel.value || '').trim();
}

function getMicroscopePayload() {
  const ocular = getOcularStr();
  const objective = getObjectiveStr();
  const badge = document.getElementById('microTotalBadge');
  let total_label = '';
  if (badge && badge.textContent && badge.textContent !== '—') total_label = badge.textContent.trim();
  return {
    ocular,
    objective,
    total_label,
    condenser:    (document.getElementById('microCondenserSel')?.value || '').trim(),
    illumination: (document.getElementById('microIllumSel')?.value || '').trim(),
    notes:        (document.getElementById('microNotes')?.value || '').trim(),
  };
}

function appendMicroscopeToFormData(fd) {
  const m = getMicroscopePayload();
  fd.append('micro_ocular', m.ocular);
  fd.append('micro_objective', m.objective);
  fd.append('micro_total_label', m.total_label);
  fd.append('micro_condenser', m.condenser);
  fd.append('micro_illumination', m.illumination);
  fd.append('micro_notes', m.notes);
}

/** Umumiy masshtab: faqat DOM dagi okulyar va obyektiv matnidan (qattiq 400× yo‘q) */
function computeMicroscopeTotalDisplay() {
  const oStr = getOcularStr();
  const bStr = getObjectiveStr();
  const no = parseMagNum(oStr);
  const nb = parseMagNum(bStr);
  if (!isNaN(no) && !isNaN(nb) && no > 0 && nb > 0) return Math.round(no * nb) + '×';
  return '';
}

function onMicroChange() {
  const ocSel = document.getElementById('microOcularSel');
  const obSel = document.getElementById('microObjSel');
  const ocInp = document.getElementById('microOcularInp');
  const obInp = document.getElementById('microObjInp');
  if (ocInp) ocInp.style.display = ocSel && ocSel.value === '__custom__' ? 'block' : 'none';
  if (obInp) obInp.style.display = obSel && obSel.value === '__custom__' ? 'block' : 'none';

  const oStr = getOcularStr();
  const bStr = getObjectiveStr();
  const badge = document.getElementById('microTotalBadge');
  const totalStr = computeMicroscopeTotalDisplay();
  if (badge) badge.textContent = totalStr || '—';

  const txtParts = [];
  if (oStr || bStr) txtParts.push([oStr, bStr].filter(Boolean).join(' · '));
  if (totalStr) txtParts.push(totalStr);
  const chipTxt = txtParts.length ? ('🔬 ' + txtParts.join('  |  ')) : '';

  ['microContextChip', 'microContextChip2'].forEach(id => {
    const c = document.getElementById(id);
    if (!c) return;
    if (chipTxt) { c.textContent = chipTxt; c.classList.remove('hidden'); }
    else { c.textContent = ''; c.classList.add('hidden'); }
  });
}

function buildPrintMicroscopeHtml() {
  const m = getMicroscopePayload();
  if (!m.ocular && !m.objective && !m.total_label && !m.condenser && !m.illumination && !m.notes) return '';
  let h = '<strong>🔬 MIKROSKOP HOLATI</strong><br>';
  if (m.ocular)       h += `Okulyar: ${esc(m.ocular)}<br>`;
  if (m.objective)    h += `Obyektiv: ${esc(m.objective)}<br>`;
  if (m.total_label)  h += `Umumiy kattalashtirish: <strong>${esc(m.total_label)}</strong><br>`;
  if (m.condenser)    h += `Kondensor / diyafragma: ${esc(m.condenser)}<br>`;
  if (m.illumination) h += `Yoritish: ${esc(m.illumination)}<br>`;
  if (m.notes)        h += `Izoh: ${esc(m.notes)}<br>`;
  return h;
}

// ─── Fayl yuklash (Ko'p fayl) ─────────────────────────────────────────────────
function handleFileSelect(e) {
  const files = Array.from(e.target.files);
  if (files.length) loadFiles(files);
}

function handleDrop(e) {
  e.preventDefault();
  document.getElementById('uploadZone').classList.remove('drag-active');
  const files = Array.from(e.dataTransfer.files);
  if (files.length) loadFiles(files);
}

function fmtSize(bytes) {
  return bytes > 1048576
    ? (bytes/1048576).toFixed(1) + ' MB'
    : (bytes/1024).toFixed(0) + ' KB';
}

function isVideoFile(file) {
  return file.type.startsWith('video/') || /\.(mp4|avi|mov|mkv|webm|mpeg|m4v)$/i.test(file.name);
}

function loadFiles(files) {
  // Yangi fayllarni mavjud ro'yxatga qo'shish
  for (const f of files) {
    if (!uploadedFiles.find(x => x.name === f.name && x.size === f.size))
      uploadedFiles.push(f);
  }
  renderFileList();
  document.getElementById('uploadZone').style.display  = uploadedFiles.length ? 'none' : '';
  document.getElementById('filePreview').style.display = uploadedFiles.length ? '' : 'none';
  document.getElementById('analyzeFileBtn').disabled   = uploadedFiles.length === 0;

  // Birinchi faylni markazda ko'rsatish
  if (uploadedFiles.length) showMainPreview(uploadedFiles[0]);
  toast(`${files.length} ta fayl qo'shildi`, 'green');
}

function renderFileList() {
  const list = document.getElementById('fileList');
  const cnt  = document.getElementById('previewCount');
  cnt.textContent = `${uploadedFiles.length} ta fayl`;
  list.innerHTML  = '';
  uploadedFiles.forEach((f, i) => {
    const isVid = isVideoFile(f);
    const div   = document.createElement('div');
    div.className = 'file-item';
    div.innerHTML = `
      <span class="file-item-ico">${isVid ? '🎬' : '🖼'}</span>
      <span class="file-item-name" title="${esc(f.name)}">${esc(f.name)}</span>
      <span class="file-item-size">${fmtSize(f.size)}</span>
      <button class="file-item-del" onclick="removeFile(${i})" title="O'chirish">✕</button>
    `;
    div.addEventListener('click', (e) => {
      if (!e.target.classList.contains('file-item-del')) showMainPreview(f);
    });
    list.appendChild(div);
  });
}

function removeFile(idx) {
  uploadedFiles.splice(idx, 1);
  if (uploadedFiles.length === 0) {
    clearFile();
  } else {
    renderFileList();
    showMainPreview(uploadedFiles[0]);
  }
}

function showMainPreview(file) {
  const content = document.getElementById('uploadedContent');
  const reader  = new FileReader();
  const isVid   = isVideoFile(file);
  reader.onload = ev => {
    content.innerHTML = '';
    if (isVid) {
      document.getElementById('previewImg').style.display   = 'none';
      document.getElementById('previewVideo').style.display = '';
      document.getElementById('previewVideo').src            = ev.target.result;
      const vid = document.createElement('video');
      vid.src = ev.target.result; vid.controls = true;
      vid.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain';
      content.appendChild(vid);
    } else {
      document.getElementById('previewImg').src            = ev.target.result;
      document.getElementById('previewImg').style.display  = '';
      document.getElementById('previewVideo').style.display = 'none';
      const img = document.createElement('img');
      img.src = ev.target.result;
      img.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain';
      content.appendChild(img);
    }
    switchView('uploaded');
  };
  reader.readAsDataURL(file);
}

function clearFile() {
  uploadedFiles = [];
  document.getElementById('uploadZone').style.display  = '';
  document.getElementById('filePreview').style.display = 'none';
  document.getElementById('previewImg').style.display  = 'none';
  document.getElementById('previewVideo').src          = '';
  document.getElementById('previewVideo').style.display = 'none';
  document.getElementById('fileInput').value           = '';
  document.getElementById('analyzeFileBtn').disabled   = true;
  document.getElementById('uploadedContent').innerHTML  = `
    <div class="overlay-content">
      <div class="ov-icon">📎</div>
      <p>Rasm yoki video yuklang</p>
    </div>`;
  switchView('camera');
}

// ─── View ────────────────────────────────────────────────────────────────────
function switchView(view) {
  currentView = view;
  document.getElementById('boxCamera').style.display   = view === 'camera'   ? '' : 'none';
  document.getElementById('boxUploaded').style.display = view === 'uploaded' ? '' : 'none';
  document.getElementById('vswCamera').className   = 'vsw' + (view === 'camera'   ? ' active' : '');
  document.getElementById('vswUploaded').className = 'vsw' + (view === 'uploaded' ? ' active' : '');
}

// ─── Kamera ──────────────────────────────────────────────────────────────────
async function scanCameras() {
  const sel = document.getElementById('camSelect');
  sel.innerHTML = '<option>Qidirilmoqda...</option>';
  const res = await api(apiPath('/api/scan_cameras'));
  sel.innerHTML = '<option value="">— Kamera tanlang —</option>';
  if (res.cameras && res.cameras.length > 0) {
    res.cameras.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.index;
      opt.textContent = `[${c.index}] ${c.name} (${c.resolution})`;
      sel.appendChild(opt);
    });
    if (res.cameras.length === 1) sel.selectedIndex = 1;
    toast(`${res.cameras.length} ta kamera topildi`, 'green');
  } else {
    toast('Kamera topilmadi', 'gray');
  }
}

async function startCamera() {
  const sel = document.getElementById('camSelect');
  const idx = parseInt(sel.value);
  if (isNaN(idx)) { toast('Kamera tanlang!', 'red'); return; }
  document.getElementById('startBtn').disabled = true;
  const res = await api(apiPath('/api/start_camera'), 'POST', { index: idx });
  if (res.success) {
    cameraRunning = true;
    document.getElementById('camOverlay').classList.add('hidden');
    document.getElementById('stopBtn').disabled     = false;
    document.getElementById('analyzeCamBtn').disabled = false;
    document.getElementById('connPill').textContent = '● Kamera ulangan';
    document.getElementById('connPill').classList.add('pill-connected');
    msg('camMsg', res.message, 'green');
    toast(res.message, 'green');
    switchView('camera');
  } else {
    document.getElementById('startBtn').disabled = false;
    msg('camMsg', res.message, 'red');
    toast(res.message, 'red');
  }
}

async function stopCamera() {
  await api(apiPath('/api/stop_camera'), 'POST');
  cameraRunning = false;
  document.getElementById('camOverlay').classList.remove('hidden');
  document.getElementById('startBtn').disabled    = false;
  document.getElementById('stopBtn').disabled     = true;
  document.getElementById('analyzeCamBtn').disabled = true;
  document.getElementById('connPill').textContent = '○ Kamera ulanmagan';
  document.getElementById('connPill').classList.remove('pill-connected');
  msg('camMsg', '', '');
  toast("Kamera o'chirildi", 'gray');
}

// ─── Tahlil ──────────────────────────────────────────────────────────────────
async function analyzeFile() {
  if (!uploadedFiles.length) { toast('Fayl yuklanmagan!', 'red'); return; }
  const hasVideo = uploadedFiles.some(isVideoFile);
  startAnalyzing(hasVideo);

  const formData = new FormData();
  for (const f of uploadedFiles) formData.append('files[]', f);
  formData.append('lab_type', currentLab);
  formData.append('source', 'upload');
  const prompt = document.getElementById('customPrompt').value.trim();
  if (prompt) formData.append('prompt', prompt);
  appendMicroscopeToFormData(formData);

  try {
    const r = await fetch(apiPath('/api/analyze'), {
      ...formFetchInit('POST'),
      body: formData,
    });
    let res;
    try {
      res = await r.json();
    } catch (_) {
      stopAnalyzing();
      toast('Server javobi noto‘g‘ri', 'red');
      return;
    }
    if (!r.ok && (r.status === 401 || r.status === 403)) {
      stopAnalyzing();
      window.location.href = '/login';
      return;
    }
    if (r.status === 409 || res.busy === true) {
      toast(res.message || 'Boshqa tahlil davom etmoqda — natija kutilmoqda.', 'blue');
      pollResult();
      return;
    }
    if (r.status === 429) {
      stopAnalyzing();
      toast(res.message || 'So‘rovlar juda tez. Biroz kuting.', 'blue');
      return;
    }
    if (r.status === 503) {
      stopAnalyzing();
      toast(res.message || 'ZiyrakAi hozircha mavjud emas (sozlash kerak).', 'blue');
      return;
    }
    if (!res.success) { stopAnalyzing(); toast(res.message || `HTTP ${r.status}`, 'red'); return; }
    if (res.warnings && res.warnings.length)
      toast("Ogohlantirish: " + res.warnings.slice(0, 2).join("; "), 'gray');
    toast(res.message || 'Tahlil boshlandi', 'green');
    pollResult();
  } catch(e) { stopAnalyzing(); toast('Server xatosi', 'red'); }
}

async function analyzeCamera() {
  if (!cameraRunning) { toast('Kamera yoqilmagan!', 'red'); return; }
  startAnalyzing(false);
  const prompt = document.getElementById('customPrompt').value.trim() || null;
  const res = await api(apiPath('/api/analyze'), 'POST', {
    source: 'camera',
    lab_type: currentLab,
    prompt,
    microscope: getMicroscopePayload(),
  });
  if (res._httpStatus === 409 || res.busy === true) {
    toast(res.message || 'Boshqa tahlil davom etmoqda — natija kutilmoqda.', 'blue');
    pollResult();
    return;
  }
  if (res._httpStatus === 429) {
    stopAnalyzing();
    toast(res.message || 'So‘rovlar juda tez. Biroz kuting.', 'blue');
    return;
  }
  if (res._httpStatus === 503) {
    stopAnalyzing();
    toast(res.message || 'ZiyrakAi hozircha mavjud emas (sozlash kerak).', 'blue');
    return;
  }
  if (!res.success) { stopAnalyzing(); toast(res.message || 'Xato', 'red'); return; }
  pollResult();
}

function startAnalyzing(isVideo) {
  document.getElementById('analyzeFileBtn').disabled = true;
  document.getElementById('analyzeCamBtn').disabled  = true;
  document.getElementById('analyzeOv1').classList.remove('hidden');
  document.getElementById('analyzeOv2').classList.remove('hidden');
  document.getElementById('analyzeStatus').textContent = isVideo
    ? '🎬 Video kadrlari tahlil qilinmoqda...'
    : '✨ ZiyrakAi tahlil qilmoqda...';
  showLoading();
}

function stopAnalyzing() {
  document.getElementById('analyzeFileBtn').disabled = uploadedFiles.length === 0;
  document.getElementById('analyzeCamBtn').disabled  = !cameraRunning;
  document.getElementById('analyzeOv1').classList.add('hidden');
  document.getElementById('analyzeOv2').classList.add('hidden');
  document.getElementById('analyzeStatus').textContent = '';
}

function pollResult() {
  let tries = 0;
  let lastStatus = '';
  const MAX_TRIES = 300; // 5 daqiqa (1000ms interval)

  const t = setInterval(async () => {
    tries++;
    try {
      const data = await api(apiPath('/api/analysis_result'));
      if (!data) return;

      // Progress xabari
      if (data.status !== lastStatus) {
        lastStatus = data.status;
        const statusMap = {
          'tahlil_qilinmoqda':       '⏳ ZiyrakAi tahlil qilmoqda...',
          'video_tahlil_qilinmoqda': '🎬 Video kadrlar tahlil qilinmoqda...',
        };
        const st = document.getElementById('analyzeStatus');
        if (st) st.textContent = statusMap[data.status] || '';
      }

      if (data.status === 'tayyor' || data.status === 'xato') {
        clearInterval(t);
        stopAnalyzing();
        const st = document.getElementById('analyzeStatus');
        if (st) {
          st.textContent = data.status === 'tayyor' ? '✅ Tahlil tayyor' : '❌ Xato yuz berdi';
          setTimeout(() => { st.textContent = ''; }, 5000);
        }
        renderResult(data);
      }
    } catch(e) {
      // tarmoq xatosi — davom etamiz
    }

    if (tries >= MAX_TRIES) {
      clearInterval(t);
      stopAnalyzing();
      toast('Vaqt tugadi — server javobi kelmadi', 'red');
    }
  }, 1000);
}

// ─── Natijani ko'rsatish ─────────────────────────────────────────────────────
function showLoading() {
  document.getElementById('resultBody').innerHTML = `
    <div class="result-loading">
      <div class="spinner" style="width:24px;height:24px;border-width:2px"></div>
      ZiyrakAi tahlil qilmoqda...
    </div>`;
  document.getElementById('resultTs').textContent = '';
}

function renderResult(data) {
  const body     = document.getElementById('resultBody');
  const ts       = document.getElementById('resultTs');
  const pdfBtn   = document.getElementById('pdfBtn');
  const printBtn = document.getElementById('printBtn');

  if (data.status === 'xato') {
    body.innerHTML = `<div class="r-error r-anim">⚠ ${esc(data.text)}</div>`;
    ts.textContent = data.timestamp || '';
    if (pdfBtn)   pdfBtn.disabled   = true;
    if (printBtn) printBtn.disabled = true;
    return;
  }
  if (!data.text) { body.innerHTML = '<div class="r-normal">Natija bo\'sh</div>'; return; }

  ts.textContent = data.timestamp ? `🕐 ${data.timestamp}` : '';
  body.setAttribute('data-raw-text', data.text || '');
  body.innerHTML = markdownToHtml(data.text);

  // Natija tayyor — PDF va Print tugmalarini yoq
  if (pdfBtn)   pdfBtn.disabled   = false;
  if (printBtn) printBtn.disabled = false;
}

function isMdTableSeparator(line) {
  const t = line.trim();
  if (!t.includes('|')) return false;
  const inner = t.replace(/^\|/, '').replace(/\|\s*$/, '');
  const parts = inner.split('|');
  if (!parts.length) return false;
  return parts.every(p => /^[\s\-:]+$/.test(p));
}

function isMdTableRow(line) {
  const t = line.trim();
  if (!t.includes('|')) return false;
  if (isMdTableSeparator(t)) return false;
  const parts = t.split('|').filter(x => x.trim() !== '');
  return parts.length >= 2;
}

function splitMdTableRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map(c => c.trim());
}

function renderMdTable(rowLines) {
  if (!rowLines.length) return '';
  const rows = rowLines.map(splitMdTableRow);
  const colCount = Math.max(...rows.map(r => r.length), 1);
  let html = '<div class="r-table-wrap"><table class="r-table"><thead><tr>';
  for (let c = 0; c < colCount; c++) {
    const cell = rows[0][c] != null ? rows[0][c] : '';
    html += `<th>${inlineFormat(cell)}</th>`;
  }
  html += '</tr></thead><tbody>';
  for (let r = 1; r < rows.length; r++) {
    html += '<tr>';
    for (let c = 0; c < colCount; c++) {
      const cell = rows[r][c] != null ? rows[r][c] : '';
      html += `<td>${inlineFormat(cell)}</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table></div>';
  return html;
}

function inlineFormat(s) {
  if (s == null) s = '';
  s = esc(String(s));
  s = s.replace(/\*\*([\s\S]+?)\*\*/g, '<strong class="r-strong">$1</strong>');
  s = s.replace(/\*\*/g, '');
  s = s.replace(/\*+/g, '');
  s = s.replace(/`([^`]+?)`/g, '<code class="r-code">$1</code>');
  return s;
}

function markdownToHtml(text) {
  const lines = (text || '').split('\n');
  let html = '';
  let i = 0;
  while (i < lines.length) {
    const l = lines[i].trim();

    if (!l) {
      html += '<div class="r-spacer"></div>';
      i++;
      continue;
    }

    if (isMdTableRow(l)) {
      const rowLines = [];
      while (i < lines.length) {
        const cur = lines[i].trim();
        if (isMdTableSeparator(cur)) { i++; continue; }
        if (isMdTableRow(cur)) { rowLines.push(cur); i++; }
        else break;
      }
      html += renderMdTable(rowLines);
      continue;
    }

    if (l.startsWith('#### ')) {
      html += `<div class="r-h3 r-anim">${inlineFormat(l.slice(5))}</div>`;
      i++;
      continue;
    }
    if (l.startsWith('### ')) {
      html += `<div class="r-h2 r-anim">${inlineFormat(l.slice(4))}</div>`;
      i++;
      continue;
    }
    if (l.startsWith('## ')) {
      html += `<div class="r-h1 r-anim">${inlineFormat(l.slice(3))}</div>`;
      i++;
      continue;
    }

    if (l.startsWith('- ') || l.startsWith('• ')) {
      html += `<div class="r-list r-anim">${inlineFormat(l.slice(2))}</div>`;
      i++;
      continue;
    }
    if (l.startsWith('* ') && !l.startsWith('**')) {
      html += `<div class="r-list r-anim">${inlineFormat(l.slice(2))}</div>`;
      i++;
      continue;
    }

    if (/^\d+\.\s/.test(l)) {
      const inner = inlineFormat(l.replace(/^\d+\.\s+/, ''));
      html += `<div class="r-list-num r-anim">${inner}</div>`;
      i++;
      continue;
    }

    if (l === '---' || l === '***' || /^[-*]{3,}$/.test(l)) {
      html += '<hr class="r-hr">';
      i++;
      continue;
    }

    const lLow = l.toLowerCase();
    let cls = 'r-line r-anim';
    if (lLow.includes('patolog') || lLow.includes('anormal') || lLow.includes('buzil') ||
        lLow.includes('giper') || lLow.includes('kamay') || lLow.includes('topildi') ||
        lLow.includes('aniqlandi') || lLow.includes('diqqat')) {
      cls += ' r-warn';
    }
    if (lLow.includes('norma') && !lLow.includes('normadan') && !lLow.includes('normasiz')) {
      cls += ' r-ok';
    }
    html += `<div class="${cls}">${inlineFormat(l)}</div>`;
    i++;
  }
  return html;
}

function clearResult() {
  document.getElementById('resultBody').innerHTML = `
    <div class="result-empty">
      <div class="re-icon">📋</div>
      <p>Tahlil natijasi shu yerda ko'rinadi</p>
      <p class="re-hint">Rasm/video yuklang yoki kamera yoqing,<br>so'ng tahlil tugmasini bosing</p>
    </div>`;
  document.getElementById('resultTs').textContent = '';
  document.getElementById('resultBody').removeAttribute('data-raw-text');
  const pdfBtn   = document.getElementById('pdfBtn');
  const printBtn = document.getElementById('printBtn');
  if (pdfBtn)   pdfBtn.disabled   = true;
  if (printBtn) printBtn.disabled = true;
}

function _fillPrintArea() {
  const lab  = LAB_META[currentLab];
  const now  = new Date();
  const dateStr = now.toLocaleDateString('uz-UZ', { year:'numeric', month:'long', day:'numeric' });
  const timeStr = now.toLocaleTimeString('uz-UZ');
  const isoStr  = now.toISOString().replace('T',' ').slice(0,19);

  const setEl = (id, txt, html) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (html) el.innerHTML = html; else el.textContent = txt;
  };
  setEl('printLabBadge', `${lab.icon} ${lab.name}`);
  setEl('printDate', dateStr);
  setEl('printTime', timeStr);
  setEl('printPageDate', '', `<strong>${dateStr}</strong> &nbsp; ${timeStr}`);
  setEl('printLabLine', `${lab.icon} ${lab.name}`);
  const up = document.getElementById('userPill');
  const rawUser = up && up.textContent ? up.textContent.trim() : '';
  const exec = rawUser.replace(/^👤\s*/, '').trim() || '—';
  setEl('printExecutor', exec);

  // Raw matn — data-raw-text atributidan olish
  const body = document.getElementById('resultBody');
  const rawText = body.getAttribute('data-raw-text') || '';

  let html;
  if (rawText.trim()) {
    html = markdownToHtml(rawText);
  } else {
    // DOM fallback
    const clone = body.cloneNode(true);
    clone.querySelectorAll('.result-empty,.result-loading').forEach(el => el.remove());
    let dhtml = clone.innerHTML;
    dhtml = dhtml.replace(/<div[^>]*class="r-h1[^"]*"[^>]*>([\s\S]*?)<\/div>/g,   '<h1>$1</h1>');
    dhtml = dhtml.replace(/<div[^>]*class="r-h2[^"]*"[^>]*>([\s\S]*?)<\/div>/g,   '<h2>$1</h2>');
    dhtml = dhtml.replace(/<div[^>]*class="r-h3[^"]*"[^>]*>([\s\S]*?)<\/div>/g,   '<h3>$1</h3>');
    dhtml = dhtml.replace(/<div[^>]*class="r-list[^"]*"[^>]*>([\s\S]*?)<\/div>/g, '<li>$1</li>');
    dhtml = dhtml.replace(/<div[^>]*class="r-warn[^"]*"[^>]*>([\s\S]*?)<\/div>/g, '<p style="color:#92400e;background:#fef9c3;padding:3px 8px;border-left:3px solid #f59e0b">$1</p>');
    dhtml = dhtml.replace(/<div[^>]*class="r-ok[^"]*"[^>]*>([\s\S]*?)<\/div>/g,   '<p style="color:#14532d;background:#f0fdf4;padding:3px 8px;border-left:3px solid #22c55e">$1</p>');
    dhtml = dhtml.replace(/<div[^>]*class="r-error[^"]*"[^>]*>([\s\S]*?)<\/div>/g,'<p style="color:#991b1b;background:#fef2f2;padding:3px 8px;border-left:3px solid #ef4444">$1</p>');
    dhtml = dhtml.replace(/<div[^>]*class="r-line[^"]*"[^>]*>[\s\S]*?<\/div>/g,   '<hr style="border:none;border-top:1px dashed #ccc;margin:5px 0">');
    dhtml = dhtml.replace(/<div[^>]*class="r-[a-z-]*"[^>]*>([\s\S]*?)<\/div>/g,   '<p>$1</p>');
    html = dhtml;
  }
  document.getElementById('printContent').innerHTML = html || '<p>Natija mavjud emas</p>';

  const pm = document.getElementById('printMicroscopeBlock');
  if (pm) {
    const mh = buildPrintMicroscopeHtml();
    if (mh) {
      pm.style.display = 'block';
      pm.innerHTML = mh;
    } else {
      pm.style.display = 'none';
      pm.innerHTML = '';
    }
  }
}

function printResult() {
  _fillPrintArea();
  const el = document.getElementById('printArea');
  el.style.display = 'block';
  window.print();
  el.style.display = 'none';
}

async function savePDF() {
  _fillPrintArea();
  const el  = document.getElementById('printArea');
  el.style.display = 'block';

  const lab = LAB_META[currentLab];
  const now = new Date();
  const fname = `MedLab_${currentLab}_${now.getFullYear()}${String(now.getMonth()+1).padStart(2,'0')}${String(now.getDate()).padStart(2,'0')}_${String(now.getHours()).padStart(2,'0')}${String(now.getMinutes()).padStart(2,'0')}.pdf`;

  const pdfBtn = document.getElementById('pdfBtn');
  pdfBtn.disabled = true;
  pdfBtn.textContent = '⏳ Saqlanmoqda...';

  try {
    await html2pdf()
      .set({
        margin:       [10, 10, 10, 10],
        filename:     fname,
        image:        { type:'jpeg', quality:0.93 },
        html2canvas:  { scale:1.65, useCORS:true, backgroundColor:'#ffffff', logging:false },
        jsPDF:        { unit:'mm', format:'a4', orientation:'portrait' },
        pagebreak:    { mode:['avoid-all', 'css', 'legacy'] }
      })
      .from(document.getElementById('printArea'))
      .save();
    toast(`📄 "${fname}" saqlandi`, 'green');
  } catch(e) {
    toast('PDF saqlashda xato: ' + e.message, 'red');
  } finally {
    el.style.display = 'none';
    pdfBtn.disabled = false;
    pdfBtn.textContent = '📄 PDF';
  }
}

// ─── Snapshot ────────────────────────────────────────────────────────────────
async function captureSnapshot() {
  const res = await api(apiPath('/api/capture'), 'POST');
  msg('snapMsg', res.message, res.success ? 'green' : 'red');
  if (res.success) setTimeout(() => msg('snapMsg','',''), 4000);
}

// ─── Utils ───────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function msg(id, text, color) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.style.color = { green:'#166534', red:'#b91c1c', gray:'#475569', '':'' }[color] || '#475569';
}

async function api(url, method = 'GET', body = null) {
  const m = (method || 'GET').toUpperCase();
  const opts = {
    method: m,
    headers: { ...csrfHeaders() },
    credentials: typeof apiCredentials === 'function' ? apiCredentials() : 'same-origin',
  };
  if (body != null) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  try {
    const r = await fetch(url, opts);
    const ct = (r.headers.get('content-type') || '').toLowerCase();
    let data;
    if (ct.includes('application/json')) {
      try {
        data = await r.json();
      } catch (_) {
        data = { success: false, message: 'Server javobi JSON emas' };
      }
    } else {
      const t = await r.text();
      data = { success: r.ok, message: t.slice(0, 200) || `HTTP ${r.status}` };
    }
    const stamp = (d) => {
      if (d && typeof d === 'object' && !Array.isArray(d)) d._httpStatus = r.status;
      return d;
    };
    if (r.status === 401 || r.status === 403) {
      const msg = String(data.detail || data.message || '').toLowerCase();
      if (msg.includes('csrf')) {
        return stamp({ success: false, message: "Sahifani yangilang (CSRF). F5 bosing." });
      }
      window.location.href = '/login';
      return stamp({ success: false, message: 'Kirish kerak' });
    }
    if (!r.ok && data.success !== false)
      return stamp({ success: false, message: data.message || data.detail || `HTTP ${r.status}` });
    return stamp(data);
  } catch (e) {
    return { success: false, message: "Server bilan aloqa yo'q", _httpStatus: 0 };
  }
}

function toast(text, color = 'gray') {
  const el = document.getElementById('toast');
  if (!el) return;
  const map = { green: 'toast-ios--green', red: 'toast-ios--red', blue: 'toast-ios--blue', gray: 'toast-ios--gray' };
  const cls = map[color] || map.gray;
  el.className = cls;
  el.style.opacity = '1';
  el.style.transform = 'translateY(0)';
  el.textContent = text;
  clearTimeout(el._t);
  el._t = setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(12px)';
  }, 3500);
}

// ─── Init ────────────────────────────────────────────────────────────────────
async function logoutMedlab() {
  try {
    await api(apiPath('/api/auth/logout'), 'POST', {});
  } catch (_) { /* ignore */ }
  window.location.href = '/login';
}

async function refreshUserPill() {
  const el = document.getElementById('userPill');
  if (!el) return;
  const data = await api(apiPath('/api/auth/me'));
  if (data && data.success && data.user && data.user.username) {
    el.textContent = '👤 ' + data.user.username;
  } else {
    el.textContent = '—';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const vf = document.getElementById('videoFeed');
  if (vf) {
    const p = vf.getAttribute('data-stream-path') || '/video_feed';
    vf.src = apiPath(p);
  }
  refreshUserPill();
  scanCameras();
  selectLab('hematology');
  onMicroChange();
});
