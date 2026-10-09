# PRD: Verse Memorizer

Oct 8, 2026 · @scott

## Summary

Verse Memorizer is a self-hosted web app that gets a child from "never seen this poem" to "recited it from memory three times clean" using text-to-speech, speech-to-text grading, and chained line-by-line practice. A second tab does the same for weekly spelling lists with spoken prompts and typed answers.

It runs on Coolify, serves one household, and tracks per-child progress so a parent can see what is passed, what is in progress, and where each kid struggles.

## Users and roles

| Role | Access | What they do |
| --- | --- | --- |
| Parent | Single household login (password) | Adds passages and spelling lists, assigns them to kids, tunes thresholds, reviews attempts, overrides results |
| Child | Profile picker, no password | Picks their profile, sees their assignments, practices, recites, spells |

There is no per-child login. Admin actions are behind the parent session only.

## Scope

| Area | MVP | Later |
| --- | --- | --- |
| Children | Profiles with name and avatar, no password |  |
| Passages | Paste text, title, reference, type, due date, assign to kids, auto/manual sectioning | Bible lookup by reference (KJV/WEB), bulk import |
| Recite practice | Listen, chain (1..n), STT grading, drills, Full mode, pass tracking | Record-own-voice playback, offline PWA |
| Spelling | Lists, spoken prompts, typed answers, rounds without replacement, fix-up rounds, test mode, carry-forward | Example-sentence generation |
| Struggle focus | Per-item score, flags, drills, extra reps, trouble-spots panel |  |
| Parent view | Dashboard, attempt review with audio, overrides, thresholds | Printable flashcards, badges/rewards |
| TTS | Browser Web Speech API | Self-hosted Piper with cached MP3 |
| STT | Self-hosted faster-whisper | Vosk live word highlighting |

**Non-goals:** multi-household SaaS, billing, analytics, mobile native apps.

## Navigation and shared flows

1. **Home:** child picker. Tapping a profile opens that child's home.
2. **Child home:** two tabs, **Recite** and **Spelling**. Each tab lists that child's assignments with a status badge and due date, sorted by due date then status.
3. **Open an assignment:** the app resumes exactly where the child left off (current line n, or current spelling round and remaining words).
4. **Session end:** progress is saved on every attempt, so closing the browser loses nothing.
5. **Parent:** a lock icon on the child picker opens the parent login and admin area.

## Recite tab

### Passages

- Fields: title, source/reference (e.g. "Psalm 23", "Frost"), type (verse or poem), text, due date.
- Assign to one or more children. Progress is tracked per child per passage.
- Edit, archive, reorder. Editing the text of an in-progress passage re-sections it and resets status for affected lines only.

### Sectioning

- Auto-split: **line** (default for poems) or **verse number** (default for scripture). Sentence and stanza are also available.
- Manual override in the editor: merge or split sections.
- Chunk size per child: how many sections advance at once (default 1).

### Chain practice

1. **Learn section n:** TTS reads it, with the text hidden by default. A **peek** button shows it and is logged.
2. **Recite 1..n:** the child taps record and recites from the start through section n. STT grades it.
3. **Clean:** section n+1 unlocks.
4. **Errors:** missed or wrong words are highlighted, TTS replays the failed section, the child retries.
5. **n = N:** the passage enters **Full mode**: the whole passage, no text, no peek.
6. **Pass:** K consecutive clean full runs (default 3).

### Status per passage per child

`Not started` → `Partial (n/N)` → `Full in progress (k/K)` → `Passed`

A passage can move backward from Full to Partial if the parent resets it. Passed passages stay in the list, collapsed, and can be reopened for review.

## Speech-to-text and alignment

Grading is alignment against known text, not open transcription, which is why child speech is workable.

### Engine

- **faster-whisper** `small.en`, int8, CPU, in its own container.
- The expected passage is passed as `initial_prompt` to bias toward archaic words (thee, shalt, begat).
- Audio is recorded in the browser (MediaRecorder, webm/opus), posted to the API, transcribed with word timestamps. Target latency: under 8 s for a 30 s clip on a Hetzner CPU VPS.
- Browser Web Speech API is **not** used for STT: Chrome sends audio to Google and Firefox lacks it.

### Alignment and grading

1. Normalize both sides: lowercase, strip punctuation, expand common contractions, collapse whitespace.
2. Word-level alignment (Levenshtein over token sequences) between transcript and expected text.
3. A token counts as matched if it is exact, within edit distance 1 for words of 5+ letters, or in the homophone list.
4. Each miss is attributed to its expected **line**, which feeds the struggle score.
5. **Clean** = matched-word rate ≥ the pass threshold (default 100%).

### Overrides and audit

- Every attempt stores its transcript, missed words, and verdict. Audio is held in memory only for transcription and discarded; nothing is written to disk.
- The parent can mark any attempt as correct or incorrect; the override is recorded as such.
- Mic access requires HTTPS, which Coolify plus Let's Encrypt provides.

## Spelling tab

### Lists

- Fields: title (e.g. "Week 6"), words, due date, assigned children.
- Bulk add by pasting one word per line or comma-separated.
- Each word has an optional example sentence and a homophone flag. Homophones always get the sentence read.

### Delivery and input

- The app **speaks** the word, then the sentence (TTS). The word is never shown before the child answers.
- The child **types** the answer. STT is not used for spelling because letter-by-letter recognition is unreliable.
- The input field disables autocorrect, autocomplete, and spellcheck.

### Modes

1. **Learn:** word shown, TTS reads it, child copies it 2–3 times.
2. **Practice:** words drawn **randomly without replacement** until the list is complete, then a **fix-up round** of missed words (also without replacement) repeated until every one is correct. The next full round includes struggling words a second time, spaced apart.
3. **Test:** one strict pass in random order, no feedback until the end, scored against the list threshold.

