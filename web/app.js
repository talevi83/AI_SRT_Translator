(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  const state = { lang: "he", theme: "system", file: null, busy: false, page: "pipeline", job: null, hasKey: false };
  let api = null;

  // ---------- i18n ----------
  const t = (key, vars = {}) => {
    let s = (I18N[state.lang] || I18N.he)[key];
    if (s === undefined) s = I18N.he[key] ?? key;
    if (typeof s === "string") for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
    return s;
  };

  function applyLang() {
    const html = document.documentElement;
    html.lang = state.lang;
    html.dir = state.lang === "he" ? "rtl" : "ltr";
    $$("[data-i18n]").forEach(el => (el.textContent = t(el.dataset.i18n)));
    $$("#lang-switch button").forEach(b => b.classList.toggle("on", b.dataset.lang === state.lang));
    $("#api-key").placeholder = t("key_placeholder");
    $("#instructions").placeholder = t("instr_placeholder");
    renderHelp();
    renderKey();
    renderModels();
    renderLangs();
    renderFile();
    renderQA();
    renderQueue();
    document.title = t("brand");
  }

  // ---------- theme ----------
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme() {
    const dark = state.theme === "dark" || (state.theme === "system" && media.matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }
  media.addEventListener?.("change", applyTheme);

  function savePrefs() { api && api.save_ui_prefs(state.lang, state.theme); }

  // ---------- toasts ----------
  function toast(msg, kind = "info") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    $("#toasts").appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 300); }, 3800);
  }
  const handleError = (res) => {
    if (res && res.error) { toast(t("err_" + res.error), "error"); if (res.error === "no_key") go("settings"); return true; }
    return false;
  };

  // ---------- navigation ----------
  const JOB_PAGES = ["pipeline", "split", "translate", "merge"];
  const PROGRESS_JOBS = ["pipeline", "batch"];
  function go(page) {
    state.page = page;
    $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.page === page));
    $$(".page").forEach(p => p.classList.toggle("active", p.id === "page-" + page));
    const job = JOB_PAGES.includes(page);
    $("#file-zone").classList.toggle("hidden", !job);
    $("#log-wrap").classList.toggle("hidden", !job);
    $("#qa").classList.toggle("hidden", !(job && state.qa));
    $("#result").classList.toggle("hidden", !(job && state.file && state.file.final));
    $(".main").scrollTop = 0;
  }
  $$(".nav-item").forEach(b => b.addEventListener("click", () => go(b.dataset.page)));

  // ---------- file ----------
  function renderFile() {
    const f = state.file;
    renderLangs();
    $("#drop").classList.toggle("hidden", !!f);
    $("#file-card").classList.toggle("hidden", !f);
    if (!f) {
      ["#split-out", "#trans-in", "#trans-out", "#merge-in", "#merge-out"].forEach(s => ($(s).textContent = "–"));
      $("#trans-in-count").textContent = $("#merge-in-count").textContent = "";
      $("#result").classList.add("hidden");
      return;
    }
    $("#file-name").textContent = f.name;
    $("#file-path").textContent = f.path;
    $("#stat-blocks").textContent = f.blocks.toLocaleString();
    $("#stat-duration").textContent = f.duration || "–";
    updateParts();
    $("#split-out").textContent = f.dirs.split;
    $("#trans-in").textContent = f.dirs.split;
    $("#trans-out").textContent = f.dirs.merge;
    $("#merge-in").textContent = f.dirs.merge;
    $("#merge-out").textContent = f.dirs.output;
    const pill = (el, n) => { el.textContent = t("files_n", { n }); el.classList.toggle("zero", !n); };
    pill($("#trans-in-count"), f.split_count);
    pill($("#merge-in-count"), f.merge_count);
    if (f.final) {
      $("#result-path").textContent = f.final;
      $("#result").classList.toggle("hidden", !JOB_PAGES.includes(state.page));
    } else $("#result").classList.add("hidden");
  }
  function updateParts() {
    if (!state.file) return;
    const chunk = parseInt($("#chunk").value, 10);
    $("#stat-parts").textContent = chunk > 0 ? Math.ceil(state.file.blocks / chunk) : "–";
  }
  function setFileResult(res, announce = true) {
    if (!res) return;
    if (res.file) { state.file = res.file; resetStages(); renderFile(); }
    if (res.queue) { state.queue = res.queue; renderQueue(); }
    if (!handleError(res) && announce && res.file)
      toast(state.queue.length > 1 ? t("queue_title", { n: state.queue.length }) : t("loaded", { name: res.file.name }), "success");
  }
  const pick = async () => { if (!api) return; setFileResult(await api.pick_file()); };
  const pickFolder = async () => { if (!api) return; setFileResult(await api.pick_folder()); };
  $("#browse-btn").addEventListener("click", pick);
  $("#folder-btn").addEventListener("click", pickFolder);
  $("#change-btn").addEventListener("click", pick);
  $("#queue-add").addEventListener("click", pick);
  $("#drop").addEventListener("click", e => { if (!e.target.closest("button")) pick(); });

  // ---------- queue (several files) ----------
  state.queue = [];
  function renderQueue() {
    const q = state.queue || [];
    const many = q.length > 1;
    $("#queue").classList.toggle("hidden", !many);
    $("#run-pipeline span").textContent = many ? t("run_batch", { n: q.length }) : t("run_pipeline");
    if (!many) return;
    $("#queue-title").textContent = t("queue_title", { n: q.length });
    const list = $("#queue-list");
    list.innerHTML = "";
    q.forEach(item => {
      const row = document.createElement("div");
      row.className = "q-row" + (item.current ? " current" : "");
      row.innerHTML = `<span class="q-name" dir="auto"></span><span class="q-status"></span><button class="q-x" title="✕">✕</button>`;
      row.children[0].textContent = item.name;
      row.title = item.error || item.path;
      const status = item.status === "pending" && item.final ? "done" : item.status;
      row.children[1].className = "q-status " + status;
      row.children[1].textContent = t("q_" + status);
      row.addEventListener("click", async e => {
        if (state.busy || !api) return;
        const res = e.target.closest(".q-x") ? await api.remove_queue_item(item.path) : await api.select_queue_item(item.path);
        if (handleError(res)) return;
        if (res.file) { state.file = res.file; resetStages(); }
        else state.file = null;
        state.queue = res.queue || []; renderFile(); renderQueue();
      });
      list.appendChild(row);
    });
  }

  // drag & drop visual (the actual path is delivered by Python)
  let dragDepth = 0;
  document.addEventListener("dragenter", e => { e.preventDefault(); dragDepth++; document.body.classList.add("dragging"); });
  document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("dragging"); } });
  document.addEventListener("dragover", e => e.preventDefault());
  document.addEventListener("drop", e => { e.preventDefault(); dragDepth = 0; document.body.classList.remove("dragging"); });

  // ---------- steppers ----------
  $$(".stepper").forEach(st => {
    const input = $("input", st);
    $$("button", st).forEach(b => b.addEventListener("click", () => {
      const max = parseInt(input.max, 10) || Infinity;
      const v = Math.min(max, Math.max(1, (parseInt(input.value, 10) || 0) + parseInt(b.dataset.step, 10)));
      input.value = v; input.dispatchEvent(new Event("input"));
    }));
  });
  const syncChunk = (src, dst) => src.addEventListener("input", () => { dst.value = src.value; updateParts(); });
  syncChunk($("#chunk"), $("#chunk-split"));
  syncChunk($("#chunk-split"), $("#chunk"));

  // ---------- stages ----------
  // ---------- quality check ----------
  function renderQA() {
    const q = state.qa, box = $("#qa");
    box.classList.toggle("hidden", !q || !JOB_PAGES.includes(state.page));
    if (!q) return;
    box.classList.toggle("clean", !q.total);
    $("#qa-title").textContent = q.total ? t("qa_title", { n: q.total }) : t("qa_clean");
    const kinds = t("qa_kind");
    $("#qa-chips").innerHTML = "";
    Object.entries(q.counts || {}).forEach(([k, n]) => {
      const c = document.createElement("span");
      c.className = "qa-chip " + k; c.textContent = `${kinds[k] || k} · ${n}`;
      $("#qa-chips").appendChild(c);
    });
    const fix = $("#run-fix");
    fix.classList.toggle("hidden", !q.fixable);
    fix.textContent = t("qa_fix", { n: q.fixable });
    fix.title = t("qa_fix_note");
    const list = $("#qa-list");
    list.innerHTML = "";
    const details = t("qa_detail");
    q.issues.forEach(i => {
      const row = document.createElement("div");
      row.className = "qa-row";
      row.innerHTML = `<span class="qa-i" dir="ltr"></span><span class="qa-k"></span><span class="qa-t" dir="auto"></span>`;
      row.children[0].textContent = "#" + i.index;
      const d = details[i.kind] && i.detail ? " · " + details[i.kind].replace("{d}", i.detail) : "";
      row.children[1].textContent = (kinds[i.kind] || i.kind) + d;
      row.children[2].textContent = i.text;
      list.appendChild(row);
    });
    if (q.total > q.issues.length) {
      const more = document.createElement("div");
      more.className = "muted"; more.textContent = t("qa_more", { shown: q.issues.length, n: q.total });
      list.appendChild(more);
    }
  }
  $("#run-fix").addEventListener("click", () => startJob("run_fix"));

  function resetStages() {
    state.qa = null; renderQA();
    $$(".stage").forEach(s => (s.dataset.status = ""));
    ["split", "translate", "merge"].forEach(s => ($(`#st-${s}-sub`).textContent = ""));
    setProgress(0);
    $("#trans-progress").style.width = "0%";
    $("#trans-status").textContent = "";
  }
  function setStage(stage, status) {
    const el = $(`.stage[data-stage="${stage}"]`);
    if (el) el.dataset.status = status;
    if (status === "error") $(`#st-${stage}-sub`).textContent = t("st_failed");
  }
  function setProgress(p) { $("#pipe-progress").style.width = Math.round(p * 100) + "%"; }

  // ---------- log ----------
  function log(msg, level = "info") {
    const box = $("#log");
    $(".log-empty", box)?.remove();
    const line = document.createElement("div");
    let lvl = level;
    if (lvl === "info" && /^(attempt \d|retrying|flex |rate limited|warning|cancel|translation options changed)|doesn't support thinking|missing or untranslated|falling back/i.test(msg)) lvl = "warn";
    else if (lvl === "info" && /fail|error|mismatch|skipping|aborted/i.test(msg)) lvl = "error";
    if (lvl === "info" && /success|complete/i.test(msg)) lvl = "success";
    line.className = "log-line " + lvl;
    const time = new Date().toLocaleTimeString("en-GB");
    line.innerHTML = `<span class="log-t">${time}</span>`;
    const span = document.createElement("span");
    span.textContent = msg; span.dir = "auto";
    line.appendChild(span);
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }
  $("#clear-log").addEventListener("click", () => { $("#log").innerHTML = `<div class="log-empty" dir="auto">${t("log_empty")}</div>`; });

  // ---------- jobs ----------
  function setBusy(b) {
    state.busy = b;
    document.body.classList.toggle("busy", b);
    $$("#run-pipeline, #run-split, #run-translate, #run-merge, #run-fix").forEach(x => (x.disabled = b));
  }
  async function startJob(fn, ...args) {
    if (!api) return;
    if (!state.file) { toast(t("err_no_file"), "error"); return; }
    const res = await api[fn](...args);
    handleError(res);
  }
  $("#run-pipeline").addEventListener("click", () => startJob("run_pipeline", $("#chunk").value));
  $("#run-split").addEventListener("click", () => startJob("run_split", $("#chunk-split").value));
  $("#run-translate").addEventListener("click", () => startJob("run_translate"));
  $("#run-merge").addEventListener("click", () => startJob("run_merge"));
  $$(".cancel-btn").forEach(b => b.addEventListener("click", () => api && api.cancel_job()));

  function renderUsage(u) {
    const chip = $("#usage-chip");
    if (!u || !u.calls) { chip.classList.add("hidden"); return; }
    const tokens = (u.input + u.output + u.thinking).toLocaleString("en-US");
    chip.textContent = t("usage_line", { cost: "$" + u.cost.toFixed(u.cost < 0.1 ? 4 : 2), tokens });
    chip.classList.remove("hidden");
  }

  window.onPyEvent = (e) => {
    switch (e.event) {
      case "started":
        state.job = e.job;
        setBusy(true);
        $("#result").classList.add("hidden");
        if (e.job === "pipeline" || e.job === "batch") resetStages();
        if (e.job !== "fix" && e.job !== "pipeline") { state.qa = null; renderQA(); }
        if (e.job === "translate") { $("#trans-progress").style.width = "0%"; $("#trans-status").textContent = ""; }
        renderUsage(null);
        log(`▶ ${e.job}`, "head");
        break;
      case "log":
        log(e.message, e.level);
        break;
      case "stage":
        setStage(e.stage, e.status);
        if (PROGRESS_JOBS.includes(state.job)) {
          if (e.stage === "split" && e.status === "done") setProgress(0.1);
          if (e.stage === "translate" && e.status === "done") setProgress(0.9);
          if (e.stage === "merge" && e.status === "done") setProgress(1);
        }
        break;
      case "usage":
        renderUsage(e);
        break;
      case "qa":
        state.qa = e; renderQA();
        break;
      case "progress": {
        const txt = t("st_progress", { c: e.current, t: e.total });
        $("#st-translate-sub").textContent = txt;
        $("#trans-status").textContent = e.filename ? `${txt} · ${e.filename}` : txt;
        $("#trans-progress").style.width = (e.current / e.total) * 100 + "%";
        if (PROGRESS_JOBS.includes(state.job)) setProgress(0.1 + 0.8 * (e.current / e.total));
        break;
      }
      case "done":
        setBusy(false);
        if (e.file) state.file = e.file;
        if (e.queue) { state.queue = e.queue; renderQueue(); }
        if (e.result && e.result.parts) $("#st-split-sub").textContent = t("st_parts", { n: e.result.parts });
        renderFile();
        if (e.cancelled) toast(t("cancelled"), "warn");
        else toast(e.ok ? t("job_done") : t("job_failed"), e.ok ? "success" : "error");
        if (!e.ok) $$(".stage").forEach(s => { if (s.dataset.status === "active") setStage(s.dataset.stage, "error"); });
        state.job = null;
        break;
      case "batch":
        state.file = e.file; state.queue = e.queue;
        resetStages(); renderFile(); renderQueue();
        $("#st-split-sub").textContent = "";
        toast(t("batch_progress", { c: e.current, t: e.total, name: e.name }), "info");
        break;
      case "queue":
        state.queue = e.queue; renderQueue();
        break;
      case "file_dropped":
        setFileResult(e.result);
        if (!JOB_PAGES.includes(state.page)) go("pipeline");
        break;
    }
  };

  // ---------- result ----------
  $("#open-file").addEventListener("click", () => state.file?.final && api.open_path(state.file.final));
  $("#open-folder").addEventListener("click", () => state.file && api.open_path(state.file.dirs.output));

  // ---------- settings ----------
  function renderKey() {
    const el = $("#key-status");
    el.className = "key-status " + (state.hasKey ? "ok" : "missing");
    el.textContent = state.hasKey ? t("key_ok", { hint: state.keyHint ? "\u2066" + state.keyHint + "\u2069" : "" }) : t("key_missing");
    $("#key-dot").classList.toggle("hidden", state.hasKey);
  }
  function applyState(s) {
    state.hasKey = s.has_key; state.keyHint = s.key_hint;
    $("#set-split").value = s.settings.split; $("#set-split").placeholder = s.defaults.split;
    $("#set-merge").value = s.settings.merge; $("#set-merge").placeholder = s.defaults.merge;
    $("#set-output").value = s.settings.output; $("#set-output").placeholder = s.defaults.output;
    renderKey();
    renderThinking(s.thinking);
    state.models = s.models || []; state.model = s.model; state.thinking = s.thinking; state.flex = !!s.flex;
    $("#flex-toggle").checked = state.flex;
    if (s.workers) $("#workers").value = s.workers;
    $("#glossary-toggle").checked = !!s.auto_glossary;
    if (document.activeElement !== $("#instructions")) $("#instructions").value = s.instructions || "";
    state.langs = s.languages || []; state.source = s.source_lang; state.target = s.target_lang;
    state.naming = s.naming;
    $("#units-toggle").checked = !!s.convert_units;
    $("#bom-toggle").checked = !!s.bom;
    renderModels();
    renderLangs();
  }
  const langName = code => {
    try { return new Intl.DisplayNames([state.lang], { type: "language" }).of(code); } catch { return code; }
  };
  function renderLangs() {
    if (!state.langs) return;
    const fill = (sel, codes, value, auto) => {
      sel.innerHTML = "";
      if (auto) sel.add(new Option(t("lang_auto"), "auto"));
      codes.map(c => [c, langName(c)]).sort((a, b) => a[1].localeCompare(b[1], state.lang))
        .forEach(([c, n]) => sel.add(new Option(n, c)));
      sel.value = value;
    };
    fill($("#source-lang"), state.langs, state.source || "auto", true);
    fill($("#target-lang"), state.langs, state.target || "he", false);
    $$("#naming-choice button").forEach(b => b.classList.toggle("on", b.dataset.naming === state.naming));
    const base = state.file ? state.file.name.replace(/\.srt$/i, "") : "Movie";
    $("#naming-lang-example").textContent = `${base}.${state.target || "he"}.srt`;
    $("#naming-suffix-example").textContent = `${base}_translated.srt`;
  }
  async function saveTranslationPrefs(patch = {}) {
    if (!api) return;
    const res = await api.save_translation_prefs({
      source_lang: $("#source-lang").value, target_lang: $("#target-lang").value,
      convert_units: $("#units-toggle").checked, naming: state.naming, bom: $("#bom-toggle").checked, ...patch,
    });
    if (handleError(res)) { renderLangs(); return; }
    applyState(res);
    if (state.file) { state.file = await api.refresh_file(); renderFile(); }
    toast(t("saved"), "success");
  }
  ["#source-lang", "#target-lang", "#units-toggle", "#bom-toggle"].forEach(s => $(s).addEventListener("change", () => saveTranslationPrefs()));
  $$("#naming-choice button").forEach(b => b.addEventListener("click", () => saveTranslationPrefs({ naming: b.dataset.naming })));
  // rough token estimate for a ~42 min episode (see README)
  // Only subtitle text goes to the model, so output is mostly the translated text itself
  const EP_IN = 20000, EP_OUT = 20000;
  const prettyModel = id => id.replace(/^gemini-/, "Gemini ").replace(/-flash-lite$/, " Flash-Lite").replace(/-flash$/, " Flash");
  function renderModels() {
    const box = $("#model-choice");
    if (!box || !state.models) return;
    box.innerHTML = "";
    state.models.forEach(m => {
      const cost = (EP_IN * m.input + EP_OUT * m.output) / 1e6 * (state.flex ? 0.5 : 1);
      const b = document.createElement("button");
      b.dataset.model = m.id;
      b.classList.toggle("on", m.id === state.model);
      b.innerHTML = `<span class="choice-t" dir="ltr"></span><span class="choice-d"></span><span class="choice-p" dir="ltr"></span><span class="choice-c"></span>`;
      $(".choice-t", b).textContent = prettyModel(m.id);
      $(".choice-d", b).textContent = (t("model_desc") || {})[m.id] || "";
      const k = state.flex ? 0.5 : 1;
      $(".choice-p", b).textContent = `$${+(m.input * k).toFixed(3)} in · $${+(m.output * k).toFixed(3)} out` + (state.flex ? " · Flex" : "");
      $(".choice-c", b).textContent = t("per_episode", { cost: "$" + cost.toFixed(2) });
      b.addEventListener("click", async () => {
        if (!api) return;
        const res = await api.save_model(m.id);
        if (handleError(res)) return;
        applyState(res); toast(t("saved"), "success");
      });
      box.appendChild(b);
    });
    const chip = $("#model-chip");
    const pair = `${(state.source && state.source !== "auto" ? state.source : "auto").toUpperCase()} → ${(state.target || "he").toUpperCase()}`;
    if (chip && state.model) chip.textContent = `${pair} · ${prettyModel(state.model)} · ${t("think_" + (state.thinking || "low"))}` + (state.flex ? " · Flex" : "");
  }
  function renderThinking(level) {
    $$("#thinking-choice button").forEach(b => b.classList.toggle("on", b.dataset.level === level));
  }
  $$("#thinking-choice button").forEach(b => b.addEventListener("click", async () => {
    if (!api) return;
    const res = await api.save_thinking(b.dataset.level);
    if (handleError(res)) return;
    applyState(res); toast(t("saved"), "success");
  }));
  $("#save-key").addEventListener("click", async () => {
    const res = await api.save_api_key($("#api-key").value);
    if (handleError(res)) return;
    $("#api-key").value = ""; applyState(res); toast(t("saved"), "success");
  });
  $("#api-key").addEventListener("keydown", e => { if (e.key === "Enter") $("#save-key").click(); });
  $("#save-dirs").addEventListener("click", async () => {
    const res = await api.save_settings({ split: $("#set-split").value, merge: $("#set-merge").value, output: $("#set-output").value });
    applyState(res);
    if (state.file) { state.file = await api.refresh_file(); renderFile(); }
    toast(t("saved"), "success");
  });
  $("#flex-toggle").addEventListener("change", async e => {
    if (!api) return;
    const res = await api.save_flex(e.target.checked);
    applyState(res); toast(t(res.flex ? "flex_on" : "flex_off"), "success");
  });
  $("#glossary-toggle").addEventListener("change", async e => {
    if (!api) return;
    const res = await api.save_auto_glossary(e.target.checked);
    applyState(res); toast(t(res.auto_glossary ? "glossary_on" : "glossary_off"), "success");
  });
  $("#save-instructions").addEventListener("click", async () => {
    if (!api) return;
    applyState(await api.save_instructions($("#instructions").value)); toast(t("saved"), "success");
  });
  let workersTimer = null;
  $("#workers").addEventListener("input", () => {
    clearTimeout(workersTimer);
    workersTimer = setTimeout(async () => {
      if (!api) return;
      const res = await api.save_workers($("#workers").value);
      if (handleError(res)) return;
      applyState(res); toast(t("saved"), "success");
    }, 600);
  });
  $("#model-chip").addEventListener("click", () => go("settings"));
  $("#get-key-link").addEventListener("click", e => { e.preventDefault(); go("help"); });

  // ---------- help ----------
  function renderHelp() {
    const box = $("#help-steps");
    box.innerHTML = "";
    t("help_steps").forEach(([title, body, cta, href], i) => {
      const card = document.createElement("div");
      card.className = "help-step card";
      card.innerHTML = `<div class="help-num">${i + 1}</div><div class="help-body"><h3></h3><p></p><button class="btn ghost sm"></button></div>`;
      $("h3", card).textContent = title; $("p", card).textContent = body;
      const b = $("button", card); b.textContent = cta;
      b.addEventListener("click", () => href.startsWith("#") ? go(href.slice(1)) : api && api.open_url(href));
      box.appendChild(card);
    });
  }

  // ---------- language / theme controls ----------
  $$("#lang-switch button").forEach(b => b.addEventListener("click", () => { state.lang = b.dataset.lang; applyLang(); savePrefs(); }));
  $("#theme-btn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme === "dark";
    state.theme = dark ? "light" : "dark"; applyTheme(); savePrefs();
  });

  // ---------- boot ----------
  async function boot() {
    api = window.pywebview.api;
    const s = await api.get_state();
    state.lang = s.lang; state.theme = s.theme;
    if (s.chunk) $("#chunk").value = $("#chunk-split").value = s.chunk;
    applyState(s);
    if (s.file) state.file = s.file;
    state.queue = s.queue || [];
    applyTheme(); applyLang(); go("pipeline");
    if (!s.has_key) setTimeout(() => toast(t("err_no_key"), "warn"), 600);
  }
  applyTheme(); applyLang(); go("pipeline");
  if (window.pywebview && window.pywebview.api) boot();
  else window.addEventListener("pywebviewready", boot);
})();
