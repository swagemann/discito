// Shared helpers for practice pages: text-to-speech and small fetch wrappers.
(function () {
  "use strict";

  let voice = null;
  function pickVoice() {
    const voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
    voice =
      voices.find((v) => v.lang === "en-US" && v.localService) ||
      voices.find((v) => v.lang && v.lang.startsWith("en") && v.localService) ||
      voices.find((v) => v.lang && v.lang.startsWith("en")) ||
      null;
  }
  if (window.speechSynthesis) {
    pickVoice();
    speechSynthesis.addEventListener("voiceschanged", pickVoice);
  }

  // Speak each string in turn; resolves when the last one finishes (or is cancelled).
  function speak(parts, rate) {
    const list = (Array.isArray(parts) ? parts : [parts]).filter(Boolean);
    if (!window.speechSynthesis || !list.length) return Promise.resolve();
    speechSynthesis.cancel();
    return new Promise((resolve) => {
      let i = 0;
      const next = () => {
        if (i >= list.length) return resolve();
        const u = new SpeechSynthesisUtterance(list[i++]);
        u.rate = rate || 0.9;
        if (voice) u.voice = voice;
        u.onend = next;
        u.onerror = () => resolve();
        speechSynthesis.speak(u);
      };
      next();
    });
  }

  async function request(url, opts) {
    const res = await fetch(url, Object.assign({ credentials: "same-origin" }, opts));
    let body = null;
    try { body = await res.json(); } catch (_) { /* non-JSON error */ }
    if (!res.ok) {
      const msg = (body && body.detail) || `Something went wrong (${res.status}).`;
      throw new Error(typeof msg === "string" ? msg : "Something went wrong.");
    }
    return body;
  }

  function post(url, fields) {
    const fd = fields instanceof FormData ? fields : new FormData();
    if (!(fields instanceof FormData)) {
      for (const [k, v] of Object.entries(fields || {})) fd.append(k, v);
    }
    return request(url, { method: "POST", body: fd });
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  window.Discito = { speak, request, post, el };
})();

// Confirm destructive admin actions: <button data-confirm="Sure?">.
document.addEventListener("click", (e) => {
  const btn = e.target.closest && e.target.closest("[data-confirm]");
  if (btn && !window.confirm(btn.dataset.confirm)) e.preventDefault();
});
