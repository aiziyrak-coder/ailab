/* MedLab AI – Laboratoriya Tahlil Tizimi */
/* apiPath, getCookie, csrfHeaders — auth.js (index.html da oldin yuklanadi) */

let currentLab   = 'histology';
let currentSource = 'upload'; // upload | phone | scope
let uploadedFiles = [];
let cameraRunning = false;
let _browserStream = null;
let _liveMode = '';
let _localCamBase = '';
let _localPumpGen = 0;
let _localBlobUrl = '';
let currentPublicId = '';
let _histPage = 1;
let _histQuery = '';
let _histAutoOpen = false;
let _analyzeBusy = false;
let _scanGen = 0;
let _previewObjectUrl = '';
let _previewIndex = 0;
let _thumbUrlByKey = {};
const MAX_CLIENT_UPLOAD = 48;

const LAB_META = {
  histology: {
    icon:'🧬', name:'Gistologiya natijasi', color:'var(--histo)',
    brief:'H&E to‘qima kesmasi: professor darajasidagi aniq tashxis.',
    checks:['Organ qulfi','Pattern','TASHXIS','Nega shu tashxis','Fakt'],
    uploadMain:'H&E (yoki maxsus bo‘yoq) kesma rasmini yuklang', uploadHint:'Avval 4–10× landshaft, keyin 40× hujayra',
    overlayUpload:'Gistologik kesmani yuklang', overlayPhone:'Kesma kadri', overlayScope:'Gistostol kamerasini yoqing',
    emptyTitle:'Gistologiya kutilmoqda', emptyHint:'H&E kesma. 10× arxitektura, 40× yadro. Boshqa lab turlari o‘chirilgan.',
    analyze:'🔬 Kesmani tahlil qil', loading:'Gistopatolog o‘qimoqda...',
    phoneHint:'Butun kesma landshafti + yaqin hujayra kadri.',
    scopeHint:'4–10× tuzilish, 40× yadro. Immersiya odatda kerak emas.',
    srcUpload:'📎 Kesma rasmi', srcPhone:'📱 Telefon kadr', srcScope:'🔬 Mikroskop',
    ocular:'10×', objective:'10×', microTitle:'Kattalashtirish · H&E',
  },
};

function labMeta(lab) {
  return LAB_META[lab] || LAB_META.histology;
}

const LAB_SPEC = {
  histology:        { dept:'Gistologiya',      stain:'H&E',                  code:'HIST', specimen:'To‘qima kesmasi',       protocol:'H&E kesma',            fields:'4–10× → 40×',   illum:'Yorug‘ maydon', bsl:'BSL-1', hazard:false },
};

function labSpec(lab) {
  return LAB_SPEC[lab] || LAB_SPEC.histology;
}

const DAFTAR_LS = 'medlab_daftar_v2';
const LAB_ID_CODES = {
  histology: 'HIST',
};

let _sampleSeq = 0;

function locApi() {
  return window.MEDLAB_UZ_LOCALITIES || null;
}

function fillViloyatSelect() {
  const sel = document.getElementById('daftarViloyat');
  const api = locApi();
  if (!sel || !api) return;
  const keep = sel.value;
  sel.innerHTML = '';
  api.getTopLevel().forEach((r) => {
    const o = document.createElement('option');
    o.value = r.key;
    o.textContent = r.label;
    sel.appendChild(o);
  });
  sel.value = keep || api.DEFAULT_REGION_KEY;
  if (!sel.value) sel.value = api.DEFAULT_REGION_KEY;
}

function fillLocalitySelect(regionKey, preferredCode) {
  const sel = document.getElementById('daftarLocality');
  const api = locApi();
  if (!sel || !api) return;
  const reg = api.findRegion(regionKey || api.DEFAULT_REGION_KEY);
  sel.innerHTML = '';
  if (!reg || !reg.places) return;
  reg.places.forEach((p) => {
    const o = document.createElement('option');
    o.value = p.code;
    o.textContent = `${p.name} (${p.code})`;
    sel.appendChild(o);
  });
  const want = preferredCode || api.DEFAULT_PLACE_CODE;
  if (want && [...sel.options].some((o) => o.value === want)) sel.value = want;
  else if (sel.options.length) sel.selectedIndex = 0;
}

function daftarGet() {
  const api = locApi();
  const vilEl = document.getElementById('daftarViloyat');
  const locEl = document.getElementById('daftarLocality');
  const cliEl = document.getElementById('daftarClinic');
  const typEl = document.getElementById('daftarType');
  const regionKey = (vilEl && vilEl.value) || (api && api.DEFAULT_REGION_KEY) || 'FAR';
  const region = (api && api.getRegionNum(regionKey)) || '40';
  const locality = String((locEl && locEl.value) || (api && api.DEFAULT_PLACE_CODE) || 'FSH')
    .toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 16) || 'FSH';
  const clinic = String((cliEl && cliEl.value) || '7').replace(/\D/g, '').slice(0, 3) || '7';
  const type = String((typEl && typEl.value) || 'OP').toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3) || 'OP';
  return { regionKey, region, locality, clinic, type };
}

function daftarSave() {
  try { localStorage.setItem(DAFTAR_LS, JSON.stringify(daftarGet())); } catch (_) {}
}

function daftarLoadSettings() {
  fillViloyatSelect();
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(DAFTAR_LS) || '{}'); } catch (_) {}
  const api = locApi();
  const regionKey = saved.regionKey || (api && api.DEFAULT_REGION_KEY) || 'FAR';
  const vil = document.getElementById('daftarViloyat');
  if (vil && regionKey) vil.value = regionKey;
  fillLocalitySelect(vil ? vil.value : regionKey, saved.locality || (api && api.DEFAULT_PLACE_CODE) || 'FSH');
  const clinic = document.getElementById('daftarClinic');
  const type = document.getElementById('daftarType');
  if (clinic) clinic.value = saved.clinic || '7';
  if (type && saved.type) type.value = saved.type;
}

function seqStorageKey() {
  const d = daftarGet();
  const now = new Date();
  const p = (n) => String(n).padStart(2, '0');
  const day = `${String(now.getFullYear()).slice(-2)}${p(now.getMonth() + 1)}${p(now.getDate())}`;
  return `medlab_id_seq_${d.region}_${d.locality}_${d.clinic}_${d.type}_${day}`;
}

function peekSampleSeq() {
  if (_sampleSeq) return _sampleSeq;
  try {
    return (parseInt(localStorage.getItem(seqStorageKey()) || '0', 10) || 0) + 1;
  } catch (_) {
    return 1;
  }
}

function allocateSampleSeq() {
  if (_sampleSeq) return _sampleSeq;
  const n = peekSampleSeq();
  try { localStorage.setItem(seqStorageKey(), String(n)); } catch (_) {}
  _sampleSeq = n;
  return n;
}

function buildRegistrationId(lab, seq) {
  const d = daftarGet();
  const code = LAB_ID_CODES[lab] || labSpec(lab).code || 'HIST';
  const n = String(seq || 1).padStart(4, '0');
  return `${d.region}${d.locality}${d.clinic}${d.type}${code}${n}`;
}

function currentNamunaId() {
  return buildRegistrationId(currentLab, peekSampleSeq());
}

function refreshSampleId() {
  const el = document.getElementById('accSample');
  const next = currentNamunaId();
  if (el) {
    const prev = String(el.value || '');
    el.value = next;
    if (prev && prev !== next) {
      el.classList.remove('is-fresh');
      void el.offsetWidth;
      el.classList.add('is-fresh');
    }
  }
  updateResultRegistrationId();
}

function updateResultRegistrationId() {
  const id = valOf('accSample') || currentNamunaId();
  const row = document.getElementById('resultRegRow');
  const el = document.getElementById('resultRegId');
  if (el) el.textContent = id;
  if (row) row.classList.toggle('hidden', !currentPublicId);
  const badge = document.getElementById('resultPublicId');
  if (badge && currentPublicId) {
    badge.textContent = id;
    badge.classList.remove('hidden');
  }
}

function emptyResultHtml() {
  const m = labMeta(currentLab);
  return `<div class="result-empty">
      <div class="re-icon">${m.icon}</div>
      <p>Natija shu yerda chiqadi</p>
      <p class="re-hint">Chapda bemor ma’lumotini to‘ldiring va rasm yuklang — keyin «Tahlil qil» chiqadi.</p>
    </div>`;
}

function lockLabSelect() {
  const sel = document.getElementById('labSelect');
  if (!sel) return;
  if (sel.tagName === 'SELECT') {
    sel.innerHTML = '<option value="histology" selected>Gistologiya</option>';
    sel.value = 'histology';
    sel.disabled = true;
    sel.onchange = null;
  }
}

function selectLab(lab) {
  lockLabSelect();
  lab = 'histology';
  currentLab = lab;
  const sel = document.getElementById('labSelect');
  if (sel && sel.value !== lab) sel.value = lab;

  const m = LAB_META[lab];
  const s = labSpec(lab);
  const root = document.querySelector('.app-root');
  if (root) {
    root.style.setProperty('--lab-accent', '#564621');
    root.setAttribute('data-lab', lab);
  }
  syncLabChrome(m, s);

  const ico = document.getElementById('resultLabIcon');
  const nm = document.getElementById('resultLabName');
  if (ico) ico.textContent = m.icon;
  if (nm) {
    nm.textContent = m.name;
    nm.style.color = '';
  }

  const oc = document.getElementById('microOcularSel');
  const ob = document.getElementById('microObjSel');
  if (oc && m.ocular) oc.value = m.ocular;
  if (ob && m.objective) ob.value = m.objective;
  onMicroChange();

  const btn = document.getElementById('analyzeBtn');
  if (btn && !_analyzeBusy) btn.textContent = 'Tahlil boshlash';

  if (document.querySelector('#resultBody .result-empty')) {
    document.getElementById('resultBody').innerHTML = emptyResultHtml();
  }

  updateOverlay();
  updateAnalyzeBtn();
  refreshSampleId();
}

function syncLabChrome(m, s) {
  m = m || labMeta(currentLab);
  s = s || labSpec(currentLab);
  const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  setTxt('hudDept', s.dept);
  setTxt('hudStain', s.stain);
  setTxt('slideCode', s.code);
  setTxt('slideName', s.specimen);
  setTxt('slideStain', s.stain);
  setTxt('frostCode', s.code);
  setTxt('protoProtocol', s.protocol);
  setTxt('protoFields', s.fields);
  setTxt('protoIllum', s.illum);
  setTxt('stIllum', s.illum);
  setTxt('wbBsl', s.bsl);
  const frost = document.getElementById('slideFrostCode');
  if (frost) frost.textContent = s.code;
  const haz = document.getElementById('wbHaz');
  if (haz) haz.classList.toggle('hidden', !s.hazard);
  refreshLabPlatform();
}

const PATIENT_FIELDS = [
  'accName', 'accAge', 'accSex', 'accWard', 'accSite',
  'daftarViloyat', 'daftarLocality', 'daftarClinic', 'daftarType',
];

function patientPayload() {
  const d = daftarGet();
  return {
    patient_name: valOf('accName'),
    sample_id: valOf('accSample') || currentNamunaId(),
    age: valOf('accAge'),
    sex: valOf('accSex'),
    ward: valOf('accWard'),
    specimen_site: valOf('accSite'),
    clinical_note: valOf('accClinical'),
    region: d.regionKey || d.region || '',
    locality: d.locality || '',
    clinic: d.clinic || '',
    facility_type: d.type || '',
    priority: _priority || 'routine',
    lab_type: currentLab,
  };
}

function appendPatientToFormData(fd) {
  const p = patientPayload();
  Object.keys(p).forEach(k => {
    if (p[k] != null && p[k] !== '') fd.append(k, String(p[k]));
  });
}

const PATIENT_LS = 'medlab_patients_v1';
let _patientLookupTimer = null;
let _patientAutofillSilent = false;

