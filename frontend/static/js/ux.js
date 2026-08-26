/**
 * MedLab — qulaylik qatlami (app.js dan keyin yuklanadi).
 *
 * Bu fayl app.js dagi mantiqni o'zgartirmaydi: mavjud funksiyalarni o'rab olib,
 * ustiga kundalik ishni tezlashtiradigan xatti-harakatlar qo'shadi.
 */
(function () {
  'use strict';

  const PATIENT_DRAFT_LS = 'medlab_draft_v1';
  const MICRO_LS = 'medlab_micro_v1';
  const DRAFT_TTL_MS = 12 * 60 * 60 * 1000; // 12 soat
  const DRAFT_FIELDS = ['accName', 'accAge', 'accSex', 'accWard', 'accSite', 'accClinical'];
  const MICRO_FIELDS = ['microOcularSel', 'microObjSel'];

  const $ = (id) => document.getElementById(id);
  const say = (msg, color) => (typeof toast === 'function' ? toast(msg, color || 'gray') : null);

  // ── Fayl nomlarini tabiiy tartibda saralash (photo_2 < photo_10) ──────────
  const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' });

  function sortUploads() {
    if (typeof uploadedFiles === 'undefined' || uploadedFiles.length < 2) return;
    uploadedFiles.sort((a, b) => collator.compare(a.name || '', b.name || ''));
  }

  function humanSize(bytes) {
    if (!bytes) return '';
    const mb = bytes / (1024 * 1024);
    return mb >= 1 ? mb.toFixed(1) + ' MB' : Math.max(1, Math.round(bytes / 1024)) + ' KB';
  }

  function showUploadSummary() {
    const box = document.querySelector('#filePreview .preview-count');
    if (!box || typeof uploadedFiles === 'undefined') return;
    const n = uploadedFiles.length;
    if (!n) return;
    const total = uploadedFiles.reduce((s, f) => s + (f.size || 0), 0);
    const size = humanSize(total);
    if (size && !box.textContent.includes(size)) box.textContent = `${n} ta fayl · ${size}`;
  }

  // ── loadFiles ni o'rash: saralash + hajm ko'rsatkichi ─────────────────────
  if (typeof window.loadFiles === 'function') {
    const orig = window.loadFiles;
    window.loadFiles = function (files) {
      const before = typeof uploadedFiles !== 'undefined' ? uploadedFiles.length : 0;
      const out = orig.apply(this, arguments);
      sortUploads();
      if (typeof renderFileList === 'function') renderFileList();
      if (typeof renderMediaThumbs === 'function') renderMediaThumbs();
      showUploadSummary();
      const after = typeof uploadedFiles !== 'undefined' ? uploadedFiles.length : 0;
      if (after > before && typeof showMainPreview === 'function') showMainPreview(0);
      return out;
    };
  }

  // ── Ctrl+V bilan rasm qo'yish (mikroskop skrinshoti uchun) ───────────────
  document.addEventListener('paste', (e) => {
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    const items = (e.clipboardData && e.clipboardData.items) || [];
    const files = [];
    for (const it of items) {
      if (it.kind !== 'file') continue;
      const f = it.getAsFile();
      if (!f || !/^image\//.test(f.type)) continue;
      const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const ext = (f.type.split('/')[1] || 'png').replace('jpeg', 'jpg');
      files.push(new File([f], f.name && f.name !== 'image.png' ? f.name : `clipboard_${stamp}.${ext}`, { type: f.type }));
    }
    if (!files.length) return;
    e.preventDefault();
    if (typeof loadFiles === 'function') loadFiles(files);
  });

  // ── Sahifaning istalgan joyiga tashlash ─────────────────────────────────
  let dragDepth = 0;
  const dropVeil = document.createElement('div');
  dropVeil.className = 'drop-veil';
  dropVeil.setAttribute('aria-hidden', 'true');
  dropVeil.innerHTML = '<div class="drop-veil-box">Rasmni shu yerga tashlang</div>';

  function veil(on) {
    if (on && !dropVeil.isConnected) document.body.appendChild(dropVeil);
    dropVeil.classList.toggle('on', !!on);
    if (!on && dropVeil.isConnected) dropVeil.remove();
  }

  const hasFiles = (e) => {
    const dt = e.dataTransfer;
    return !!dt && Array.from(dt.types || []).includes('Files');
  };

  window.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth++;
    veil(true);
  });
  window.addEventListener('dragover', (e) => {
    if (hasFiles(e)) e.preventDefault();
  });
  window.addEventListener('dragleave', (e) => {
    if (!hasFiles(e)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) veil(false);
  });
  window.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    veil(false);
    const files = Array.from(e.dataTransfer.files || []);
    if (files.length && typeof loadFiles === 'function') loadFiles(files);
  });

  // ── Klaviatura qisqartmalari ────────────────────────────────────────────
  document.addEventListener('keydown', (e) => {
    const t = e.target || {};
    const typing = t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable;
    const mod = e.ctrlKey || e.metaKey;

    // Ctrl/Cmd + Enter — tahlilni boshlash (istalgan joydan)
    if (mod && e.key === 'Enter') {
      e.preventDefault();
      if (typeof analyze === 'function') analyze();
      return;
    }
    // Ctrl/Cmd + Shift + N — yangi tahlil
    if (mod && e.shiftKey && (e.key === 'N' || e.key === 'n')) {
      e.preventDefault();
      if (typeof startNewAnalysis === 'function') startNewAnalysis();
      return;
    }
    // Ctrl/Cmd + K yoki "/" — namuna ID qidiruvi
    if ((mod && (e.key === 'k' || e.key === 'K')) || (!typing && e.key === '/')) {
      const s = $('headerIdSearch');
      if (s) {
        e.preventDefault();
        s.focus();
        s.select();
      }
      return;
    }
    // Ctrl/Cmd + H — tarix
    if (mod && (e.key === 'h' || e.key === 'H')) {
      e.preventDefault();
      if (typeof openHistory === 'function') openHistory();
    }
  });

  // ── Bemor kartasi qoralamasi: sahifa yopilib qolsa yo'qolmasin ───────────
  function saveDraft() {
    try {
      const data = {};
      let any = false;
      DRAFT_FIELDS.forEach((id) => {
        const el = $(id);
        if (el && el.value) {
          data[id] = el.value;
          any = true;
        }
      });
      if (!any) {
        localStorage.removeItem(PATIENT_DRAFT_LS);
        return;
      }
      localStorage.setItem(PATIENT_DRAFT_LS, JSON.stringify({ ts: Date.now(), data }));
    } catch (_) { /* xotira to'lgan bo'lishi mumkin */ }
  }

  function clearDraft() {
    try { localStorage.removeItem(PATIENT_DRAFT_LS); } catch (_) {}
  }

  function restoreDraft() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(PATIENT_DRAFT_LS) || 'null'); } catch (_) { return; }
    if (!saved || !saved.data || Date.now() - (saved.ts || 0) > DRAFT_TTL_MS) {
      clearDraft();
      return;
    }
    const busy = DRAFT_FIELDS.some((id) => ($(id) || {}).value);
    if (busy) return;
    let n = 0;
    DRAFT_FIELDS.forEach((id) => {
      const el = $(id);
      if (el && saved.data[id]) {
        el.value = saved.data[id];
        n++;
      }
    });
    if (!n) return;
    if (typeof updateAnalyzeBtn === 'function') updateAnalyzeBtn();
    if (typeof refreshLabPlatform === 'function') refreshLabPlatform();
    say('Tugallanmagan bemor kartasi tiklandi', 'blue');
  }

  DRAFT_FIELDS.forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener('input', saveDraft);
    el.addEventListener('change', saveDraft);
  });

  // ── Mikroskop sozlamasi eslab qolinadi ──────────────────────────────────
  function saveMicro() {
    try {
      const data = {};
      MICRO_FIELDS.forEach((id) => {
        const el = $(id);
        if (el) data[id] = el.value;
      });
      localStorage.setItem(MICRO_LS, JSON.stringify(data));
    } catch (_) {}
  }

  function restoreMicro() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(MICRO_LS) || 'null'); } catch (_) { return; }
    if (!saved) return;
    let changed = false;
    MICRO_FIELDS.forEach((id) => {
      const el = $(id);
      if (!el || !saved[id]) return;
      if ([...el.options].some((o) => o.value === saved[id])) {
        el.value = saved[id];
        changed = true;
      }
    });
    if (changed && typeof onMicroChange === 'function') onMicroChange();
  }

  MICRO_FIELDS.forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener('change', saveMicro);
  });

  // ── «Yangi tahlil» — qoralama ham tozalansin ────────────────────────────
  if (typeof window.startNewAnalysis === 'function') {
    const orig = window.startNewAnalysis;
    window.startNewAnalysis = function () {
      clearDraft();
      return orig.apply(this, arguments);
    };
  }

  // ── Ko'p faylni tozalashdan oldin so'rash ───────────────────────────────
  if (typeof window.clearFile === 'function') {
    const orig = window.clearFile;
    window.clearFile = function (force) {
      const n = typeof uploadedFiles !== 'undefined' ? uploadedFiles.length : 0;
      // Faqat foydalanuvchi «Tozalash» tugmasini bosganda so'raladi
      if (n > 3 && force !== true && window.event && window.event.isTrusted) {
        if (!window.confirm(`${n} ta fayl o'chirilsinmi?`)) return;
      }
      return orig.apply(this, arguments);
    };
  }

  // ── Hisobot matnini nusxalash tugmasi ───────────────────────────────────
  function reportText() {
    const body = $('resultBody');
    if (!body) return '';
    // Faqat haqiqiy hisobot matni — bo'sh holatdagi ko'rsatma nusxalanmasin
    return (body.getAttribute('data-raw-text') || '').trim();
  }

  function addCopyButton() {
    const bar = document.querySelector('.result-actions');
    if (!bar || $('copyReportBtn')) return;
    const btn = document.createElement('button');
    btn.id = 'copyReportBtn';
    btn.type = 'button';
    btn.className = 'btn-small';
    btn.title = 'Hisobot matnini nusxalash';
    btn.textContent = 'Nusxalash';
    btn.disabled = true;
    btn.addEventListener('click', async () => {
      const txt = reportText();
      if (!txt) return;
      try {
        await navigator.clipboard.writeText(txt);
        say('Hisobot nusxalandi', 'green');
      } catch (_) {
        const ta = document.createElement('textarea');
        ta.value = txt;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); say('Hisobot nusxalandi', 'green'); } catch (__) {}
        ta.remove();
      }
    });
    bar.appendChild(btn);
  }

  function syncCopyButton() {
    const btn = $('copyReportBtn');
    if (btn) btn.disabled = !reportText();
  }

  if (typeof window.setExportButtonsEnabled === 'function') {
    const orig = window.setExportButtonsEnabled;
    window.setExportButtonsEnabled = function () {
      const out = orig.apply(this, arguments);
      syncCopyButton();
      return out;
    };
  }

  // ── Tahlil ketayotganda sahifani yopishdan ogohlantirish ────────────────
  window.addEventListener('beforeunload', (e) => {
    if (typeof _analyzeBusy !== 'undefined' && _analyzeBusy) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  // ── Qisqartmalar haqida eslatma (tugma ustiga olib borilganda) ──────────
  function addHints() {
    const hints = {
      newAnalysisBtn: 'Yangi tahlil — hamma narsa tozalanadi (Ctrl+Shift+N)',
      newAnalysisBtnResult: 'Yangi tahlil — hamma narsa tozalanadi (Ctrl+Shift+N)',
      historyBtn: 'Tahlillar tarixi (Ctrl+H)',
      headerIdSearch: "Namuna ID bo'yicha qidirish (Ctrl+K yoki /)",
      uploadZone: "Rasm tanlang, sudrab tashlang yoki Ctrl+V bilan qo'ying",
    };
    Object.keys(hints).forEach((id) => {
      const el = $(id);
      if (el) el.title = hints[id];
    });
  }

  // ── Ishga tushirish ─────────────────────────────────────────────────────
  function init() {
    addHints();
    restoreDraft();
    restoreMicro();
    addCopyButton();
    syncCopyButton();
    showUploadSummary();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
