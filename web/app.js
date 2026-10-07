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
    renderHelp();
    renderKey();
    renderModels();
    renderFile();
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
  function go(page) {
    state.page = page;
    $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.page === page));
    $$(".page").forEach(p => p.classList.toggle("active", p.id === "page-" + page));
    const job = JOB_PAGES.includes(page);
    $("#file-zone").classList.toggle("hidden", !job);
    $("#log-wrap").classList.toggle("hidden", !job);
    $("#result").classList.toggle("hidden", !(job && state.file && state.file.final));
    $(".main").scrollTop = 0;
  }
  $$(".nav-item").forEach(b => b.addEventListener("click", () => go(b.dataset.page)));

  // ---------- file ----------
  function renderFile() {
    const f = state.file;
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
    if (!handleError(res) && announce && res.file) toast(t("loaded", { name: res.file.name }), "success");
  }
  const pick = async () => { if (!api) return; setFileResult(await api.pick_file()); };
  $("#browse-btn").addEventListener("click", pick);
  $("#change-btn").addEventListener("click", pick);
  $("#drop").addEventListener("click", e => { if (e.target.id !== "browse-btn") pick(); });

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
      const v = Math.max(1, (parseInt(input.value, 10) || 0) + parseInt(b.dataset.step, 10));
      input.value = v; input.dispatchEvent(new Event("input"));
    }));
  });
  const syncChunk = (src, dst) => src.addEventListener("input", () => { dst.value = src.value; updateParts(); });
  syncChunk($("#chunk"), $("#chunk-split"));
  syncChunk($("#chunk-split"), $("#chunk"));

  // ---------- stages ----------
  function resetStages() {
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
    if (lvl === "info" && /^(attempt \d|retrying|flex |.* doesn't support thinking)/i.test(msg)) lvl = "warn";
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
    $$("#run-pipeline, #run-split, #run-translate, #run-merge").forEach(x => (x.disabled = b));
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

  window.onPyEvent = (e) => {
    switch (e.event) {
      case "started":
        state.job = e.job;
        setBusy(true);
        $("#result").classList.add("hidden");
        if (e.job === "pipeline") resetStages();
        if (e.job === "translate") { $("#trans-progress").style.width = "0%"; $("#trans-status").textContent = ""; }
        log(`▶ ${e.job}`, "head");
        break;
      case "log":
        log(e.message, e.level);
        break;
      case "stage":
        setStage(e.stage, e.status);
        if (state.job === "pipeline") {
          if (e.stage === "split" && e.status === "done") setProgress(0.1);
          if (e.stage === "translate" && e.status === "done") setProgress(0.9);
          if (e.stage === "merge" && e.status === "done") setProgress(1);
        }
        break;
      case "progress": {
        const txt = t("st_progress", { c: e.current, t: e.total });
        $("#st-translate-sub").textContent = txt;
        $("#trans-status").textContent = `${txt} · ${e.filename}`;
        $("#trans-progress").style.width = (e.current / e.total) * 100 + "%";
        if (state.job === "pipeline") setProgress(0.1 + 0.8 * (e.current / e.total));
        break;
      }
      case "done":
        setBusy(false);
        if (e.file) state.file = e.file;
        if (e.result && e.result.parts) $("#st-split-sub").textContent = t("st_parts", { n: e.result.parts });
        renderFile();
        toast(e.ok ? t("job_done") : t("job_failed"), e.ok ? "success" : "error");
        if (!e.ok) $$(".stage").forEach(s => { if (s.dataset.status === "active") setStage(s.dataset.stage, "error"); });
        state.job = null;
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
    renderModels();
  }
  // rough token estimate for a ~42 min episode (see README)
  const EP_IN = 20000, EP_OUT = 35000;
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
    if (chip && state.model) chip.textContent = `${prettyModel(state.model)} · ${t("think_" + (state.thinking || "low"))}` + (state.flex ? " · Flex" : "");
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
    applyTheme(); applyLang(); go("pipeline");
    if (!s.has_key) setTimeout(() => toast(t("err_no_key"), "warn"), 600);
  }
  applyTheme(); applyLang(); go("pipeline");
  if (window.pywebview && window.pywebview.api) boot();
  else window.addEventListener("pywebviewready", boot);
})();