function _normPatientName(name) {
  return String(name || '').toLowerCase().replace(/[ʻ'`]/g, "'").replace(/\s+/g, ' ').trim();
}

function rememberPatient(payload) {
  const p = payload || patientPayload();
  const key = _normPatientName(p.patient_name);
  if (!key || key.length < 2) return;
  let map = {};
  try { map = JSON.parse(localStorage.getItem(PATIENT_LS) || '{}') || {}; } catch (_) { map = {}; }
  map[key] = {
    patient_name: p.patient_name || '',
    age: p.age || '',
    sex: p.sex || '',
    ward: p.ward || '',
    specimen_site: p.specimen_site || '',
    clinical_note: p.clinical_note || '',
    region: p.region || '',
    locality: p.locality || '',
    clinic: p.clinic || '',
    facility_type: p.facility_type || '',
    lab_type: p.lab_type || currentLab || '',
    saved_at: Date.now(),
  };
  const keys = Object.keys(map);
  if (keys.length > 80) {
    keys.sort((a, b) => (map[a].saved_at || 0) - (map[b].saved_at || 0));
    keys.slice(0, keys.length - 80).forEach(k => delete map[k]);
  }
  try { localStorage.setItem(PATIENT_LS, JSON.stringify(map)); } catch (_) {}
}

function lookupPatientLocal(name) {
  const key = _normPatientName(name);
  if (!key || key.length < 2) return null;
  let map = {};
  try { map = JSON.parse(localStorage.getItem(PATIENT_LS) || '{}') || {}; } catch (_) { return null; }
  if (map[key]) return map[key];
  const hit = Object.keys(map).find(k => k === key || k.startsWith(key) || key.startsWith(k));
  return hit ? map[hit] : null;
}

function mapSpecimenSite(raw) {
  const s = (raw || '').toLowerCase();
  if (!s) return '';
  if (/teri|skin|koja|kozha|kojniy|kozh|epiderm|keratoz|dermis/.test(s)) return 'Teri';
  if (/sut bezi|ko['’]?krak|breast|mamma/.test(s)) return 'Sut bezi';
  if (/qovuq|bladder|urotel/.test(s)) return 'Qovuq';
  if (/prostat/.test(s)) return 'Prostata';
  if (/qalqon|thyroid/.test(s)) return 'Qalqonsimon bez';
  if (/ichak|colon|oshqozon|gi\b/.test(s)) return 'Oshqozon-ichak';
  if (/endometr|bachadon/.test(s)) return 'Endometrium';
  if (/yumurtalik|ovar/.test(s)) return 'Yumurtalik';
  if (/buyrak|kidney|renal/.test(s)) return 'Buyrak';
  if (/o['’]?pka|lung/.test(s)) return "O'pka";
  if (/qon|yoqma|smear/.test(s)) return 'Qon';
  if (/siydik|urine/.test(s)) return 'Siydik';
  return '';
}

function setAccSite(val, soft) {
  const sel = document.getElementById('accSite');
  if (!sel) return;
  const raw = val == null ? '' : String(val).trim();
  if (soft && sel.value) return;
  const mapped = mapSpecimenSite(raw);
  const values = [...sel.options].map(o => o.value);
  if (mapped && values.includes(mapped)) {
    sel.value = mapped;
  } else if (raw && values.includes(raw)) {
    sel.value = raw;
  } else if (raw) {
    let extra = sel.querySelector('option[data-custom="1"]');
    if (!extra) {
      extra = document.createElement('option');
      extra.dataset.custom = '1';
      sel.appendChild(extra);
    }
    extra.value = raw;
    extra.textContent = raw;
    sel.value = raw;
  } else {
    sel.value = '';
  }
  sel.classList.remove('missing');
}

function applyPatientFields(p, opts) {
  if (!p) return false;
  const soft = !!(opts && opts.soft);
  _patientAutofillSilent = true;
  const set = (id, val) => {
    const el = document.getElementById(id);
    if (!el) return;
    const v = val == null ? '' : String(val);
    if (soft && valOf(id)) return;
    el.value = v;
    el.classList.remove('missing');
  };
  set('accName', p.patient_name);
  set('accAge', p.age);
  set('accSex', p.sex);
  set('accWard', p.ward);
  setAccSite(p.specimen_site, soft);
  set('accClinical', p.clinical_note);
  if (p.region || p.locality || p.clinic || p.facility_type) {
    const vil = document.getElementById('daftarViloyat');
    if (vil && p.region && (!soft || !vil.value)) {
      vil.value = p.region;
      fillLocalitySelect(p.region);
    }
    const loc = document.getElementById('daftarLocality');
    if (loc && p.locality && (!soft || !loc.value)) loc.value = p.locality;
    const cli = document.getElementById('daftarClinic');
    if (cli && p.clinic && (!soft || !cli.value)) cli.value = p.clinic;
    const typ = document.getElementById('daftarType');
    if (typ && p.facility_type && (!soft || !typ.value)) typ.value = p.facility_type;
    daftarSave();
  }
  if (p.lab_type && LAB_META[p.lab_type]) selectLab(p.lab_type);
  checkSexNameHint();
  updateAnalyzeBtn();
  refreshLabPlatform();
  _patientAutofillSilent = false;
  return true;
}

function clearPatientFields() {
  ['accName', 'accAge', 'accSex', 'accWard', 'accSite', 'accClinical'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.value = '';
      el.classList.remove('missing');
    }
  });
  checkSexNameHint();
}

async function tryAutofillPatient(name, opts) {
  const n = String(name || valOf('accName') || '').trim();
  if (n.length < 2) return false;
  const local = lookupPatientLocal(n);
  if (local && applyPatientFields(local, opts)) {
    if (!(opts && opts.silent)) toast('Bemor kartasi topildi (saqlangan)', 'blue');
    return true;
  }
  const data = await api(apiPath('/api/patients/lookup?q=' + encodeURIComponent(n)));
  if (data && data.success && data.found && data.patient) {
    rememberPatient(data.patient);
    applyPatientFields(data.patient, opts);
    if (!(opts && opts.silent)) toast('Bemor kartasi avtomatik to‘ldirildi', 'green');
    return true;
  }
  return false;
}

function schedulePatientLookup() {
  if (_patientAutofillSilent) return;
  clearTimeout(_patientLookupTimer);
  _patientLookupTimer = setTimeout(() => {
    const name = valOf('accName');
    if (name.length < 3) return;
    // Faqat bo'sh maydonlarni to'ldirish — foydalanuvchi yozganini buzmasin
    tryAutofillPatient(name, { soft: true, silent: true });
  }, 450);
}

function ensureSampleNo() {
  allocateSampleSeq();
  refreshSampleId();
}

function patientFieldsComplete() {
  return PATIENT_FIELDS.every(id => valOf(id));
}

function markPatientFields(highlight) {
  PATIENT_FIELDS.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.toggle('missing', !!(highlight && !valOf(id)));
  });
}

function isCsrfFail(data) {
  const msg = String((data && (data.detail || data.message)) || '').toLowerCase();
  return msg.includes('csrf');
}

function kickIfLoggedOut(status, data) {
  if (status !== 401 && status !== 403) return false;
  if (isCsrfFail(data)) return false;
  window.location.href = '/login';
  return true;
}

function refreshConnPill() {
  const pill = document.getElementById('connPill');
  if (!pill) return;
  pill.classList.remove('pill-connected', 'pill-file', 'pill-warn');
  if (cameraRunning) {
    const label = (_liveMode === 'browser' || currentSource === 'phone')
      ? (currentSource === 'scope' ? '● Mikroskop ulangan' : '● Telefon ulangan')
      : (currentSource === 'scope' ? '● Mikroskop ulangan' : '● Telefon ulangan');
    pill.textContent = currentSource === 'scope' ? '● Mikroskop ulangan' : '● Telefon ulangan';
    pill.classList.add('pill-connected');
    return;
  }
  if (currentSource === 'upload') {
    pill.textContent = uploadedFiles.length ? '● Fayl yuklangan' : '● Fayl rejimi';
    pill.classList.add('pill-file');
    return;
  }
  pill.textContent = currentSource === 'scope' ? '○ Mikroskop kutilmoqda' : '○ Telefon kutilmoqda';
}

function jumpToStep(step) {
  const n = Number(step);
  if (n === 0) {
    document.querySelector('.left-panel .card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    focusFirstMissingPatient() || document.getElementById('accName')?.focus();
  } else if (n === 1) {
    document.querySelector('.capture-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (currentSource === 'upload') document.getElementById('uploadZone')?.focus();
  } else if (n === 2) {
    document.getElementById('analyzeCard')?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    if (!_analyzeBusy) document.getElementById('analyzeBtn')?.focus();
  } else if (n === 3) {
    document.querySelector('.right-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function focusFirstMissingPatient() {
  for (const id of PATIENT_FIELDS) {
    if (!valOf(id)) {
      const el = document.getElementById(id);
      if (!el) continue;
      const details = el.closest('details');
      if (details) details.open = true;
      el.focus();
      el.classList.add('missing');
      return true;
    }
  }
  return false;
}

const _MALE_NAME_HINTS = (
  'bek|jon|ali|umid|islom|sardor|javohir|odil|aziz|bobur|temur|shox|shah|akmal|dilshod|farhod|jamshid|karim|nodir|rustam|said|tolib|ulug'
).split('|');
const _FEMALE_NAME_HINTS = (
  'oy|gul|xon|begim|nilufar|dilnoza|madina|zarina|malika|sevinch|shahnoza|feruza|munisa|laziza|sabina|diyora|asila'
).split('|');

function checkSexNameHint() {
  const hint = document.getElementById('sexNameHint');
  if (!hint) return;
  const name = valOf('accName').toLowerCase().replace(/ʻ/g, "'");
  const sex = valOf('accSex');
  if (!name || !sex) {
    hint.classList.add('hidden');
    hint.textContent = '';
    return;
  }
  const first = name.split(/\s+/)[0] || '';
  const maleish = _MALE_NAME_HINTS.some(h => first.includes(h));
  const femaleish = _FEMALE_NAME_HINTS.some(h => first.includes(h));
  if (sex === 'Ayol' && maleish && !femaleish) {
    hint.textContent = 'Ehtimol: ism erkakka o‘xshaydi, jins «Ayol» tanlangan — tekshiring.';
    hint.classList.remove('hidden');
  } else if (sex === 'Erkak' && femaleish && !maleish) {
    hint.textContent = 'Ehtimol: ism ayolga o‘xshaydi, jins «Erkak» tanlangan — tekshiring.';
    hint.classList.remove('hidden');
  } else {
    hint.classList.add('hidden');
    hint.textContent = '';
  }
}

let _analyzeTimer = null;
let _analyzeStartedAt = 0;

function startAnalyzeTimer() {
  stopAnalyzeTimer();
  _analyzeStartedAt = Date.now();
  const tick = () => {
    const el = document.getElementById('azElapsed');
    if (!el || !_analyzeBusy) return;
    const sec = Math.floor((Date.now() - _analyzeStartedAt) / 1000);
    const m = Math.floor(sec / 60);
    const s = String(sec % 60).padStart(2, '0');
    el.textContent = m > 0 ? `Vaqt: ${m}:${s}` : `Vaqt: ${sec} s`;
    if (sec >= 45) {
      const hint = document.getElementById('azHint');
      if (hint) hint.textContent = 'Chuqur tahlil davom etmoqda — 1–2 daqiqa normal';
    }
  };
  tick();
  _analyzeTimer = setInterval(tick, 1000);
}

function stopAnalyzeTimer() {
  if (_analyzeTimer) {
    clearInterval(_analyzeTimer);
    _analyzeTimer = null;
  }
  const el = document.getElementById('azElapsed');
  if (el) el.textContent = '';
  const hint = document.getElementById('azHint');
  if (hint) hint.textContent = 'Preparat o‘qilmoqda — biroz kuting';
}

function scrollResultIntoView() {
  const panel = document.querySelector('.right-panel') || document.getElementById('resultBody');
  if (!panel) return;
  requestAnimationFrame(() => {
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

function updateAnalyzeBtn() {
  const btn = document.getElementById('analyzeBtn');
  const cancelBtn = document.getElementById('cancelAnalysisBtn');
  const newBtns = document.querySelectorAll('.js-new-analysis');
  const readyPatient = patientFieldsComplete();
  const hasSample = uploadedFiles.length > 0 || cameraRunning;
  if (cancelBtn) cancelBtn.classList.toggle('hidden', !_analyzeBusy);
  newBtns.forEach((el) => {
    el.disabled = !!_analyzeBusy;
    el.classList.toggle('is-ready', !!_hasResult && !_analyzeBusy);
  });
  const nextBar = document.getElementById('resultNextBar');
  if (nextBar) nextBar.classList.toggle('has-result', !!_hasResult);
  refreshConnPill();
  if (!btn) return;
  if (_analyzeBusy) {
    btn.disabled = true;
    btn.textContent = 'Tahlil qilinmoqda…';
    btn.title = 'Tahlil davom etmoqda — bekor qilish mumkin';
    return;
  }
  btn.disabled = !readyPatient || !hasSample;
  if (_hasResult && hasSample) {
    btn.textContent = 'Qayta tahlil';
    btn.title = 'Shu rasm bilan qayta tahlil qilish';
  } else {
    btn.textContent = 'Tahlil boshlash';
    btn.title = !readyPatient
      ? 'Avval bemor ma’lumotini to‘ldiring'
      : (!hasSample ? 'Avval rasm yuklang yoki mikroskopni yoqing' : 'Tahlilni boshlash');
  }
}

function cancelAnalysis() {
  _pollGen++;
  _analyzeBusy = false;
  stopAnalyzeTimer();
  const ov = document.getElementById('analyzeOv');
  if (ov) ov.classList.add('hidden');
  const st = document.getElementById('analyzeStatus');
  if (st) st.textContent = '';
  updateAnalyzeBtn();
  refreshLabPlatform();
  if (!_hasResult) {
    const body = document.getElementById('resultBody');
    if (body && body.querySelector('.result-loading')) {
      body.innerHTML = emptyResultHtml();
    }
  }
  toast('Tahlil bekor qilindi — yangi tahlil boshlashingiz mumkin', 'blue');
}

function startNewAnalysis() {
  _pollGen++;
  _analyzeBusy = false;
  stopAnalyzeTimer();
  const ov = document.getElementById('analyzeOv');
  if (ov) ov.classList.add('hidden');
  clearResult();
  clearPatientFields();
  // Yuklangan rasm/video ro'yxati ham tozalanadi — «yangi tahlil» bo'sh boshlanadi
  clearFile();
  const headerSearch = document.getElementById('headerIdSearch');
  if (headerSearch) headerSearch.value = '';
  const analyzeStatus = document.getElementById('analyzeStatus');
  if (analyzeStatus) analyzeStatus.textContent = '';
  const hint = document.getElementById('sexNameHint');
  if (hint) { hint.textContent = ''; hint.classList.add('hidden'); }
  markPatientFields(false);
  // Yangi namuna raqami (oldingi natija bilan aralashmasin)
  _sampleSeq = 0;
  allocateSampleSeq();
  refreshSampleId();
  setPriority('routine');
  updateAnalyzeBtn();
  refreshLabPlatform();
  document.querySelector('.left-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  const name = document.getElementById('accName');
  if (name) {
    name.focus();
  }
  toast('Yangi tahlil: F.I.Sh. yozing — oldingi bemor avtomatik to‘ldiriladi', 'green');
}

let _priority = 'routine';
let _fieldCount = 0;
let _validated = false;
let _hasResult = false;

function valOf(id) {
  const el = document.getElementById(id);
  return el ? String(el.value || '').trim() : '';
}

function setPriority(p) {
  _priority = p === 'stat' ? 'stat' : 'routine';
  document.querySelectorAll('.prio').forEach(b => {
    b.classList.toggle('on', b.getAttribute('data-prio') === _priority);
  });
}

function addMicField() {
  _fieldCount += 1;
  const n = document.getElementById('fieldN');
  if (n) n.textContent = String(_fieldCount);
  toast('Maydon ' + _fieldCount + ' qayd qilindi', 'blue');
}

function validateReport() {
  if (!_hasResult) { toast('Avval tahlil natijasi bo‘lsin', 'red'); return; }
  _validated = true;
  const btn = document.getElementById('validateBtn');
  if (btn) {
    btn.classList.add('is-ok');
    btn.textContent = 'Tasdiqlangan';
    btn.disabled = true;
  }
  refreshLabPlatform();
  toast('Laborant tasdiqladi', 'green');
}

function tickLabClock() {
  const now = new Date();
  const p = n => String(n).padStart(2, '0');
  const t = `${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`;
  const d = now.toLocaleDateString('uz-UZ', { day: '2-digit', month: 'short', year: 'numeric' });
  const timeEl = document.getElementById('wbTime');
  const dateEl = document.getElementById('wbDate');
  const ft = document.getElementById('ftClock');
  if (timeEl) timeEl.textContent = t;
  if (dateEl) dateEl.textContent = d;
  if (ft) ft.textContent = t;
}

function refreshLabPlatform() {
  let step = 0;
  if (valOf('accName') || valOf('accSample')) step = 0;
  if (uploadedFiles.length || cameraRunning) step = 1;
  if (_analyzeBusy) step = 2;
  if (_hasResult || _validated) step = 3;
  document.querySelectorAll('#wbFlow li').forEach(li => {
    const n = Number(li.getAttribute('data-step'));
    li.classList.toggle('on', n === step);
    li.classList.toggle('done', n < step);
  });
  let inst = 'Tayyor';
  let instCls = '';
  if (_analyzeBusy) { inst = 'Tahlil'; instCls = 'busy'; }
  else if (cameraRunning) { inst = 'Jonli'; instCls = 'live'; }
  const wbInst = document.getElementById('wbInst');
  if (wbInst) {
    wbInst.textContent = inst;
    wbInst.classList.remove('live', 'busy');
    if (instCls) wbInst.classList.add(instCls);
  }
  const stLive = document.getElementById('stLive');
  if (stLive) {
    stLive.textContent = cameraRunning ? 'Jonli' : 'Tayyor';
    stLive.classList.toggle('on', cameraRunning);
  }
  const oil = document.getElementById('stOil');
  if (oil) {
    const obj = getObjectiveStr();
    oil.classList.toggle('hidden', !/100/.test(obj));
  }
  const cs = document.getElementById('caseStatus');
  if (cs) {
    cs.classList.remove('ready', 'ok', 'busy');
    if (_validated) { cs.textContent = 'TASDIQLANGAN'; cs.classList.add('ok'); }
    else if (_hasResult) { cs.textContent = 'TAYYOR'; cs.classList.add('ready'); }
    else if (_analyzeBusy) { cs.textContent = 'TAHLILDA'; cs.classList.add('busy'); }
    else if (_priority === 'stat') { cs.textContent = 'STAT'; cs.classList.add('busy'); }
    else cs.textContent = 'KUTILMOQDA';
  }
}

function parseMagNum(s) {
  if (!s) return NaN;
  const m = String(s).match(/[\d.]+/);
  return m ? parseFloat(m[0]) : NaN;
}

function getOcularStr() {
  return (document.getElementById('microOcularSel')?.value || '').trim();
}

function getObjectiveStr() {
  return (document.getElementById('microObjSel')?.value || '').trim();
}

function computeMicroscopeTotalDisplay() {
  const no = parseMagNum(getOcularStr());
  const nb = parseMagNum(getObjectiveStr());
  if (!isNaN(no) && !isNaN(nb) && no > 0 && nb > 0) return Math.round(no * nb) + '×';
  return '';
}

function getMicroscopePayload() {
  const ocular = getOcularStr();
  const objective = getObjectiveStr();
  const badge = document.getElementById('microTotalBadge');
  let total_label = '';
  if (badge && badge.textContent && badge.textContent !== '—') total_label = badge.textContent.trim();
  return { ocular, objective, total_label };
}

function appendMicroscopeToFormData(fd) {
  const m = getMicroscopePayload();
  fd.append('micro_ocular', m.ocular);
  fd.append('micro_objective', m.objective);
  fd.append('micro_total_label', m.total_label);
}

function onMicroChange() {
  const bStr = getObjectiveStr();
  const badge = document.getElementById('microTotalBadge');
  const totalStr = computeMicroscopeTotalDisplay();
  if (badge) badge.textContent = totalStr || '—';
  const hudMag = document.getElementById('hudMag');
  if (hudMag) hudMag.textContent = totalStr || '—';
  const slideMag = document.getElementById('slideMag');
  if (slideMag) slideMag.textContent = totalStr || '—';

  const objN = parseMagNum(bStr);
  document.querySelectorAll('#objTurret [data-obj]').forEach(el => {
    el.classList.toggle('on', Number(el.getAttribute('data-obj')) === objN);
  });
  const oil = document.getElementById('stOil');
  if (oil) oil.classList.toggle('hidden', !/100/.test(bStr || ''));
}

function buildPrintMicroscopeHtml() {
  const m = getMicroscopePayload();
  if (!m.ocular && !m.objective && !m.total_label) return '';
  let h = '<strong>🔬 MIKROSKOP</strong><br>';
  if (m.ocular)      h += `Okulyar: ${esc(m.ocular)}<br>`;
  if (m.objective)   h += `Obyektiv: ${esc(m.objective)}<br>`;
  if (m.total_label) h += `Umumiy kattalashtirish: <strong>${esc(m.total_label)}</strong><br>`;
  return h;
}

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
  let added = 0;
  let skippedLimit = 0;
  for (const f of files) {
    if (uploadedFiles.find(x => x.name === f.name && x.size === f.size)) continue;
    if (uploadedFiles.length >= MAX_CLIENT_UPLOAD) {
      skippedLimit++;
      continue;
    }
    uploadedFiles.push(f);
    added++;
  }
  renderFileList();
  syncUploadPane();
  updateAnalyzeBtn();
  if (_previewIndex >= uploadedFiles.length) _previewIndex = 0;
  renderMediaThumbs();
  if (uploadedFiles.length) showMainPreview(_previewIndex);
  setSource('upload');
  if (skippedLimit) {
    toast(`Maksimum ${MAX_CLIENT_UPLOAD} ta fayl. ${skippedLimit} tasi qo‘shilmadi.`, 'red');
  } else if (added) {
    toast(`${added} ta fayl qo‘shildi`, 'green');
  } else {
    toast('Bu fayllar allaqachon qo‘shilgan', 'gray');
  }
}

function syncUploadPane() {
  const zone = document.getElementById('uploadZone');
  const prev = document.getElementById('filePreview');
  const pane = document.getElementById('paneUpload');
  const main = document.getElementById('uploadMain');
  const hint = document.querySelector('#uploadZone .upload-hint-line');
  const n = uploadedFiles.length;
  if (zone) {
    zone.style.display = '';
    zone.classList.toggle('is-compact', n > 0);
  }
  if (main) main.textContent = n ? 'Yana rasm qo‘shish' : 'Rasm yuklang';
  if (hint) hint.style.display = n ? 'none' : '';
  if (prev) prev.style.display = n ? '' : 'none';
  if (pane) pane.classList.toggle('has-files', n > 0);
}

function renderFileList() {
  const list = document.getElementById('fileList');
  const cnt  = document.getElementById('previewCount');
  if (cnt) cnt.textContent = uploadedFiles.length ? `${uploadedFiles.length} ta fayl` : '';
  if (!list) return;
  list.innerHTML  = '';
  uploadedFiles.forEach((f, i) => {
    const isVid = isVideoFile(f);
    const div   = document.createElement('div');
    div.className = 'file-item' + (i === _previewIndex ? ' is-on' : '');
    div.setAttribute('data-idx', String(i));
    div.innerHTML = `
      <span class="file-item-ico">${isVid ? '🎬' : '🖼'}</span>
      <span class="file-item-name" title="${esc(f.name)}">${esc(f.name)}</span>
      <span class="file-item-size">${fmtSize(f.size)}</span>
      <button type="button" class="file-item-del" data-idx="${i}" title="O'chirish">✕</button>
    `;
    list.appendChild(div);
  });
}

function bindUploadFileActions() {
  const pane = document.getElementById('paneUpload');
  if (!pane || pane.dataset.boundFiles === '1') return;
  pane.dataset.boundFiles = '1';
  pane.addEventListener('click', (e) => {
    const clearBtn = e.target.closest('[data-clear-files]');
    if (clearBtn) {
      e.preventDefault();
      e.stopPropagation();
      clearFile();
      return;
    }
    const del = e.target.closest('.file-item-del');
    if (del) {
      e.preventDefault();
      e.stopPropagation();
      const idx = Number(del.getAttribute('data-idx'));
      if (Number.isFinite(idx)) removeFile(idx);
      return;
    }
    const item = e.target.closest('.file-item');
    if (item) {
      const idx = Number(item.getAttribute('data-idx'));
      if (Number.isFinite(idx)) showMainPreview(idx);
    }
  });
}

function removeFile(idx) {
  uploadedFiles.splice(idx, 1);
  if (uploadedFiles.length === 0) {
    clearFile();
  } else {
    if (_previewIndex > idx) _previewIndex -= 1;
    if (_previewIndex >= uploadedFiles.length) _previewIndex = uploadedFiles.length - 1;
    renderFileList();
    renderMediaThumbs();
    showMainPreview(_previewIndex);
    updateAnalyzeBtn();
    refreshLabPlatform();
  }
}

function _fileKey(f) {
  return `${f.name}|${f.size}|${f.lastModified || 0}`;
}

function _blobUrlForFile(f) {
  const k = _fileKey(f);
  if (!_thumbUrlByKey[k]) _thumbUrlByKey[k] = URL.createObjectURL(f);
  return _thumbUrlByKey[k];
}

function _revokeUnusedBlobUrls() {
  const keep = new Set(uploadedFiles.map(_fileKey));
  Object.keys(_thumbUrlByKey).forEach(k => {
    if (keep.has(k)) return;
    try { URL.revokeObjectURL(_thumbUrlByKey[k]); } catch (_) {}
    delete _thumbUrlByKey[k];
  });
}

function _revokePreviewUrl() {
  if (_previewObjectUrl) {
    try { URL.revokeObjectURL(_previewObjectUrl); } catch (_) {}
    _previewObjectUrl = '';
  }
}

function _revokeThumbUrls() {
  _revokeUnusedBlobUrls();
}

function renderMediaThumbs() {
  const strip = document.getElementById('mediaThumbs');
  if (!strip) return;
  strip.innerHTML = '';
  _revokeUnusedBlobUrls();
  const show = uploadedFiles.length > 0 && currentSource === 'upload' && !cameraRunning;
  strip.classList.toggle('hidden', !show);
  if (!show) return;
  uploadedFiles.forEach((f, i) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'media-thumb' + (i === _previewIndex ? ' on' : '');
    btn.title = f.name;
    const url = _blobUrlForFile(f);
    if (isVideoFile(f)) {
      const v = document.createElement('video');
      v.src = url;
      v.muted = true;
      v.preload = 'metadata';
      btn.appendChild(v);
    } else {
      const img = document.createElement('img');
      img.src = url;
      img.alt = f.name;
      btn.appendChild(img);
    }
    const n = document.createElement('span');
    n.className = 'media-thumb-n';
    n.textContent = String(i + 1);
    btn.appendChild(n);
    btn.addEventListener('click', () => showMainPreview(i));
    strip.appendChild(btn);
  });
}

function showMainPreview(fileOrIndex) {
  const content = document.getElementById('uploadedContent');
  if (!content || !uploadedFiles.length) return;
  let idx = typeof fileOrIndex === 'number' ? fileOrIndex : uploadedFiles.indexOf(fileOrIndex);
  if (idx < 0) idx = 0;
  if (idx >= uploadedFiles.length) idx = uploadedFiles.length - 1;
  _previewIndex = idx;
  const file = uploadedFiles[idx];
  if (!file) return;
  const url = _blobUrlForFile(file);
  const isVid = isVideoFile(file);
  content.innerHTML = '';
  if (isVid) {
    const vid = document.createElement('video');
    vid.src = url;
    vid.controls = true;
    vid.preload = 'metadata';
    content.appendChild(vid);
  } else {
    const img = document.createElement('img');
    img.src = url;
    img.alt = file.name || 'Yuklangan rasm';
    content.appendChild(img);
  }
  document.querySelectorAll('.media-thumb').forEach((b, i) => b.classList.toggle('on', i === _previewIndex));
  document.querySelectorAll('.file-item').forEach((el, i) => el.classList.toggle('is-on', i === _previewIndex));
  const badge = document.getElementById('mediaCount');
  if (badge) {
    badge.textContent = uploadedFiles.length > 1 ? `${_previewIndex + 1} / ${uploadedFiles.length}` : '';
    badge.classList.toggle('hidden', uploadedFiles.length < 2);
  }
  showLivePreview(false);
}

function clearFile() {
  uploadedFiles = [];
  _previewIndex = 0;
  _revokePreviewUrl();
  _revokeThumbUrls();
  const inp = document.getElementById('fileInput');
  if (inp) inp.value = '';
  renderFileList();
  syncUploadPane();
  updateAnalyzeBtn();
  const content = document.getElementById('uploadedContent');
  if (content) {
    content.innerHTML = '';
    if (!cameraRunning) content.style.display = 'none';
  }
  const thumbs = document.getElementById('mediaThumbs');
  if (thumbs) { thumbs.innerHTML = ''; thumbs.classList.add('hidden'); }
  const badge = document.getElementById('mediaCount');
  if (badge) { badge.textContent = ''; badge.classList.add('hidden'); }
  if (!cameraRunning) updateOverlay();
  refreshLabPlatform();
}

function _camOptionValue(c) {
  if (c && c.localPort) return 'l:' + c.localPort + ':' + String(c.index);
  if (c && c.deviceId) return 'b:' + c.deviceId;
  return 's:' + String(c && c.index != null ? c.index : '');
}

function fillSelect(sel, items, placeholder) {
  sel.innerHTML = `<option value="">${placeholder}</option>`;
  items.forEach(c => {
    const opt = document.createElement('option');
    opt.value = _camOptionValue(c);
    opt.textContent = c.name || ('Kamera ' + c.index);
    sel.appendChild(opt);
  });
  const prefer = items.findIndex(c => /toup|microskop|microscope|cmex|euromex|usb2\.0|obs virtual/i.test(c.name || ''));
  if (prefer >= 0) sel.selectedIndex = prefer + 1;
  else if (items.length === 1) sel.selectedIndex = 1;
}

function _camNameIsUsbVideo(name) {
  return /^usb video device/i.test(name || '');
}
function _camNameIsPlugin(name) {
  return /camera plug-?in/i.test(name || '');
}
function _camNameIsDummy(name) {
  return /usb video device|camera plug-?in|many ?cam|droidcam|nvidia broadcast|iriun|splitcam/i.test(name || '');
}
function _camNameIsObs(name) {
  return /obs virtual|virtual camera/i.test(name || '') && !_camNameIsPlugin(name);
}
function _camNameIsMicro(name) {
  return /toup|microskop|microscope|cmex|euromex|bioblue|tucsen|usb2\.0\s*cam|amcam/i.test(name || '');
}
function _camNameIsLaptop(name) {
  return /integrated|facetime|hd webcam|laptop|built-?in|realtek|lenovo|dell webcam/i.test(name || '');
}
function _camRank(c) {
  const n = (c && c.name) || '';
  if (_camNameIsMicro(n)) return 0;
  if (_camNameIsObs(n)) return 1;
  if (!_camNameIsDummy(n) && !_camNameIsLaptop(n)) return 2;
  if (_camNameIsLaptop(n)) return 3;
  return 4;
}

async function probeLocalCamAgent() {
  const ports = [8012, 8013];
  for (const port of ports) {
    const base = 'http://127.0.0.1:' + port;
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 700);
      const r = await fetch(base + '/api/cam-health', {
        mode: 'cors', credentials: 'omit', cache: 'no-store', signal: ctrl.signal,
      });
      clearTimeout(t);
      if (!r.ok) continue;
      const health = await r.json();
      if (!health || health.service !== 'medlab-cam') continue;
      const s = await fetch(base + '/api/scan_cameras', {
        mode: 'cors', credentials: 'omit', cache: 'no-store',
      });
      if (!s.ok) continue;
      const data = await s.json();
      return { port, base, data };
    } catch (_) { /* agent yo‘q */ }
  }
  return null;
}

function _stopLocalPump() {
  _localPumpGen += 1;
  if (_localBlobUrl) {
    try { URL.revokeObjectURL(_localBlobUrl); } catch (_) {}
    _localBlobUrl = '';
  }
}

function startLocalFramePump() {
  const gen = ++_localPumpGen;
  const img = document.getElementById('videoFeed');
  const tick = async () => {
    if (gen !== _localPumpGen || _liveMode !== 'local' || !_localCamBase) return;
    try {
      const r = await fetch(_localCamBase + '/api/frame.jpg?t=' + Date.now(), {
        mode: 'cors', credentials: 'omit', cache: 'no-store',
      });
      if (r.ok) {
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        const old = _localBlobUrl;
        _localBlobUrl = url;
        if (img) {
          img.src = url;
          img.style.display = '';
        }
        if (old) URL.revokeObjectURL(old);
      }
    } catch (_) { /* kadr tashlab ketiladi */ }
    if (gen === _localPumpGen) setTimeout(tick, 70);
  };
  tick();
}

async function captureLocalBlobs(n) {
  if (!_localCamBase) throw new Error('Lokal mikroskop ochilmagan');
  const out = [];
  const count = Math.max(1, Math.min(n || 3, 6));
  for (let i = 0; i < count; i++) {
    if (i) await new Promise(r => setTimeout(r, 160));
    const r = await fetch(_localCamBase + '/api/frame.jpg?t=' + Date.now(), {
      mode: 'cors', credentials: 'omit', cache: 'no-store',
    });
    if (!r.ok) continue;
    const blob = await r.blob();
    if (blob && blob.size) out.push(new File([blob], `live_${i + 1}.jpg`, { type: 'image/jpeg' }));
  }
  if (!out.length) throw new Error('Kadr olinmadi');
  return out;
}

function _stopBrowserStream() {
  if (_browserStream) {
    try { _browserStream.getTracks().forEach(t => t.stop()); } catch (_) {}
    _browserStream = null;
  }
  const lv = document.getElementById('liveVideo');
  if (lv) {
    lv.srcObject = null;
    lv.style.display = 'none';
  }
}

function _mediaCamMsg(e) {
  const name = (e && e.name) || '';
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
    return 'Brauzer kameraga ruxsat bermadi. Manzil yonidagi kamera belgisidan ruxsatni yoqing.';
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return 'Kamera ochilmadi. Ro‘yxatdan OBS Virtual Camera yoki ToupcamMicro ni tanlang.';
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return 'Brauzer kamera topilmadi. USB mikroskopni ulang.';
  }
  if (name === 'OverconstrainedError' || name === 'ConstraintNotSatisfiedError') {
    return 'Tanlangan kamera hozir ochilmadi. Ro‘yxatdan boshqa qurilmani tanlang.';
  }
  if (name === 'SecurityError') {
    return 'Brauzer kamera ruxsatini blokladi. Sahifani https://lab.fermi.uz da oching.';
  }
  return (e && e.message) || 'Brauzer kamerani ocholmadi.';
}

async function _gUMUnlock(constraints) {
  const stream = await navigator.mediaDevices.getUserMedia(constraints);
  stream.getTracks().forEach(t => t.stop());
  await new Promise(r => setTimeout(r, 280));
}

function _mapVideoInputs(all) {
  return (all || []).filter(d => d.kind === 'videoinput').map((d, i) => ({
    index: i,
    deviceId: d.deviceId,
    name: (d.label || ('Kamera ' + (i + 1))).trim(),
    kind: (_camNameIsMicro(d.label) || _camNameIsObs(d.label)) ? 'microscope' : 'webcam',
    resolution: '—',
  }));
}

async function listBrowserCameras() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return [];
  const labeled = (cams) => cams.some(c => c.name && !/^Kamera \d+$/i.test(c.name));
  let lastErr = null;
  let cams = _mapVideoInputs(await navigator.mediaDevices.enumerateDevices().catch(e => {
    lastErr = e;
    return [];
  }));
  if (labeled(cams)) return cams;

  let perm = '';
  try {
    if (navigator.permissions && navigator.permissions.query) {
      perm = (await navigator.permissions.query({ name: 'camera' })).state || '';
    }
  } catch (_) {}

  if (perm !== 'granted') {
    const unlocks = [];
    cams.filter(c => c.deviceId && _camRank(c) < 3).forEach(c => {
      unlocks.push({ audio: false, video: { deviceId: { exact: c.deviceId } } });
    });
    unlocks.push({ audio: false, video: true });
    for (const constraints of unlocks) {
      try {
        await _gUMUnlock(constraints);
        lastErr = null;
        break;
      } catch (e) {
        lastErr = e;
        if (e && (e.name === 'NotAllowedError' || e.name === 'PermissionDeniedError')) break;
      }
    }
  }

  cams = _mapVideoInputs(await navigator.mediaDevices.enumerateDevices().catch(() => []));
  if (cams.length) return cams;
  if (lastErr) {
    const err = new Error(_mediaCamMsg(lastErr));
    err.code = lastErr.name || 'media';
    throw err;
  }
  return cams;
}

async function setSource(src) {
  if (cameraRunning && src !== currentSource) await stopCamera(true);
  currentSource = src;
  document.querySelectorAll('.src-tab').forEach(t => {
    const on = t.getAttribute('data-src') === src;
    t.classList.toggle('active', on);
    t.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  document.getElementById('paneUpload').style.display = src === 'upload' ? '' : 'none';
  document.getElementById('panePhone').style.display  = src === 'phone'  ? '' : 'none';
  document.getElementById('paneScope').style.display  = src === 'scope'  ? '' : 'none';

  if (src === 'upload') {
    renderMediaThumbs();
    if (uploadedFiles.length) showMainPreview(_previewIndex);
    else showLivePreview(false);
  } else {
    renderMediaThumbs();
    showLivePreview(false);
    scanCameras();
  }
  updateOverlay();
  refreshConnPill();
}

function updateOverlay() {
  const ov = document.getElementById('camOverlay');
  const icon = document.getElementById('overlayIcon');
  const text = document.getElementById('overlayText');
  const hint = document.getElementById('overlayHint');
  const kicker = document.getElementById('overlayKicker');
  const showingUpload = uploadedFiles.length && currentSource === 'upload';
  if (cameraRunning || showingUpload) {
    ov.classList.add('hidden');
    return;
  }
  ov.classList.remove('hidden');
  const m = labMeta(currentLab);
  if (icon) icon.textContent = m.icon;
  if (kicker) kicker.textContent = (m.name || 'Mikroskopiya').replace(/ Natijasi$/i, '') + ' · maydon';
  if (!text) return;
  if (currentSource === 'phone') {
    text.textContent = 'Telefon okulyari kutilmoqda';
    if (hint) hint.textContent = 'Chapdan telefonni yoqing — preparat kadri shu maydonda ochiladi';
  } else if (currentSource === 'scope') {
    text.textContent = 'Mikroskop signal kutilmoqda';
    if (hint) hint.textContent = 'Chapdan mikroskopni tanlab yoqing — yorug‘ maydon shu yerda';
  } else {
    text.textContent = 'Preparat kutilmoqda';
    if (hint) hint.textContent = 'Chapdan yoqma rasmini yuklang — ko‘rish maydoni ochiladi';
  }
}

function showLivePreview(live) {
  const vf = document.getElementById('videoFeed');
  const lv = document.getElementById('liveVideo');
  const up = document.getElementById('uploadedContent');
  if (live && _liveMode === 'browser' && _browserStream) {
    if (vf) { vf.style.display = 'none'; vf.removeAttribute('src'); }
    if (up) up.style.display = 'none';
    if (lv) {
      lv.style.display = '';
      if (lv.srcObject !== _browserStream) lv.srcObject = _browserStream;
      lv.play().catch(() => {});
    }
  } else if (live && _liveMode === 'local') {
    if (lv) { lv.style.display = 'none'; lv.srcObject = null; }
    if (up) up.style.display = 'none';
    if (vf) vf.style.display = '';
    startLocalFramePump();
  } else if (live) {
    if (lv) { lv.style.display = 'none'; lv.srcObject = null; }
    if (up) up.style.display = 'none';
    if (vf) {
      vf.style.display = '';
      vf.onerror = () => {
        if (cameraRunning) toast('Jonli tasvir uzildi. Qayta Yoqish ni bosing.', 'red');
      };
      const p = vf.getAttribute('data-stream-path') || '/video_feed';
      vf.src = apiPath(p) + '?t=' + Date.now();
    }
  } else if (uploadedFiles.length && currentSource === 'upload') {
    if (vf) { vf.style.display = 'none'; vf.removeAttribute('src'); }
    if (lv) { lv.style.display = 'none'; lv.srcObject = null; }
    if (up) up.style.display = '';
  } else {
    if (vf) { vf.style.display = 'none'; vf.removeAttribute('src'); }
    if (lv) { lv.style.display = 'none'; lv.srcObject = null; }
    if (up) up.style.display = 'none';
  }
  updateOverlay();
  const box = document.getElementById('mediaBox');
  if (box) {
    box.classList.toggle('is-live', !!live && cameraRunning);
    box.classList.toggle('has-upload', !live && uploadedFiles.length > 0 && currentSource === 'upload');
  }
  if (live) {
    const thumbs = document.getElementById('mediaThumbs');
    if (thumbs) thumbs.classList.add('hidden');
  }
  refreshLabPlatform();
}

async function scanCameras() {
  const my = ++_scanGen;
  const phoneSel = document.getElementById('phoneSelect');
  const scopeSel = document.getElementById('scopeSelect');
  if (phoneSel) phoneSel.innerHTML = '<option>Qidirilmoqda...</option>';
  if (scopeSel) scopeSel.innerHTML = '<option>Qidirilmoqda...</option>';

  let cams = [];
  let browserErr = '';
  try {
    cams = await listBrowserCameras();
  } catch (e) {
    browserErr = (e && e.message) || '';
  }
  if (my !== _scanGen) return;

  const ranked = cams.slice().sort((a, b) => _camRank(a) - _camRank(b));
  let scopes = ranked.filter(c => _camRank(c) <= 2);
  let phones = ranked.filter(c => _camRank(c) >= 2 && _camRank(c) <= 3 && !scopes.includes(c));
  if (!scopes.length) {
    scopes = ranked.filter(c => _camRank(c) <= 3);
    phones = ranked.filter(c => !scopes.includes(c));
  }
  if (!scopes.length) scopes = ranked.slice();

  if (phoneSel) fillSelect(phoneSel, phones, '— Telefon / webcam tanlang —');
  if (scopeSel) fillSelect(scopeSel, scopes, '— Mikroskop tanlang —');

  const box = document.getElementById('scopeDriverBox');
  if (box) {
    if (scopes.length) {
      box.style.display = 'none';
      box.innerHTML = '';
    } else if (browserErr) {
      box.style.display = '';
      const perm = /ruxsat/i.test(browserErr);
      box.innerHTML = perm
        ? `<strong>Kamera ruxsati kerak.</strong><br>${esc(browserErr)}`
        : `<strong>Kamera ochilmadi.</strong><br>${esc(browserErr)}`;
    } else {
      box.style.display = '';
      box.innerHTML = `Mikroskop topilmadi. USB ni ulang, Chrome da kamera ruxsatini yoqing, keyin ⟳ bosing.`;
    }
  }

  if (currentSource === 'scope' && scopes.length) {
    toast(`${scopes.length} ta mikroskop topildi`, 'green');
  } else if (currentSource === 'phone' && phones.length) {
    toast(`${phones.length} ta kamera topildi`, 'green');
  } else if (currentSource !== 'upload') {
    toast(browserErr || 'Mos kamera topilmadi', 'red');
  }
}

function _gumSets(deviceId) {
  if (!deviceId) return [{ audio: false, video: true }];
  return [
    { audio: false, video: { deviceId: { exact: deviceId } } },
    { audio: false, video: { deviceId: { exact: deviceId }, width: { max: 1920 }, height: { max: 1080 } } },
    { audio: false, video: { deviceId: { exact: deviceId }, width: { ideal: 640 }, height: { ideal: 480 } } },
    { audio: false, video: { deviceId: { ideal: deviceId } } },
  ];
}

async function _openBrowserCam(deviceId) {
  const cams = _mapVideoInputs(await navigator.mediaDevices.enumerateDevices().catch(() => []));
  const order = [];
  const seen = new Set();
  const push = (c) => {
    if (!c || !c.deviceId || seen.has(c.deviceId)) return;
    seen.add(c.deviceId);
    order.push(c);
  };
  push(cams.find(c => c.deviceId === deviceId));
  cams.slice().sort((a, b) => _camRank(a) - _camRank(b)).forEach(push);

  let lastErr = null;
  for (const cam of order) {
    for (const constraints of _gumSets(cam.deviceId)) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        const track = stream.getVideoTracks()[0];
        if (!track || track.readyState === 'ended') {
          stream.getTracks().forEach(t => t.stop());
          continue;
        }
        return { stream, cam };
      } catch (e) {
        lastErr = e;
      }
    }
  }
  throw lastErr || new Error('Kamera ochilmadi');
}

async function startLive(kind) {
  const sel = document.getElementById(kind === 'scope' ? 'scopeSelect' : 'phoneSelect');
  const startBtn = document.getElementById(kind === 'scope' ? 'scopeStartBtn' : 'phoneStartBtn');
  const stopBtn  = document.getElementById(kind === 'scope' ? 'scopeStopBtn' : 'phoneStopBtn');
  const msgId    = kind === 'scope' ? 'scopeMsg' : 'phoneMsg';
  const val = (sel && sel.value) || '';
  if (!val) { toast('Avval qurilmani tanlang', 'red'); return; }
  if (startBtn) startBtn.disabled = true;

  if (val.startsWith('l:')) {
    const parts = val.split(':');
    const port = parts[1];
    const idx = parseInt(parts[2], 10);
    if (!port || isNaN(idx)) { if (startBtn) startBtn.disabled = false; toast('Avval qurilmani tanlang', 'red'); return; }
    _stopBrowserStream();
    _stopLocalPump();
    _localCamBase = 'http://127.0.0.1:' + port;
    try {
      const r = await fetch(_localCamBase + '/api/start_camera', {
        method: 'POST',
        mode: 'cors',
        credentials: 'omit',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ index: idx }),
      });
      const res = await r.json();
      if (!res || !res.success) {
        if (startBtn) startBtn.disabled = false;
        const m = (res && res.message) || 'Mikroskop ochilmadi';
        msg(msgId, m, 'red');
        toast(m, 'red');
        return;
      }
      _liveMode = 'local';
      cameraRunning = true;
      if (stopBtn) stopBtn.disabled = false;
      document.getElementById('connPill').textContent = '● Mikroskop ulangan';
      document.getElementById('connPill').classList.add('pill-connected');
      msg(msgId, res.message || 'Jonli tasvir ochildi', 'green');
      toast('Mikroskop yoqildi', 'green');
      updateAnalyzeBtn();
      showLivePreview(true);
      return;
    } catch (e) {
      if (startBtn) startBtn.disabled = false;
      const m = 'Lokal mikroskop yordamchisi javob bermadi';
      msg(msgId, m, 'red');
      toast(m, 'red');
      return;
    }
  }

  if (val.startsWith('b:')) {
    const deviceId = val.slice(2);
    try {
      _stopBrowserStream();
      await new Promise(r => setTimeout(r, 220));
      const opened = await _openBrowserCam(deviceId);
      const stream = opened.stream || opened;
      const openedCam = opened.cam;
      if (openedCam && openedCam.deviceId && sel) {
        const want = 'b:' + openedCam.deviceId;
        if ([...sel.options].some(o => o.value === want)) sel.value = want;
      }
      _browserStream = stream;
      _liveMode = 'browser';
      cameraRunning = true;
      if (stopBtn) stopBtn.disabled = false;
      document.getElementById('connPill').textContent = kind === 'scope' ? '● Mikroskop ulangan' : '● Telefon ulangan';
      document.getElementById('connPill').classList.add('pill-connected');
      msg(msgId, 'Jonli tasvir ochildi', 'green');
      toast(kind === 'scope' ? 'Mikroskop yoqildi' : 'Telefon yoqildi', 'green');
      updateAnalyzeBtn();
      showLivePreview(true);
      const lv = document.getElementById('liveVideo');
      if (lv && !lv.videoWidth) {
        await Promise.race([
          new Promise(r => { lv.onloadedmetadata = () => r(); }),
          new Promise(r => setTimeout(r, 2000)),
        ]);
      }
      return;
    } catch (e) {
      if (startBtn) startBtn.disabled = false;
      const m = _mediaCamMsg(e);
      msg(msgId, m, 'red');
      toast(m, 'red');
      return;
    }
  }

  const idx = parseInt(val.replace(/^s:/, ''), 10);
  if (isNaN(idx)) { if (startBtn) startBtn.disabled = false; toast('Avval qurilmani tanlang', 'red'); return; }
  try {
    _stopBrowserStream();
    await new Promise(r => setTimeout(r, 180));
    const opened = await _openBrowserCam('');
    const stream = opened.stream || opened;
    const openedCam = opened.cam;
    if (openedCam && openedCam.deviceId && sel) {
      const want = 'b:' + openedCam.deviceId;
      if ([...sel.options].some(o => o.value === want)) sel.value = want;
    }
    _browserStream = stream;
    _liveMode = 'browser';
    cameraRunning = true;
    if (stopBtn) stopBtn.disabled = false;
    document.getElementById('connPill').textContent = kind === 'scope' ? '● Mikroskop ulangan' : '● Telefon ulangan';
    document.getElementById('connPill').classList.add('pill-connected');
    msg(msgId, 'Jonli tasvir ochildi', 'green');
    toast(kind === 'scope' ? 'Mikroskop yoqildi' : 'Telefon yoqildi', 'green');
    updateAnalyzeBtn();
    showLivePreview(true);
    return;
  } catch (e) {
    if (startBtn) startBtn.disabled = false;
    const m = _mediaCamMsg(e);
    msg(msgId, m, 'red');
    toast(m, 'red');
  }
}

async function stopCamera(silent) {
  const wasBrowser = _liveMode === 'browser';
  const wasLocal = _liveMode === 'local';
  const localBase = _localCamBase;
  _stopBrowserStream();
  _stopLocalPump();
  _liveMode = '';
  _localCamBase = '';
  if (wasLocal && localBase) {
    try {
      await fetch(localBase + '/api/stop_camera', {
        method: 'POST', mode: 'cors', credentials: 'omit',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
    } catch (_) {}
  } else if (!wasBrowser) {
    await api(apiPath('/api/stop_camera'), 'POST');
  }
  cameraRunning = false;
  ['phoneStartBtn', 'scopeStartBtn'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = false;
  });
  ['phoneStopBtn', 'scopeStopBtn'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = true;
  });
  document.getElementById('connPill').textContent = '○ Ulanmagan';
  document.getElementById('connPill').classList.remove('pill-connected');
  msg('phoneMsg', '', '');
  msg('scopeMsg', '', '');
  updateAnalyzeBtn();
  refreshConnPill();
  showLivePreview(false);
  if (!silent) toast("O'chirildi", 'gray');
}

async function analyze() {
  if (_analyzeBusy) return;
  if (!patientFieldsComplete()) {
    markPatientFields(true);
    const more = document.getElementById('daftarMore');
    if (more && PATIENT_FIELDS.some(id => !valOf(id) && (id.startsWith('daftar')))) more.open = true;
    toast('Bemor ma’lumotlarini to‘ldiring — qizil maydonlarni tekshiring.', 'red');
    focusFirstMissingPatient();
    return;
  }
  checkSexNameHint();
  markPatientFields(false);
  ensureSampleNo();
  if (currentSource === 'upload' && uploadedFiles.length) {
    _analyzeBusy = true;
    updateAnalyzeBtn();
    try {
      await analyzeFile();
    } catch (e) {
      _analyzeBusy = false;
      stopAnalyzing();
      toast((e && e.message) || 'Tahlil xatosi', 'red');
    }
    return;
  }
  if (cameraRunning) {
    _analyzeBusy = true;
    updateAnalyzeBtn();
    try {
      if (_liveMode === 'browser') await analyzeBrowserLive();
      else if (_liveMode === 'local') await analyzeLocalLive();
      else await analyzeCamera();
    } catch (e) {
      _analyzeBusy = false;
      stopAnalyzing();
      toast((e && e.message) || 'Tahlil xatosi', 'red');
    }
    return;
  }
  toast('Avval rasm yuklang yoki qurilmani yoqing', 'red');
}

async function captureLiveBlobs(n) {
  const vid = document.getElementById('liveVideo');
  if (!vid || !vid.videoWidth) throw new Error('Jonli tasvir hali ochilmadi');
  const out = [];
  const count = Math.max(1, Math.min(n || 3, 6));
  for (let i = 0; i < count; i++) {
    if (i) await new Promise(r => setTimeout(r, 160));
    const c = document.createElement('canvas');
    c.width = vid.videoWidth;
    c.height = vid.videoHeight;
    c.getContext('2d').drawImage(vid, 0, 0);
    const blob = await new Promise(res => c.toBlob(res, 'image/jpeg', 0.93));
    if (blob) out.push(new File([blob], `live_${i + 1}.jpg`, { type: 'image/jpeg' }));
  }
  if (!out.length) throw new Error('Kadr olinmadi');
  return out;
}

async function analyzeLocalLive() {
  startAnalyzing(false);
  let files;
  try {
    files = await captureLocalBlobs(3);
  } catch (e) {
    stopAnalyzing();
    toast((e && e.message) || 'Kadr olinmadi', 'red');
    return;
  }
  const formData = new FormData();
  files.forEach(f => formData.append('files[]', f));
  formData.append('lab_type', currentLab);
  formData.append('source', 'upload');
  appendPatientToFormData(formData);
  appendMicroscopeToFormData(formData);
  await postAnalyzeForm(formData);
}

async function analyzeBrowserLive() {
  startAnalyzing(false);
  let files;
  try {
    files = await captureLiveBlobs(3);
  } catch (e) {
    stopAnalyzing();
    toast((e && e.message) || 'Kadr olinmadi', 'red');
    return;
  }
  const formData = new FormData();
  files.forEach(f => formData.append('files[]', f));
  formData.append('lab_type', currentLab);
  formData.append('source', currentSource === 'phone' ? 'phone' : 'upload');
  appendPatientToFormData(formData);
  appendMicroscopeToFormData(formData);
  await postAnalyzeForm(formData);
}

async function analyzeFile() {
  const hasVideo = uploadedFiles.some(isVideoFile);
  startAnalyzing(hasVideo);

  const formData = new FormData();
  for (const f of uploadedFiles) formData.append('files[]', f);
  formData.append('lab_type', currentLab);
  formData.append('source', currentSource === 'phone' ? 'phone' : 'upload');
  appendPatientToFormData(formData);
  appendMicroscopeToFormData(formData);
  await postAnalyzeForm(formData);
}

async function postAnalyzeForm(formData) {
  try {
    if (typeof ensureCsrfCookie === 'function') await ensureCsrfCookie();
    let r = await fetch(apiPath('/api/analyze'), {
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
    if (r.status === 403 && isCsrfFail(res)) {
      if (typeof ensureCsrfCookie === 'function') await ensureCsrfCookie();
      r = await fetch(apiPath('/api/analyze'), {
        ...formFetchInit('POST'),
        body: formData,
      });
      try { res = await r.json(); } catch (_) {
        stopAnalyzing();
        toast('Sahifani yangilang (F5) va qayta urining', 'red');
        return;
      }
    }
    if (kickIfLoggedOut(r.status, res)) {
      stopAnalyzing();
      return;
    }
    if (r.status === 409 || res.busy === true) {
      toast(res.message || 'Boshqa tahlil davom etmoqda — natija kutilmoqda.', 'blue');
      if (res.job_id) pollResult(res.job_id);
      else stopAnalyzing();
      return;
    }
    if (r.status === 429) {
      stopAnalyzing();
      toast(res.message || 'So‘rovlar juda tez. Biroz kuting.', 'blue');
      return;
    }
    if (r.status === 503) {
      stopAnalyzing();
      toast(res.message || 'MedLab tahlil hozircha mavjud emas (sozlash kerak).', 'blue');
      return;
    }
    if (!res.success) { stopAnalyzing(); toast(res.message || `HTTP ${r.status}`, 'red'); return; }
    if (res.warnings && res.warnings.length)
      toast("Ogohlantirish: " + res.warnings.slice(0, 2).join("; "), 'gray');
    toast(res.message || 'Tahlil boshlandi', 'green');
    if (res.public_id) setPublicId(res.public_id);
    pollResult(res.job_id);
  } catch(e) { stopAnalyzing(); toast('Server xatosi', 'red'); }
}

async function analyzeCamera() {
  startAnalyzing(false);
  const res = await api(apiPath('/api/analyze'), 'POST', {
    source: 'camera',
    lab_type: currentLab,
    microscope: getMicroscopePayload(),
    ...patientPayload(),
  });
  if (res._httpStatus === 409 || res.busy === true) {
    toast(res.message || 'Boshqa tahlil davom etmoqda — natija kutilmoqda.', 'blue');
    if (res.job_id) pollResult(res.job_id);
    else stopAnalyzing();
    return;
  }
  if (res._httpStatus === 429) {
    stopAnalyzing();
    toast(res.message || 'So‘rovlar juda tez. Biroz kuting.', 'blue');
    return;
  }
  if (res._httpStatus === 503) {
    stopAnalyzing();
    toast(res.message || 'MedLab tahlil hozircha mavjud emas (sozlash kerak).', 'blue');
    return;
  }
  if (!res.success) { stopAnalyzing(); toast(res.message || 'Xato', 'red'); return; }
  if (res.public_id) setPublicId(res.public_id);
  pollResult(res.job_id);
}

function startAnalyzing(isVideo) {
  _analyzeBusy = true;
  document.getElementById('analyzeBtn').disabled = true;
  const ov = document.getElementById('analyzeOv');
  if (ov) ov.classList.remove('hidden');
  const hint = document.getElementById('azHint');
  if (hint) {
    if (isVideo) {
      hint.textContent = 'Video kadrlar o‘qilmoqda — biroz kuting';
    } else if (currentSource === 'upload' && uploadedFiles.length > 1) {
      hint.textContent = `${uploadedFiles.length} ta rasm birga o‘qilmoqda — hammasi hisobga olinadi`;
    } else {
      hint.textContent = 'Preparat o‘qilmoqda — biroz kuting';
    }
  }
  document.getElementById('analyzeStatus').textContent = '';
  startAnalyzeTimer();
  updateAnalyzeBtn();
  showLoading();
  refreshLabPlatform();
}

function stopAnalyzing() {
  _analyzeBusy = false;
  stopAnalyzeTimer();
  updateAnalyzeBtn();
  const ov = document.getElementById('analyzeOv');
  if (ov) ov.classList.add('hidden');
  document.getElementById('analyzeStatus').textContent = '';
  refreshLabPlatform();
}

let _pollGen = 0;

function pollResult(jobId) {
  const myGen = ++_pollGen;
  let tries = 0;
  let lastStatus = '';
  const MAX_TRIES = 300;
  let t = null;

  const tick = async () => {
    if (myGen !== _pollGen) {
      if (t) clearInterval(t);
      return;
    }
    tries++;
    try {
      const q = jobId ? ('?job_id=' + encodeURIComponent(jobId)) : '';
      const data = await api(apiPath('/api/analysis_result' + q));
      if (!data || myGen !== _pollGen) return;
      if (jobId && data.job_id && data.job_id !== jobId) return;

      if (data.status !== lastStatus) {
        lastStatus = data.status;
        const statusMap = {
          'tahlil_qilinmoqda':       '⏳ Tahlil qilinmoqda...',
          'video_tahlil_qilinmoqda': '🎬 Video kadrlar tahlil qilinmoqda...',
        };
        const st = document.getElementById('analyzeStatus');
        if (st) st.textContent = statusMap[data.status] || '';
      }

      const done = data.status === 'tayyor' || data.status === 'xato';
      const jobOk = !jobId || !data.job_id || data.job_id === jobId;
      if (done && jobOk && data.loading === false) {
        if (t) clearInterval(t);
        if (myGen !== _pollGen) return;
        stopAnalyzing();
        const st = document.getElementById('analyzeStatus');
        if (st) {
          st.textContent = data.status === 'tayyor' ? '✅ Tahlil tayyor' : '❌ Xato yuz berdi';
          setTimeout(() => { st.textContent = ''; }, 5000);
        }
        if (currentPublicId && data.public_id && data.public_id !== currentPublicId) {
          stopAnalyzing();
          toast('Yangi tahlil tayyor: ' + data.public_id + ' — Tarixdan oching', 'green');
          return;
        }
        if (data.public_id) setPublicId(data.public_id);
        renderResult(data);
        updateAnalyzeBtn();
        scrollResultIntoView();
      }
    } catch (e) {
      // tarmoq xatosi — davom etamiz
    }

    if (tries >= MAX_TRIES) {
      if (t) clearInterval(t);
      if (myGen === _pollGen) {
        stopAnalyzing();
        toast('Vaqt tugadi — server javobi kelmadi. «Bekor qilish» yoki «Yangi tahlil»ni bosing.', 'red');
      }
    }
  };

  tick();
  t = setInterval(tick, 800);
}

function showLoading() {
  setExportButtonsEnabled(false);
  const m = labMeta(currentLab);
  document.getElementById('resultBody').innerHTML = `
    <div class="result-loading">
      <div class="load-bar" aria-hidden="true"><i></i></div>
      ${esc(m.loading)}
    </div>`;
  document.getElementById('resultTs').textContent = '';
}

function _pdfActionButtons() {
  return [
    document.getElementById('pdfClinicBtn'),
    document.getElementById('pdfPatientBtn'),
  ].filter(Boolean);
}

function setExportButtonsEnabled(on) {
  _pdfActionButtons().forEach(b => { b.disabled = !on; });
}

function renderResult(data) {
  const body = document.getElementById('resultBody');
  const ts   = document.getElementById('resultTs');
  if (data.public_id) setPublicId(data.public_id);
  if (data.lab_type && LAB_META[data.lab_type]) selectLab(data.lab_type);

  const pending = data.status === 'tahlil_qilinmoqda' || data.status === 'video_tahlil_qilinmoqda';
  if (pending) {
    body.innerHTML = `
      <div class="result-loading">
        <div class="load-bar" aria-hidden="true"><i></i></div>
        Tahlil hali tayyor emas (ID: ${esc(currentPublicId || '—')})
      </div>`;
    ts.textContent = data.timestamp || '';
    setExportButtonsEnabled(false);
    return;
  }
  if (data.status === 'xato') {
    body.innerHTML = `<div class="r-error r-anim">⚠ ${esc(data.text)}</div>`;
    ts.textContent = data.timestamp || '';
    setExportButtonsEnabled(false);
    return;
  }
  if (!data.text) { body.innerHTML = '<div class="r-normal">Natija bo\'sh</div>'; setExportButtonsEnabled(false); return; }

  ts.textContent = data.timestamp ? `🕐 ${data.timestamp}` : '';
  body.setAttribute('data-raw-text', data.text || '');
  body.innerHTML = markdownToHtml(data.text);
  setExportButtonsEnabled(true);
  updateResultRegistrationId();
  _hasResult = true;
  _validated = false;
  const vb = document.getElementById('validateBtn');
  if (vb) {
    vb.disabled = false;
    vb.classList.remove('is-ok');
    vb.textContent = 'Tasdiqlash';
  }
  refreshLabPlatform();
  if (_hasResult) {
    rememberPatient();
    scrollResultIntoView();
  }
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

function markdownToHtml(text, opts) {
  const forPrint = !!(opts && opts.forPrint);
  const anim = forPrint ? '' : ' r-anim';
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
      html += `<div class="r-h3${anim}">${inlineFormat(l.slice(5))}</div>`;
      i++;
      continue;
    }
    if (l.startsWith('### ')) {
      html += `<div class="r-h2${anim}">${inlineFormat(l.slice(4))}</div>`;
      i++;
      continue;
    }
    if (l.startsWith('## ')) {
      html += `<div class="r-h1${anim}">${inlineFormat(l.slice(3))}</div>`;
      i++;
      continue;
    }

    if (l.startsWith('- ') || l.startsWith('• ')) {
      html += `<div class="r-list${anim}">${inlineFormat(l.slice(2))}</div>`;
      i++;
      continue;
    }
    if (l.startsWith('* ') && !l.startsWith('**')) {
      html += `<div class="r-list${anim}">${inlineFormat(l.slice(2))}</div>`;
      i++;
      continue;
    }

    if (/^\d+\.\s/.test(l)) {
      const inner = inlineFormat(l.replace(/^\d+\.\s+/, ''));
      html += `<div class="r-list-num${anim}">${inner}</div>`;
      i++;
      continue;
    }

    if (l === '---' || l === '***' || /^[-*]{3,}$/.test(l)) {
      html += '<hr class="r-hr">';
      i++;
      continue;
    }

    let cls = 'r-line' + anim;
    if (!forPrint) {
      const lLow = l.toLowerCase();
      if (lLow.includes('patolog') || lLow.includes('anormal') || lLow.includes('buzil') ||
          lLow.includes('giper') || lLow.includes('kamay') || lLow.includes('topildi') ||
          lLow.includes('aniqlandi') || lLow.includes('diqqat')) {
        cls += ' r-warn';
      }
      if (lLow.includes('norma') && !lLow.includes('normadan') && !lLow.includes('normasiz')) {
        cls += ' r-ok';
      }
    }
    html += `<div class="${cls}">${inlineFormat(l)}</div>`;
    i++;
  }
  return html;
}

function clearResult() {
  _pollGen++;
  document.getElementById('resultBody').innerHTML = emptyResultHtml();
  document.getElementById('resultTs').textContent = '';
  document.getElementById('resultBody').removeAttribute('data-raw-text');
  _sampleSeq = 0;
  setPublicId('');
  setExportButtonsEnabled(false);
  _hasResult = false;
  _validated = false;
  const vb = document.getElementById('validateBtn');
  if (vb) {
    vb.disabled = true;
    vb.classList.remove('is-ok');
    vb.textContent = 'Tasdiqlash';
  }
  refreshLabPlatform();
}

function _joinReportLines(arr) {
  return arr.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function _extractPatientTables(lines) {
  const tableLines = [];
  let inTable = false;
  for (const line of lines) {
    const t = line.trim();
    if (isMdTableRow(t) || isMdTableSeparator(t)) {
      tableLines.push(line);
      inTable = true;
    } else if (inTable && !t) {
      tableLines.push('');
      inTable = false;
    } else {
      inTable = false;
    }
  }
  return _joinReportLines(tableLines);
}

function _synthesizePatientTable(raw) {
  const rows = [];
  const seen = new Set();
  for (const line of String(raw || '').split('\n')) {
    let t = line.replace(/^#{1,4}\s+/, '').replace(/^[-•*]\s+/, '').trim();
    if (!t || t.length > 180) continue;
    const m = t.match(/^(.{3,70}?)\s*[:—–\-]\s+(.{1,90})$/);
    if (!m || !/\d/.test(m[2])) continue;
    const k = m[1].replace(/\*+/g, '').trim();
    const v = m[2].replace(/\*+/g, '').trim();
    if (seen.has(k.toLowerCase())) continue;
    seen.add(k.toLowerCase());
    rows.push(`| ${k} | ${v} | — | — |`);
  }
  if (rows.length < 2) return '';
  return ['| Ko\'rsatkich | Topilgan | Normal | Baho |', ...rows].join('\n');
}

function filterPatientReport(raw) {
  const lines = String(raw || '').split('\n');
  return _extractPatientTables(lines) || _synthesizePatientTable(raw) || '';
}

function _stampNow() {
  const now = new Date();
  const p = n => String(n).padStart(2, '0');
  return {
    now,
    dateStr: now.toLocaleDateString('uz-UZ', { year: 'numeric', month: 'long', day: 'numeric' }),
    timeStr: now.toLocaleTimeString('uz-UZ'),
    fileStamp: `${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}_${p(now.getHours())}${p(now.getMinutes())}`,
  };
}

function _fillPrintArea(mode) {
  mode = mode === 'patient' ? 'patient' : 'clinic';
  const lab = LAB_META[currentLab];
  const { dateStr, timeStr } = _stampNow();
  const area = document.getElementById('printArea');
  if (area) {
    area.dataset.printMode = mode;
    area.classList.toggle('print-mode-patient', mode === 'patient');
    area.classList.toggle('print-mode-clinic', mode === 'clinic');
  }

  const setEl = (id, txt, html) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (html) el.innerHTML = html; else el.textContent = txt;
  };
  setEl('printLabBadge', `${lab.icon} ${lab.name}`);
  setEl('printPublicId', valOf('accSample') || currentPublicId || '—');
  setEl('printDate', dateStr);
  setEl('printTime', timeStr);
  setEl('printPageDate', '', `<strong>${dateStr}</strong> &nbsp; ${timeStr}`);
  setEl('printLabLine', `${lab.icon} ${lab.name}`);
  const up = document.getElementById('userPill');
  const rawUser = up && up.textContent ? up.textContent.trim() : '';
  const exec = rawUser.replace(/^👤\s*/, '').trim() || '—';
  setEl('printExecutor', exec);
  setEl('printPatientName', valOf('accName'));
  const ageSex = [valOf('accAge') ? (valOf('accAge') + ' yosh') : '', valOf('accSex')].filter(Boolean).join(', ');
  setEl('printPatientAge', ageSex);
  setEl('printPatientWard', valOf('accWard') + (_priority === 'stat' ? (valOf('accWard') ? ' · STAT' : 'STAT') : ''));
  setEl('printSampleNo', valOf('accSample'));

  const banner = document.getElementById('printKindBanner');
  if (banner) {
    banner.className = 'print-kind-banner print-kind-clinic';
    banner.innerHTML =
      '<div class="print-kind-kicker">Shifokor uchun</div>' +
      '<div class="print-kind-title">KLINIK HISOBOT</div>' +
      '<div class="print-kind-sub">To‘liq morfologiya, AI xulosalari, differensial talqin va tavsiyalar</div>';
  }

  const sec = document.getElementById('printSectionTitle');
  if (sec) {
    sec.innerHTML = '<span>🩺</span> KLINIK LABORATORIYA HISOBOTI (to‘liq)';
  }

  const note = document.getElementById('printClosingNote');
  if (note) {
    note.className = 'print-closing-note print-closing-clinic';
    note.textContent =
      'Ushbu hujjat shifokor va laborant uchun to‘liq klinik hisobotdir. MedLab morfologiya yordamchisi; yakuniy tashxis, davolash va rasmiy xulosa faqat litsenziyali mutaxassis tomonidan qo‘yiladi.';
  }

  setEl('printFooterAi', 'Klinik hisobot: MedLab');
  const copy = document.getElementById('printFooterCopy');
  if (copy) {
    copy.innerHTML =
      '&copy; 2026 Far&#x2018;ona Jamoat Salomatligi Tibbiyot Instituti. Barcha huquqlar himoyalangan. Yakuniy tashxis faqat mutaxassis tomonidan qo&#x2018;yilishi lozim.';
  }

  const body = document.getElementById('resultBody');
  const rawText = body.getAttribute('data-raw-text') || '';
  let html;
  if (mode === 'patient') {
    const wraps = body ? body.querySelectorAll('.r-table-wrap') : [];
    if (wraps.length) {
      html = Array.from(wraps).map((w) => w.outerHTML).join('');
    } else {
      const tables = filterPatientReport(rawText);
      html = tables
        ? markdownToHtml(tables, { forPrint: true })
        : '<p class="print-no-table">Natija jadvali yo‘q. Qayta tahlil qiling.</p>';
    }
  } else if (rawText.trim()) {
    html = markdownToHtml(rawText, { forPrint: true });
  } else {
    const clone = body.cloneNode(true);
    clone.querySelectorAll('.result-empty,.result-loading').forEach(el => el.remove());
    html = clone.innerHTML;
  }
  document.getElementById('printContent').innerHTML = html || '<p>Natija mavjud emas</p>';

  const pm = document.getElementById('printMicroscopeBlock');
  if (pm) {
    if (mode === 'clinic') {
      const mh = buildPrintMicroscopeHtml();
      if (mh) {
        pm.style.display = 'block';
        pm.innerHTML = mh;
      } else {
        pm.style.display = 'none';
        pm.innerHTML = '';
      }
    } else {
      pm.style.display = 'none';
      pm.innerHTML = '';
    }
  }
}

async function ensureHtml2Pdf() {
  if (typeof html2pdf === 'function') return true;
  await new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js';
    s.async = true;
    s.onload = () => resolve(true);
    s.onerror = () => reject(new Error('PDF kutubxonasi yuklanmadi'));
    document.head.appendChild(s);
  });
  return typeof html2pdf === 'function';
}

async function savePDF(mode) {
  mode = mode === 'patient' ? 'patient' : 'clinic';
  try {
    await ensureHtml2Pdf();
  } catch (e) {
    toast('PDF uchun kutubxona yuklanmadi (internetni tekshiring)', 'red');
    return;
  }
  _fillPrintArea(mode);
  const el = document.getElementById('printArea');
  el.style.display = 'block';

  const { fileStamp } = _stampNow();
  const kind = mode === 'patient' ? 'Tahlil' : 'Klinik';
  const idPart = currentPublicId ? currentPublicId.replace(/[^\w-]/g, '') : currentLab;
  const fname = `MedLab_${kind}_${idPart}_${fileStamp}.pdf`;
  const btn = document.getElementById(mode === 'patient' ? 'pdfPatientBtn' : 'pdfClinicBtn');
  const prevLabel = btn ? btn.textContent : '';
  setExportButtonsEnabled(false);
  if (btn) btn.textContent = '⏳ Saqlanmoqda...';

  try {
    const patient = mode === 'patient';
    await html2pdf()
      .set({
        margin:      patient ? [8, 10, 8, 10] : [10, 10, 10, 10],
        filename:    fname,
        image:       { type: 'jpeg', quality: 0.95 },
        html2canvas: {
          scale: patient ? 2.2 : 2,
          useCORS: true,
          backgroundColor: '#ffffff',
          logging: false,
          onclone: (doc) => {
            const root = doc.getElementById('printArea');
            if (!root) return;
            root.style.display = 'block';
            root.querySelectorAll('*').forEach((n) => {
              n.style.webkitPrintColorAdjust = 'exact';
              n.style.printColorAdjust = 'exact';
            });
          },
        },
        jsPDF:       { unit: 'mm', format: 'a4', orientation: 'portrait' },
        pagebreak:   patient ? { mode: ['avoid-all'] } : { mode: ['css', 'legacy'] },
      })
      .from(document.getElementById('printArea'))
      .save();
    toast(`📄 ${kind} PDF saqlandi: ${fname}`, 'green');
  } catch (e) {
    toast('PDF saqlashda xato: ' + e.message, 'red');
  } finally {
    el.style.display = 'none';
    if (btn) btn.textContent = prevLabel;
    setExportButtonsEnabled(true);
  }
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function msg(id, text, color) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.style.color = { green:'#166534', red:'#b91c1c', gray:'#665e4a', '':'' }[color] || '#665e4a';
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
      if (isCsrfFail(data)) {
        return stamp({ success: false, message: "Sahifani yangilang (CSRF). F5 bosing." });
      }
      kickIfLoggedOut(r.status, data);
      return stamp({ success: false, message: 'Kirish kerak' });
    }
    if (!r.ok && data.success !== false)
      return stamp({ success: false, message: data.message || data.detail || `HTTP ${r.status}` });
    return stamp(data);
  } catch (e) {
    return { success: false, message: "Server bilan aloqa yo'q", _httpStatus: 0 };
  }
}