### Status

- Per word: `New` → `Learning` → `Known` (2 consecutive correct in practice).
- Per list: `Not started` → `Practicing (x/N known)` → `Test passed`.
- Pass = test score ≥ threshold (default 100%, configurable per list).
- **Carry-forward:** words still struggling when a list is passed or archived become review words in the child's next list. Toggle, default on.

## Struggle focus

Every item (a spelling word or a passage line) carries a **struggle score**: an exponentially weighted miss rate (α = 0.5), so the last few attempts dominate and old misses fade.

- **Flag** when score ≥ 0.34 (roughly 1 miss in the last 3).
- **Clear** after 3 consecutive correct attempts.

### Recite drills

1. When a line is flagged, the chain pauses for a drill: recite that line alone, then the **transition** from the previous line into it, then resume at 1..n.
2. In Full mode, a failed run triggers a drill on the failed lines before the full run is retried.
3. Frequently missed words are underlined in the practice view.

### Spelling

- Flagged words appear twice in the next practice round, spaced apart.
- Flagged words drive the carry-forward list.

## Parent admin

- **Dashboard:** per child, each assignment with status, % complete, streak (days practiced), and due date.
- **Trouble spots:** top struggling words and lines per child with miss counts and trend.
- **Attempt review:** list of attempts with transcript, highlighted misses, and a correct/incorrect override.
- **Settings:** per child (chunk size, TTS speed) and per assignment (pass threshold, K clean runs, carry-forward).
- **Reset:** send a passage back to Partial or a list back to Not started.

## Data model

| Table | Key fields |
| --- | --- |
| `child` | id, name, avatar, chunk\_size, tts\_rate |
| `passage` | id, title, reference, type (verse/poem), text, due\_date, archived |
| `section` | id, passage\_id, ordinal, text |
| `passage_assignment` | id, passage\_id, child\_id, status, current\_section, clean\_full\_runs, pass\_threshold, k\_required |
| `recite_attempt` | id, assignment\_id, scope (partial n / full / drill), transcript, missed\_words (json), verdict, override, peeked, created\_at |
| `line_stat` | assignment\_id, section\_id, struggle\_score, consecutive\_correct, flagged |
| `spelling_list` | id, title, due\_date, pass\_threshold, carry\_forward, archived |
| `spelling_word` | id, list\_id, word, sentence, homophone |
| `list_assignment` | id, list\_id, child\_id, status, round\_no, remaining\_words (json) |
| `spelling_attempt` | id, assignment\_id, word\_id, mode, typed, correct, created\_at |
| `word_stat` | assignment\_id, word\_id, status, struggle\_score, consecutive\_correct, flagged |

No audio is persisted. The SQLite file is the only state to back up.

## Architecture

&#91;embedded content: deployment · 2 containers, 1 optional\]

The browser handles TTS and records audio; the app grades and schedules; Whisper only transcribes. Piper is dashed because it is a later addition.

### Deviations from the WAG standard

- **SQLite** instead of Postgres: one household, low write volume. Switch to Postgres only if multi-household ever happens.
- **faster-whisper sidecar** container (`docker-compose` service, internal network only).
- **No Stripe, no Plausible.**
- Persistent volume for `/data` (SQLite file) mounted via Coolify; B2 backup job added to the existing homelab backup schedule.
- Everything else (FastAPI, Jinja2, HTMX/Alpine, Tailwind, uv, ruff, pyright, pytest, multi-stage Docker) is standard.

## Configurable defaults

| Setting | Scope | Default |
| --- | --- | --- |
| Pass threshold (word match or test score) | per passage / per list | 100% |
| Clean full runs to pass (K) | per passage | 3 |
| Partial step pass | per passage | 1 clean run of 1..n |
| Chunk unit | per passage | line (poem), verse number (scripture) |
| Chunk size (sections unlocked per step) | per child | 1 |
| Text visible during partial practice | per child | hidden, peek allowed and logged |
| TTS rate | per child | 0.9 |
| Known word (spelling) | global | 2 consecutive correct |
| Struggle flag threshold | global | score ≥ 0.34 |
| Struggle clear | global | 3 consecutive correct |
| Struggle decay (α) | global | 0.5 |
| Carry-forward of struggling words | per list | on |
| Fuzzy match: edit distance 1 allowed | global | words of 5+ letters |

## Acceptance criteria (MVP)

- [ ] A parent can add a passage, assign it to two children, and each child sees it with independent status.
- [ ] A child can pick their profile with no password and resume an assignment at the exact section they left.
- [ ] Chain practice unlocks section n+1 only after a clean 1..n recitation, and enters Full mode at n = N.
- [ ] A passage is marked Passed only after K consecutive clean full runs.
- [ ] A 30 s recitation is graded in under 8 s with highlighted misses attributed to lines.
- [ ] A parent can read any attempt's transcript and override its verdict.
- [ ] A spelling list speaks each word once per round in random order without replacement, then runs fix-up rounds until all are correct.
- [ ] Test mode scores a list against its threshold and marks it passed.
- [ ] Struggling words and lines are flagged, drilled, and shown in the parent's Trouble spots panel.
- [ ] The app deploys on Coolify from the repo with a persistent `/data` volume and a working HTTPS mic prompt.

## Open questions

- [ ] Carry-forward of struggling spelling words: confirm default on.
- [ ] Whisper model size: start with `small.en` and move to `base.en` if latency on the VPS is too high?
- [ ] Should a peek during partial practice invalidate that step's clean run?
- [ ] Homophone list: hand-maintained seed file or generated per passage?
