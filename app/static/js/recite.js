// Recite practice controller: learn → recite 1..n → drills → full runs → passed.
// The server owns all state (GET/POST /api/recite/:id); this file renders it,
// speaks text, and records audio for grading.
(function () {
  "use strict";
  const { speak, request, post, el } = window.Memoria;

  const root = document.getElementById("recite");
  if (!root) return;
  const api = root.dataset.api;
  const $ = (name) => root.querySelector(`[data-el="${name}"]`);
  const ui = {
    progress: $("progress"), progressLabel: $("progress-label"), progressRight: $("progress-right"),
    kicker: $("step-kicker"), title: $("step-title"), text: $("text"), result: $("result"),
    listen: $("listen"), peek: $("peek"), ready: $("ready"), mic: $("mic"), micHint: $("mic-hint"),
    typedForm: $("typed-form"),
  };

  let view = null;
  let peeking = false; // text revealed right now
  let peeked = false; // any peek/listen since the last graded attempt (sent to the server)
  let busy = false;
  let recorder = null;
  let recTimer = null;
  const MAX_SECONDS = 180;

  const lineName = (i) => `line ${i + 1}`;
  const BAR_WIDTHS = ["w-1/3", "w-1/2", "w-2/3", "w-3/4", "w-5/6", "w-full"];

  function sectionNode(s, opts) {
    const p = el("p", "mb-2");
    p.dataset.section = s.ordinal;
    if (opts.hidden) {
      const words = s.text.split(/\s+/).length;
      const bar = el("div", `hidden-line ${BAR_WIDTHS[Math.min(5, Math.floor(words / 2))]}`);
      bar.title = lineName(s.ordinal);
      return bar;
    }
    const missed = new Set((opts.misses || []).filter((m) => m.section_id === s.id).map((m) => m.word));
    const weak = new Set(s.weak_words || []);
    const num = el("sup", "mr-1 select-none font-sans text-xs text-ink-soft", String(s.ordinal + 1));
    p.appendChild(num);
    s.text.split(/\s+/).forEach((w, i) => {
      if (i) p.appendChild(document.createTextNode(" "));
      const cls = missed.has(i) ? "w-miss" : weak.has(i) ? "w-weak" : "";
      p.appendChild(el("span", cls, w));
    });
    if (s.flagged && !opts.hidden) p.appendChild(el("span", "ml-2 align-middle text-base", "🔸"));
    return p;
  }

  function renderText(indexes, opts) {
    ui.text.replaceChildren();
    const secs = indexes.map((i) => view.sections[i]).filter(Boolean);
    secs.forEach((s) => ui.text.appendChild(sectionNode(s, opts)));
  }

  function show(node, on) {
    node.classList.toggle("hidden", !on);
  }

  function stepCopy(v) {
    const n = v.state.n;
    if (v.stage === "learn") {
      const first = v.learn[0];
      const many = v.learn.length > 1;
      return {
        kicker: many ? "New lines" : "New line",
        title: many ? `Listen to lines ${first + 1}–${n}` : `Listen to ${lineName(first)}`,
        hint: "Listen as many times as you like, then tap “I'm ready”.",
      };
    }
    if (v.stage === "recite") {
      return {
        kicker: "From memory",
        title: n === 1 ? "Say line 1" : `Say lines 1–${n}`,
        hint: "Tap the mic, say it, then tap again to finish.",
      };
    }
    if (v.stage === "drill") {
      const s = v.drill.section;
      const transition = v.drill.kind === "transition";
      return {
        kicker: "Tricky spot",
        title: transition ? `Say ${lineName(s - 1)} into ${lineName(s)}` : `Say ${lineName(s)} by itself`,
        hint: "Short practice on the hard part, then back to the chain.",
      };
    }
    if (v.stage === "full") {
      return {
        kicker: "Whole passage",
        title: "Say the whole thing!",
        hint: `No peeking now. ${v.k_required} clean runs in a row to pass.`,
      };
    }
    return { kicker: "Passed 🎉", title: "You know it by heart!", hint: "Recite it any time to keep it fresh." };
  }

  function render() {
    const v = view;
    const st = v.state;
    const copy = stepCopy(v);
    ui.kicker.textContent = copy.kicker;
    ui.title.textContent = copy.title;
    ui.micHint.textContent = copy.hint;

    if (v.stage === "passed") {
      ui.progressLabel.textContent = "Passed";
      ui.progress.value = 100;
    } else if (v.stage === "full" || (v.stage === "drill" && st.resume === "full")) {
      ui.progressLabel.textContent = "Whole passage";
      ui.progress.value = 70 + (30 * st.clean_full_runs) / v.k_required;
    } else {
      ui.progressLabel.textContent = `Line ${st.n} of ${v.total}`;
      ui.progress.value = (70 * Math.max(st.n - 1, 0)) / Math.max(v.total, 1);
    }
    ui.progressRight.textContent =
      v.stage === "full" || st.resume === "full" || v.stage === "passed"
        ? `Clean runs ${Math.min(st.clean_full_runs, v.k_required)}/${v.k_required}`
        : "";

    const textIdx = v.stage === "learn" ? v.learn : v.stage === "full" ? [] : v.scope;
    const reveal = v.stage === "passed" || v.show_text || peeking;
    if (v.stage !== "full") renderText(textIdx, { hidden: !reveal });
    else ui.text.replaceChildren(el("p", "font-sans text-lg text-ink-soft", `${v.total} lines · from the top`));

    const canHelp = v.stage !== "full";
    show(ui.listen, canHelp);
    show(ui.peek, canHelp && !v.show_text && v.stage !== "passed");
    ui.peek.setAttribute("aria-pressed", String(peeking));
    ui.peek.textContent = peeking ? "🙈 Hide" : "👀 Peek";
    show(ui.ready, v.stage === "learn");
    show(ui.mic, v.stage !== "learn");
    show(ui.typedForm, !!root.dataset.typed && v.stage !== "learn");
    ui.typedForm.classList.toggle("flex", !!root.dataset.typed && v.stage !== "learn");
    [ui.listen, ui.peek, ui.ready].forEach((b) => (b.disabled = busy || !!recorder));
    ui.mic.disabled = busy;
  }

  function listenIndexes() {
    if (view.stage === "learn") return view.learn;
    if (view.stage === "recite") {
      // Hear the newest chunk only; the rest should already be in memory.
      return view.scope.slice(-Math.max(1, view.chunk || 1));
    }
    return view.scope;
  }

  function sayIndexes(idx) {
    return speak(idx.map((i) => view.sections[i] && view.sections[i].text), view.tts_rate);
  }

  function setResult(kind, nodes) {
    ui.result.className = `mt-4 rounded-2xl p-4 ${kind === "good" ? "bg-emerald-50 text-good" : kind === "bad" ? "bg-rose-50 text-ink" : "bg-stone-100 text-ink-soft"}`;
    ui.result.replaceChildren(...nodes);
    show(ui.result, true);
  }

  function clearResult() {
    show(ui.result, false);
    ui.result.replaceChildren();
  }

  function cheer(before, after, a) {
    if (after.stage === "passed" && before.stage !== "passed") return "Passed! You know it by heart! 🎉";
    if (before.stage === "passed") return `Great review — ${a.matched}/${a.total} words.`;
    if (after.stage === "full" && before.stage === "recite") return "Every line! Now the whole passage, no peeking.";
    if (before.stage === "full") return `Clean run! ${after.state.clean_full_runs} of ${after.k_required}.`;
    if (before.stage === "drill") return after.stage === "drill" ? "Got it! One more." : "Nice fix! Back to the chain.";
    return `Clean! Line ${after.state.n} unlocked.`;
  }

  function showAttempt(before, after) {
    const a = after.attempt;
    if (a.verdict) {
      setResult("good", [el("p", "text-lg font-bold", cheer(before, after, a))]);
      return;
    }
    const nodes = [el("p", "mb-2 text-lg font-bold text-bad", `Almost! ${a.matched} of ${a.total} words.`)];
    const missedIds = new Set(a.missed_section_ids);
    const secs = before.sections.filter((s) => missedIds.has(s.id));
    const box = el("div", "verse text-xl");
    secs.forEach((s) => box.appendChild(sectionNode(s, { misses: a.misses })));
    nodes.push(box);
    if (after.stage === "drill" && before.stage !== "drill") {
      nodes.push(el("p", "mt-2 font-sans text-sm text-ink-soft", "Let's practice the tricky part on its own."));
    }
    if (!a.transcript.trim()) {
      nodes.push(el("p", "mt-2 font-sans text-sm text-ink-soft", "I didn't hear anything — is the mic on?"));
    }
    setResult("bad", nodes);
    // Replay what was missed so the fix is fresh.
    speak(secs.map((s) => s.text), view.tts_rate);
  }

  async function submit(fields) {
    busy = true;
    ui.micHint.textContent = "Listening back…";
    render();
    const before = view;
    try {
      fields.append("peeked", peeked ? "true" : "false");
      const next = await post(`${api}/attempt`, fields);
      view = next;
      peeked = false;
      peeking = false;
      busy = false;
      render();
      showAttempt(before, next);
    } catch (err) {
      busy = false;
      render();
      setResult("bad", [el("p", "font-semibold", err.message)]);
    }
  }

  function pickMime() {
    const options = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    return window.MediaRecorder && options.find((m) => MediaRecorder.isTypeSupported(m));
  }

  async function startRecording() {
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      setResult("bad", [el("p", "font-semibold", "This browser can't record audio. Try Chrome, Edge, Firefox or Safari over https.")]);
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
    } catch (_) {
      setResult("bad", [el("p", "font-semibold", "I need the microphone. Allow it in the browser's address bar, then try again.")]);
      return;
    }
    if (window.speechSynthesis) speechSynthesis.cancel();
    clearResult();
    const mime = pickMime();
    const chunks = [];
    recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    recorder.ondataavailable = (e) => e.data && e.data.size && chunks.push(e.data);
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      const type = recorder.mimeType || mime || "audio/webm";
      recorder = null;
      clearInterval(recTimer);
      ui.mic.classList.remove("recording");
      ui.mic.textContent = "🎙️";
      ui.mic.setAttribute("aria-label", "Record");
      const fd = new FormData();
      fd.append("audio", new Blob(chunks, { type }), type.includes("mp4") ? "clip.mp4" : "clip.webm");
      submit(fd);
    };
    recorder.start();
    const started = Date.now();
    ui.mic.classList.add("recording");
    ui.mic.textContent = "■";
    ui.mic.setAttribute("aria-label", "Stop recording");
    ui.micHint.textContent = "Recording… tap ■ when you're done.";
    recTimer = setInterval(() => {
      const s = Math.round((Date.now() - started) / 1000);
      ui.micHint.textContent = `Recording… ${s}s — tap ■ when you're done.`;
      if (s >= MAX_SECONDS && recorder) recorder.stop();
    }, 500);
    render();
  }

  ui.mic.addEventListener("click", () => {
    if (busy) return;
    if (recorder) recorder.stop();
    else startRecording();
  });

  ui.listen.addEventListener("click", () => {
    if (view.stage !== "passed") peeked = true;
    sayIndexes(listenIndexes());
  });

  ui.peek.addEventListener("click", () => {
    peeking = !peeking;
    if (peeking) peeked = true;
    render();
  });

  ui.ready.addEventListener("click", async () => {
    busy = true;
    render();
    try {
      view = await post(`${api}/ready`, {});
      peeking = false;
    } finally {
      busy = false;
      clearResult();
      render();
    }
  });

  ui.typedForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const input = ui.typedForm.elements.typed;
    const fd = new FormData();
    fd.append("typed", input.value);
    input.value = "";
    submit(fd);
  });

  request(api)
    .then((v) => {
      view = v;
      render();
    })
    .catch((err) => {
      ui.title.textContent = err.message;
    });
})();