function toast(text, color = 'red') {
  const el = document.getElementById('toast');
  if (!el) return;
  const map = { green: 'toast-ios--green', red: 'toast-ios--red', blue: 'toast-ios--info', gray: 'toast-ios--muted' };
  const cls = map[color] || map.red;
  el.className = cls;
  el.textContent = text;
  el.style.opacity = '0';
  el.style.transform = 'translate(-50%, -28px)';
  void el.offsetWidth;
  el.style.opacity = '1';
  el.style.transform = 'translate(-50%, 0)';
  clearTimeout(el._t);
  el._t = setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translate(-50%, -28px)';
  }, 3800);
}

function setPublicId(id) {
  currentPublicId = String(id || '').trim();
  refreshSampleId();
  const el = document.getElementById('resultPublicId');
  if (!el) return;
  const shown = valOf('accSample') || currentPublicId;
  if (shown && currentPublicId) {
    el.textContent = shown;
    el.classList.remove('hidden');
  } else {
    el.textContent = '';
    el.classList.add('hidden');
  }
}

async function copyPublicId() {
  const id = valOf('accSample') || currentPublicId;
  if (!id) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(id);
    } else {
      const ta = document.createElement('textarea');
      ta.value = id;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    toast('ID nusxalandi: ' + id, 'green');
  } catch (_) {
    toast(id, 'blue');
  }
}

