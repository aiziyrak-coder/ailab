/**
 * Yo'llanma (patomorfologik tekshiruvga yo'llanma) varaqasini o'qish.
 *
 * Foydalanuvchi blankani suratga oladi → server matnni ajratadi → bemor kartasi
 * avtomatik to'ladi. Yo'llanma rasmi tahlilga yuborilmaydi (u H&E kesma emas).
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const say = (m, c) => (typeof toast === 'function' ? toast(m, c || 'gray') : null);

  // Kartaga tushadigan maydonlar (server javobidagi kalit → input id)
  const FIELD_MAP = {
    patient_name: 'accName',
    age: 'accAge',
    sex: 'accSex',
    ward: 'accWard',
    specimen_site: 'accSite',
    clinical_note: 'accClinical',
  };

  let busy = false;

  function setHint(text, tone) {
    const el = $('referralHint');
    if (!el) return;
    el.textContent = text;
    el.classList.remove('is-ok', 'is-err', 'is-busy');
    if (tone) el.classList.add(tone);
  }

  function applyReferral(r) {
    const filled = [];
    Object.keys(FIELD_MAP).forEach((key) => {
      const el = $(FIELD_MAP[key]);
      const val = (r[key] || '').trim();
      if (!el || !val) return;
      if (el.tagName === 'SELECT') {
        // Namuna joyi / jins — ro'yxatdagi mos qiymat
        const opt = [...el.options].find(
          (o) => o.value.toLowerCase() === val.toLowerCase()
            || o.textContent.trim().toLowerCase().startsWith(val.toLowerCase())
        );
        if (!opt) return;
        el.value = opt.value;
      } else {
        el.value = val;
      }
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.classList.add('from-referral');
      setTimeout(() => el.classList.remove('from-referral'), 2500);
      filled.push(key);
    });

    // Yo'llanma raqami — namuna izohiga qo'shiladi (karta raqami avtomatik)
    const extra = [];
    if (r.referral_no) extra.push('Yo‘llanma №' + r.referral_no);
    if (r.doctor) extra.push('Shifokor: ' + r.doctor);
    if (r.procedure) extra.push(r.procedure);
    const note = $('accClinical');
    if (note && extra.length) {
      const have = note.value.trim();
      const add = extra.join('; ');
      if (!have.includes(add.slice(0, 20))) {
        note.value = have ? `${have} · ${add}` : add;
        note.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }

    if (typeof updateAnalyzeBtn === 'function') updateAnalyzeBtn();
    if (typeof refreshLabPlatform === 'function') refreshLabPlatform();
    if (typeof checkSexNameHint === 'function') checkSexNameHint();
    return filled;
  }

  async function sendReferral(file) {
    const fd = new FormData();
    fd.append('file', file);
    if (typeof ensureCsrfCookie === 'function') await ensureCsrfCookie();
    const r = await fetch(apiPath('/api/referral/parse'), {
      ...formFetchInit('POST'),
      body: fd,
    });
    let data = null;
    try { data = await r.json(); } catch (_) { data = null; }
    return { status: r.status, data };
  }

  window.handleReferralSelect = async function (e) {
    const file = (e.target.files || [])[0];
    e.target.value = '';
    if (!file || busy) return;
    busy = true;
    const btn = $('referralBtn');
    if (btn) btn.disabled = true;
    setHint('Yo‘llanma o‘qilmoqda…', 'is-busy');
    try {
      const { status, data } = await sendReferral(file);
      if (status === 503) {
        setHint('Xizmat sozlanmagan', 'is-err');
        say((data && data.message) || 'Xizmat mavjud emas', 'blue');
        return;
      }
      if (!data || !data.success) {
        const msg = (data && data.message) || 'Yo‘llanma o‘qilmadi';
        setHint(msg, 'is-err');
        say(msg + ' — blankani to‘liq va yorug‘ suratga oling', 'red');
        return;
      }
      const filled = applyReferral(data.referral || {});
      if (!filled.length) {
        setHint('Maydonlar topilmadi — qo‘lda to‘ldiring', 'is-err');
        say('Yo‘llanmadan ma’lumot ajratib bo‘lmadi', 'red');
        return;
      }
      setHint(`To‘ldirildi: ${filled.length} ta maydon — tekshirib chiqing`, 'is-ok');
      say(`Yo‘llanmadan ${filled.length} ta maydon to‘ldirildi. Tekshirib chiqing.`, 'green');
      if (typeof focusFirstMissingPatient === 'function') focusFirstMissingPatient();
    } catch (_) {
      setHint('Server bilan aloqa yo‘q', 'is-err');
      say('Yo‘llanmani yuborib bo‘lmadi', 'red');
    } finally {
      busy = false;
      if (btn) btn.disabled = false;
    }
  };
})();
