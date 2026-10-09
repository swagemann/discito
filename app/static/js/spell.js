// Spelling controller: learn (see + copy), practice (hear + type, fix-up rounds),
// test (one strict pass, results at the end). State lives on the server.
(function () {
  "use strict";
  const { speak, request, post, el } = window.Discito;

  const root = document.getElementById("spell");
  if (!root) return;
  const api = root.dataset.api;
  const rate = parseFloat(root.dataset.rate) || 0.9;
  const $ = (name) => root.querySelector(`[data-el="${name}"]`);
  const ui = {
    sub: $("sub"), menu: $("menu"), knownLabel: $("known-label"), statusLabel: $("status-label"),
    knownBar: $("known-bar"), lastTest: $("last-test"), session: $("session"), round: $("round"),
    left: $("left"), showWord: $("show-word"), say: $("say"), sentenceHint: $("sentence-hint"),
    form: $("form"), feedback: $("feedback"), stop: $("stop"), results: $("results"),
  };
  const input = ui.form.elements.typed;
  let view = null;
  let busy = false;
  let lastSpokenId = null;

  const show = (n, on) => n.classList.toggle("hidden", !on);
  const pct = (x) => `${Math.round(x * 100)}%`;

  function sayCurrent() {
    const c = view && view.current;
    if (!c) return Promise.resolve();
    // Word, then the sentence (always for homophones, which need it), then the word again.
    const parts = [c.speak];
    if (c.sentence) parts.push(c.sentence, c.speak);
    return speak(parts, rate);
  }

  function renderMenu() {
    show(ui.menu, true);
    show(ui.session, false);
    ui.knownLabel.textContent = `${view.known} of ${view.total} words known`;
    ui.knownBar.value = view.total ? (100 * view.known) / view.total : 0;
    ui.statusLabel.textContent = view.status === "passed" ? "Test passed ✅" : "";
    const t = view.last_test;
    show(ui.lastTest, !!t);
    if (t) {
      ui.lastTest.replaceChildren(
        el("p", "text-sm text-ink-soft", `Last test: ${t.right}/${t.total} (${pct(t.score)}) — ${t.passed ? "passed" : `need ${pct(view.threshold)}`}`),
      );
    }
  }

  function renderSession() {
    show(ui.menu, false);
    show(ui.session, true);
    const c = view.current;
    const mode = view.mode;
    ui.round.textContent =
      mode === "learn" ? `Learn · copy ${view.copies + 1} of ${view.learn_copies}`
      : mode === "test" ? "Test"
      : view.round_kind === "fixup" ? `Round ${view.round_no} · fix-up` : `Round ${view.round_no}`;
    ui.left.textContent = `${view.remaining} left`;
    show(ui.showWord, mode === "learn");
    ui.showWord.textContent = mode === "learn" && c ? c.show : "";
    show(ui.sentenceHint, !!(c && c.homophone));
    ui.sentenceHint.textContent = c && c.homophone ? "Sounds like another word — listen to the sentence." : "";
    input.value = "";
    input.disabled = busy;
    if (c && c.id !== lastSpokenId) {
      lastSpokenId = c.id;
      sayCurrent();
    }
    input.focus();
  }

  function renderResults(result) {
    show(ui.menu, false);
    show(ui.session, false);
    show(ui.results, true);
    const head = el("div", "mb-4 text-center");
    head.appendChild(el("p", "text-5xl", result.passed ? "🏆" : "💪"));
    head.appendChild(el("p", "mt-2 text-2xl font-bold", `${result.right} of ${result.total} — ${pct(result.score)}`));
    head.appendChild(el("p", "text-ink-soft", result.passed ? "You passed the test!" : "Keep practicing — you're getting there."));
    const list = el("ul", "divide-y divide-line");
    result.answers.forEach((ans) => {
      const li = el("li", "flex items-center justify-between py-2");
      li.appendChild(el("span", "font-serif text-lg", ans.word));
      li.appendChild(
        ans.correct
          ? el("span", "text-good font-semibold", "✓")
          : el("span", "text-bad", `✗ ${ans.typed || "(blank)"}`),
      );
      list.appendChild(li);
    });
    const done = el("button", "btn-primary mt-6 w-full", "Done");
    done.type = "button";
    done.addEventListener("click", () => {
      show(ui.results, false);
      render();
    });
    ui.results.replaceChildren(head, list, done);
  }

  function render() {
    const known = `${view.known}/${view.total} known`;
    ui.sub.textContent = view.status === "passed" ? `Test passed · ${known}` : known;
    if (view.mode && view.current) renderSession();
    else renderMenu();
  }

  function flash(kind, nodes) {
    ui.feedback.className = `mt-4 rounded-2xl p-4 text-center ${kind === "good" ? "bg-emerald-50 text-good" : "bg-rose-50 text-ink"}`;
    ui.feedback.replaceChildren(...nodes);
    show(ui.feedback, true);
  }

  // Letter-by-letter comparison so the child sees exactly where it went wrong.
  function diffNode(expected, typed) {
    const p = el("p", "font-serif text-3xl tracking-wider");
    [...expected].forEach((ch, i) => {
      const ok = typed[i] && typed[i].toLowerCase() === ch.toLowerCase();
      p.appendChild(el("span", ok ? "" : "text-bad underline decoration-2", ch));
    });
    return p;
  }

  async function answer(typed) {
    if (busy) return;
    busy = true;
    const before = view;
    try {
      const next = await post(`${api}/answer`, { typed });
      const fb = next.feedback || {};
      if (fb.result) {
        view = next;
        busy = false;
        show(ui.feedback, false);
        renderResults(fb.result);
        return;
      }
      if (before.mode === "test") {
        show(ui.feedback, false);
      } else if (fb.correct) {
        flash("good", [el("p", "text-xl font-bold", before.mode === "learn" ? "✓ Nice copy!" : "✓ Correct!")]);
      } else {
        flash("bad", [
          el("p", "mb-1 font-bold text-bad", "Not quite. It's spelled:"),
          diffNode(fb.word || "", typed),
        ]);
        if (fb.word) await speak([fb.word, [...fb.word.toUpperCase()].join(", ")], rate);
      }
      if (fb.event === "fixup") flash("bad", [el("p", "font-bold", "Fix-up round: just the ones you missed.")]);
      if (fb.event === "round_done") flash("good", [el("p", "font-bold", "Round complete! 🎉 Starting the next one.")]);
      view = next;
      if (!view.mode || !view.current) lastSpokenId = null;
    } catch (err) {
      flash("bad", [el("p", "font-semibold", err.message)]);
    }
    busy = false;
    render();
  }

  ui.form.addEventListener("submit", (e) => {
    e.preventDefault();
    const typed = input.value.trim();
    if (!typed) return input.focus();
    answer(typed);
  });

  ui.say.addEventListener("click", () => {
    sayCurrent();
    input.focus();
  });

  root.querySelectorAll("[data-mode]").forEach((b) =>
    b.addEventListener("click", async () => {
      show(ui.feedback, false);
      lastSpokenId = null;
      view = await post(`${api}/start`, { mode: b.dataset.mode });
      render();
    }),
  );

  ui.stop.addEventListener("click", async () => {
    if (window.speechSynthesis) speechSynthesis.cancel();
    show(ui.feedback, false);
    view = await post(`${api}/start`, { mode: "stop" });
    lastSpokenId = null;
    render();
  });

  request(api)
    .then((v) => {
      view = v;
      render();
    })
    .catch((err) => (ui.sub.textContent = err.message));
})();