function openHistory(skipLoad) {
  const ov = document.getElementById('historyOverlay');
  if (!ov) return;
  ov.classList.remove('hidden');
  document.body.classList.add('hist-open');
  ov.dataset.prevFocus = (document.activeElement && document.activeElement.id) || 'historyBtn';
  const inp = document.getElementById('histSearch');
  if (inp) {
    const hdr = document.getElementById('headerIdSearch');
    if (hdr && hdr.value && !inp.value) inp.value = hdr.value;
    setTimeout(() => inp.focus(), 50);
  }
  if (!skipLoad) {
    _histPage = 1;
    loadHistory();
  }
}

function closeHistory(ev) {
  if (ev && ev.target && ev.target.id !== 'historyOverlay' && ev.type === 'click') return;
  const ov = document.getElementById('historyOverlay');
  if (ov) ov.classList.add('hidden');
  document.body.classList.remove('hist-open');
  const back = ov && ov.dataset.prevFocus;
  const el = back && document.getElementById(back);
  if (el && typeof el.focus === 'function') el.focus();
}

function headerSearchHistory(ev) {
  ev.preventDefault();
  const q = (document.getElementById('headerIdSearch') || {}).value || '';
  const inp = document.getElementById('histSearch');
  if (inp) inp.value = q.trim();
  _histAutoOpen = true;
  openHistory(true);
  searchHistory(ev, true);
}

