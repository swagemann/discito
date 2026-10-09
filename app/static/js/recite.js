// Recite practice controller: learn → recite 1..n → drills → full runs → passed.
// The server owns all state (GET/POST /api/recite/:id); this file renders it,
// speaks text, and records audio for grading.
(function () {
  "use strict";
  const { speak, request, post, el } = window.Discito;

  const root = document.getElementById("recite");
  if (!root) return;
  const api = root.dataset.api;
  const $ = (name) => root.querySelector(`[data-el="${name}"]`);
  const ui = {
    progress: $("progress"), progressLabel: $("progress-label"), progressRight: $("progress-right"),
    kicker: $("step-kicker"), title: $("step-title"), text: $("text"), result: $("result"),
    listen: $("listen"), ready: $("ready"), mic: $("mic"),
    typedForm: $("typed-form"),
  };

  let view = null;
  // Listened since the last graded attempt, or read along while recording.
  // Sent to the server as `peeked`; the parent sees it as "with help".
  let helped = false;
  let busy = false;
  let starting = false; // mic permission/stream requested, recorder not yet live
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
      };
    }
    if (v.stage === "recite") {
      return {
        kicker: "From memory",
        title: n === 1 ? "Say line 1" : `Say lines 1–${n}`,
      };
    }
    if (v.stage === "drill") {
      const s = v.drill.section;
      const transition = v.drill.kind === "transition";
      return {
        kicker: "Tricky spot",
        title: transition ? `Say ${lineName(s - 1)} into ${lineName(s)}` : `Say ${lineName(s)} by itself`,
      };
    }
    if (v.stage === "full") {
      return {
        kicker: "Whole passage",
        title: "Say the whole thing!",
      };
    }
    return { kicker: "Passed 🎉", title: "You know it by heart!" };
  }

  function render() {
    const v = view;
    const st = v.state;
    const copy = stepCopy(v);
    ui.kicker.textContent = copy.kicker;
    ui.title.textContent = copy.title;

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
    // The text stays up while the child reads and listens, and goes away once the
    // mic is live (unless the parent turned on read-along). Full runs never show it.
    const micLive = !!recorder || starting || (busy && v.stage !== "learn");
    const reveal = v.stage === "passed" || v.show_text || !micLive;
    if (v.stage !== "full") renderText(textIdx, { hidden: !reveal });
    else ui.text.replaceChildren(el("p", "font-sans text-lg text-ink-soft", `${v.total} lines · from the top`));

    show(ui.listen, v.stage !== "full");
    show(ui.ready, v.stage === "learn");
    show(ui.mic, v.stage !== "learn");
    show(ui.typedForm, !!root.dataset.typed && v.stage !== "learn");
    ui.typedForm.classList.toggle("flex", !!root.dataset.typed && v.stage !== "learn");
    [ui.listen, ui.ready].forEach((b) => (b.disabled = busy || !!recorder));
    ui.mic.disabled = busy || starting;
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

  // Transient status shown in the card (recording timer, grading spinner).
  function setStatus(text) {
    setResult("neutral", [el("p", "text-lg font-semibold", text)]);
  }

  function setMic(glyph, label) {
    ui.mic.textContent = glyph;
    ui.mic.setAttribute("aria-label", label);
  }

  function showGrading() {
    busy = true;
    setMic("⏳", "Grading");
    render();
    setStatus("Grading… 🎧");
  }

  function cheer(before, after, a) {
    if (after.stage === "passed" && before.stage !== "passed") return "Passed! You know it by heart! 🎉";
    if (before.stage === "passed") return `Great review — ${a.matched}/${a.total} words.`;
    if (after.stage === "full" && before.stage === "recite") return "Every line! Now the whole passage, no text.";
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
    showGrading();
    const before = view;
    try {
      fields.append("peeked", helped ? "true" : "false");
      const next = await post(`${api}/attempt`, fields);
      view = next;
      helped = false;
      busy = false;
      setMic("🎙️", "Record");
      render();
      showAttempt(before, next);
    } catch (err) {
      busy = false;
      setMic("🎙️", "Record");
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
    // Getting the mic can take a beat on phones; show it right away so a
    // second tap isn't needed (and can't start a second recorder).
    starting = true;
    if (window.speechSynthesis) speechSynthesis.cancel();
    if (view.show_text && view.stage !== "passed" && view.stage !== "full") helped = true;
    setMic("…", "Starting microphone");
    render();
    setStatus("Starting the mic…");
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
    } catch (_) {
      starting = false;
      setMic("🎙️", "Record");
      render();
      setResult("bad", [el("p", "font-semibold", "I need the microphone. Allow it in the browser's address bar, then try again.")]);
      return;
    }
    starting = false;
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
      const fd = new FormData();
      fd.append("audio", new Blob(chunks, { type }), type.includes("mp4") ? "clip.mp4" : "clip.webm");
      submit(fd);
    };
    recorder.start();
    const started = Date.now();
    ui.mic.classList.add("recording");
    setMic("■", "Stop recording");
    render();
    setStatus("🔴 Recording… tap ■ when you're done.");
    recTimer = setInterval(() => {
      if (!recorder || busy) return;
      const s = Math.round((Date.now() - started) / 1000);
      setStatus(`🔴 Recording… ${s}s — tap ■ when you're done.`);
      if (s >= MAX_SECONDS) stopRecording();
    }, 500);
  }

  // Feedback goes up before onstop fires so the tap never looks ignored.
  function stopRecording() {
    if (!recorder || busy) return;
    clearInterval(recTimer);
    ui.mic.classList.remove("recording");
    showGrading();
    recorder.stop();
  }

  ui.mic.addEventListener("click", () => {
    if (busy || starting) return;
    if (recorder) stopRecording();
    else startRecording();
  });

  ui.listen.addEventListener("click", () => {
    if (view.stage !== "passed") helped = true;
    sayIndexes(listenIndexes());
  });

  ui.ready.addEventListener("click", async () => {
    busy = true;
    render();
    try {
      view = await post(`${api}/ready`, {});
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