function searchHistory(ev, alreadyOpen) {
  if (ev && ev.preventDefault) ev.preventDefault();
  _histQuery = ((document.getElementById('histSearch') || {}).value || '').trim();
  _histPage = 1;
  if (!alreadyOpen) _histAutoOpen = true;
  if (!alreadyOpen) {
    const ov = document.getElementById('historyOverlay');
    if (ov && ov.classList.contains('hidden')) openHistory(true);
  }
  loadHistory();
}

function _histStatus(st, createdAt) {
  if (st === 'tayyor') return { cls: 'hist-st--ok', label: 'Tayyor' };
  if (st === 'xato') return { cls: 'hist-st--err', label: 'Xato' };
  const ts = createdAt ? Date.parse(createdAt) : NaN;
  if (!Number.isNaN(ts) && (Date.now() - ts) > 20 * 60 * 1000) {
    return { cls: 'hist-st--err', label: 'Uzildi' };
  }
  return { cls: 'hist-st--wait', label: 'Jarayonda' };
}

function _histSource(src) {
  return ({ camera: 'Mikroskop', upload: 'Fayl', phone: 'Telefon' })[src] || src || '';
}

async function loadHistory() {
  const list = document.getElementById('histList');
  const meta = document.getElementById('histMeta');
  if (!list) return;
  if (_histPage <= 1) list.innerHTML = '<div class="hist-empty">Yuklanmoqda...</div>';
  const params = new URLSearchParams();
  if (_histQuery) params.set('q', _histQuery);
  params.set('page', String(_histPage));
  const data = await api(apiPath('/api/analyses?' + params.toString()));
  if (!data || !data.success) {
    list.innerHTML = `<div class="hist-empty">${esc((data && data.message) || 'Tarix yuklanmadi')}</div>`;
    if (meta) meta.textContent = '';
    return;
  }
  const rows = data.results || [];
  if (meta) {
    meta.textContent = _histQuery
      ? `${data.count} ta natija — «${_histQuery}»`
      : `Jami ${data.count} ta tahlil`;
  }
  if (!rows.length && _histPage <= 1) {
    list.innerHTML = `<div class="hist-empty">${_histQuery ? 'Bu ID bo‘yicha tahlil topilmadi' : 'Hali tahlillar yo‘q — birinchi tahlilni qiling'}</div>`;
    return;
  }
  if (_histAutoOpen && data.exact_id && rows.length === 1 && _histQuery) {
    _histAutoOpen = false;
    openHistoryRecord(data.exact_id);
    return;
  }
  _histAutoOpen = false;
  const cards = rows.map((r) => {
    const id = r.sample_id || r.public_id || '';
    const name = (r.patient_name || '').trim() || 'Bemor kiritilmagan';
    return `<article class="hist-card" data-public-id="${esc(r.public_id)}">
      <div class="hist-card-main">
        <span class="hist-id">${esc(id)}</span>
        <span class="hist-patient">${esc(name)}</span>
      </div>
      <button type="button" class="hist-del" data-del-id="${esc(r.public_id)}" title="Tahlilni o‘chirish">O‘chirish</button>
    </article>`;
  }).join('');
  const more = data.has_more
    ? '<button type="button" class="hist-card" id="histMoreBtn" onclick="_histPage++; loadHistory()">Yana yuklash</button>'
    : '';
  const oldMore = document.getElementById('histMoreBtn');
  if (oldMore) oldMore.remove();
  if (_histPage <= 1) list.innerHTML = cards + more;
  else list.insertAdjacentHTML('beforeend', cards + more);
}

async function deleteHistoryRecord(publicId) {
  if (!publicId) return;
  if (!window.confirm('Bu tahlil o‘chirilsinmi? Qayta tiklanmaydi.')) return;
  const data = await api(apiPath('/api/analyses/' + encodeURIComponent(publicId)), 'DELETE');
  if (!data || !data.success) {
    toast((data && data.message) || 'O‘chirilmadi', 'red');
    return;
  }
  toast('Tahlil o‘chirildi', 'green');
  if (currentPublicId === publicId) clearResult();
  _histPage = 1;
  loadHistory();
}

async function openHistoryRecord(publicId) {
  const data = await api(apiPath('/api/analyses/' + encodeURIComponent(publicId)));
  if (!data || !data.success || !data.analysis) {
    toast((data && data.message) || 'Tahlil ochilmadi', 'red');
    return;
  }
  const rec = data.analysis;
  closeHistory();
  applyPatientFields({
    patient_name: rec.patient_name,
    age: rec.age,
    sex: rec.sex,
    ward: rec.ward,
    specimen_site: rec.specimen_site,
    clinical_note: rec.clinical_note,
    region: rec.region,
    locality: rec.locality,
    clinic: rec.clinic,
    facility_type: rec.facility_type,
    lab_type: rec.lab_type,
  });
  rememberPatient({
    patient_name: rec.patient_name,
    age: rec.age,
    sex: rec.sex,
    ward: rec.ward,
    specimen_site: rec.specimen_site,
    clinical_note: rec.clinical_note,
    region: rec.region,
    locality: rec.locality,
    clinic: rec.clinic,
    facility_type: rec.facility_type,
    lab_type: rec.lab_type,
  });
  if (rec.sample_id) {
    const sid = document.getElementById('accSample');
    if (sid) sid.value = rec.sample_id;
  }
  renderResult({
    status: rec.status,
    text: rec.text,
    timestamp: rec.created_label,
    public_id: rec.public_id,
    lab_type: rec.lab_type,
  });
  toast('Tahlil ochildi: ' + rec.public_id + ' — bemor kartasi to‘ldirildi', 'green');
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeHistory();
});

async function logoutMedlab() {
  if (!window.confirm('Tizimdan chiqasizmi?')) return;
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
  lockLabSelect();
  bindUploadFileActions();
  refreshUserPill();
  daftarLoadSettings();
  refreshSampleId();
  selectLab('histology');
  onMicroChange();
  setSource('upload');
  updateAnalyzeBtn();
  tickLabClock();
  setInterval(tickLabClock, 1000);
  refreshLabPlatform();
  const vil = document.getElementById('daftarViloyat');
  if (vil) {
    vil.addEventListener('change', () => {
      fillLocalitySelect(vil.value);
      daftarSave();
      refreshSampleId();
      updateAnalyzeBtn();
    });
  }
  ['daftarLocality', 'daftarClinic', 'daftarType'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    const onDaftar = () => {
      if (valOf(id)) el.classList.remove('missing');
      daftarSave();
      refreshSampleId();
      updateAnalyzeBtn();
    };
    el.addEventListener('change', onDaftar);
    el.addEventListener('input', onDaftar);
  });
  ['accName', 'accAge', 'accSex', 'accWard', 'accSite', 'accClinical'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    const onAcc = () => {
      if (valOf(id)) el.classList.remove('missing');
      if (id === 'accName' || id === 'accSex') checkSexNameHint();
      if (id === 'accName') schedulePatientLookup();
      refreshLabPlatform();
      updateAnalyzeBtn();
    };
    el.addEventListener('change', onAcc);
    el.addEventListener('input', onAcc);
    if (id === 'accName') {
      el.addEventListener('blur', () => {
        if (valOf('accName').length >= 2) tryAutofillPatient(valOf('accName'), { soft: true });
      });
    }
    el.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        if (id === 'accName' && valOf('accName').length >= 2) {
          tryAutofillPatient(valOf('accName'), { soft: false }).then(() => {
            if (patientFieldsComplete() && (uploadedFiles.length || cameraRunning) && !_analyzeBusy) {
              analyze();
            } else {
              focusFirstMissingPatient();
            }
          });
          return;
        }
        if (patientFieldsComplete() && (uploadedFiles.length || cameraRunning) && !_analyzeBusy) {
          analyze();
        } else {
          focusFirstMissingPatient();
        }
      }
    });
  });
  checkSexNameHint();
  refreshConnPill();
  const more = document.getElementById('daftarMore');
  if (more) {
    more.open = !valOf('daftarViloyat') || !valOf('daftarLocality') || !valOf('daftarClinic');
  }
  const histList = document.getElementById('histList');
  if (histList) {
    histList.addEventListener('click', (e) => {
      const del = e.target.closest('[data-del-id]');
      if (del) {
        e.preventDefault();
        e.stopPropagation();
        deleteHistoryRecord(del.getAttribute('data-del-id'));
        return;
      }
      const btn = e.target.closest('[data-public-id]');
      if (btn && btn.id !== 'histMoreBtn') openHistoryRecord(btn.getAttribute('data-public-id'));
    });
  }
});
