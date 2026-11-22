(() => {
  const $ = (selector) => document.querySelector(selector);
  const teams = ["ATL","BOS","BKN","CHA","CHI","CLE","DAL","DEN","DET","GSW","HOU","IND","LAC","LAL","MEM","MIA","MIL","MIN","NOP","NYK","OKC","ORL","PHI","PHX","POR","SAC","SAS","TOR","UTA","WAS"];
  const teamOptions = teams.map((team) => `<option value="${team}">${team}</option>`).join("");
  let activeView = "lab";
  let liveMode = "automatic";
  let liveTimer = null;
  let liveGames = [];

  async function request(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `Error HTTP ${response.status}`);
    return payload;
  }
  function notice(target, message, isError = false) {
    const node = $(target);
    if (!node) return;
    node.hidden = !message;
    node.textContent = message || "";
    node.classList.toggle("is-error", Boolean(isError));
  }
  function number(value, digits = 1) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : "—";
  }
  function probability(value) {
    return value === null || value === undefined ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
  }
  function initLab() {
    $("#labHome").innerHTML = teamOptions;
    $("#labAway").innerHTML = teamOptions;
    $("#labHome").value = "BOS";
    $("#labAway").value = "LAL";
    const now = new Date();
    const localDate = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    $("#labDate").value = localDate;
    const pairs = [
      ["#labPace", "#labPaceValue", "%"],
      ["#labHomeEff", "#labHomeEffValue", ""],
      ["#labAwayEff", "#labAwayEffValue", ""],
      ["#labHomeMinutes", "#labHomeMinutesValue", " min"],
      ["#labHomePpm", "#labHomePpmValue", ""],
      ["#labAwayMinutes", "#labAwayMinutesValue", " min"],
      ["#labAwayPpm", "#labAwayPpmValue", ""],
    ];
    pairs.forEach(([input, output, suffix]) => {
      $(input)?.addEventListener("input", () => { $(output).textContent = $(input).value + suffix; });
    });
  }
  function renderLab(data) {
    document.dispatchEvent(new CustomEvent("nba:lab-result", { detail: data }));
    const base = data.baseline || {};
    const scenario = data.scenario || {};
    $("#labResult").innerHTML = `
      <div class="lab-result-head"><div><span class="eyebrow">RESULTADO DEL ESCENARIO</span><h3>${data.matchup.away} @ ${data.matchup.home}</h3><small>${data.matchup.date} · ${data.model_name || "Modelo actual"}</small></div><span class="lab-delta">${Number(data.changes.total) >= 0 ? "+" : ""}${number(data.changes.total)} pts</span></div>
      <div class="lab-compare">
        <article><small>BASELINE ML</small><strong>${number(base.total)}</strong><span>Total proyectado</span><div>${number(base.home_score)} – ${number(base.away_score)}</div></article>
        <article class="is-simulated"><small>ESCENARIO SIMULADO</small><strong>${number(scenario.total)}</strong><span>Total proyectado</span><div>${number(scenario.home_score)} – ${number(scenario.away_score)}</div></article>
      </div>
      <div class="lab-metrics">
        <div><small>P(over)</small><b>${probability(scenario.probability_over)}</b></div>
        <div><small>P(victoria local)</small><b>${probability(scenario.home_win_probability)}</b></div>
        <div><small>Margen local</small><b>${number(scenario.margin, 2)}</b></div>
      </div>
      <p class="lab-method">${data.methodology || ""}</p>
      <details><summary>Ver parámetros utilizados</summary><pre>${JSON.stringify(data.scenario_inputs, null, 2)}</pre></details>`;
  }
  async function runLab(event) {
    event.preventDefault();
    if ($("#labHome").value === $("#labAway").value) {
      notice("#labNotice", "Selecciona dos equipos distintos.", true);
      return;
    }
    const payload = {
      home: $("#labHome").value,
      away: $("#labAway").value,
      game_date: $("#labDate").value,
      line: $("#labLine").value ? Number($("#labLine").value) : null,
      pace_delta_pct: Number($("#labPace").value),
      home_off_rating_delta: Number($("#labHomeEff").value),
      away_off_rating_delta: Number($("#labAwayEff").value),
      home_minutes_delta: Number($("#labHomeMinutes").value),
      away_minutes_delta: Number($("#labAwayMinutes").value),
      home_points_per_minute: Number($("#labHomePpm").value),
      away_points_per_minute: Number($("#labAwayPpm").value),
    };
    const button = $("#labRun");
    button.disabled = true;
    button.textContent = "Calculando…";
    notice("#labNotice", "");
    try {
      renderLab(await request("/api/lab/simulate", { method: "POST", body: JSON.stringify(payload) }));
    } catch (error) {
      notice("#labNotice", error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "Simular escenario";
    }
  }
  function setLiveMode(mode) {
    liveMode = mode;
    $$("[data-live-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.liveMode === mode));
    $("#liveAutomatic").hidden = mode !== "automatic";
    $("#liveManualForm").hidden = mode !== "manual";
    if (mode === "manual") stopLiveTimer(); else startLiveTimer();
  }
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  async function loadLiveGames() {
    notice("#liveNotice", "");
    try {
      const data = await request("/api/live/games");
      liveGames = data.games || [];
      const select = $("#liveGameSelect");
      const previous = select.value;
      select.innerHTML = liveGames.length
        ? liveGames.map((game) => `<option value="${game.event_id}">${game.away_team} @ ${game.home_team} · ${game.status} · ${game.away_score}-${game.home_score}</option>`).join("")
        : '<option value="">No hay partidos en vivo en este momento</option>';
      if (liveGames.some((game) => game.event_id === previous)) select.value = previous;
      if (data.warning) notice("#liveNotice", data.warning, true);
      return liveGames;
    } catch (error) {
      notice("#liveNotice", error.message, true);
      return [];
    }
  }
  function renderLive(data) {
    document.dispatchEvent(new CustomEvent("nba:live-result", { detail: data }));
    const score = data.score || {};
    const progress = Math.min(100, Math.max(0, Number(data.live_weight || 0) * 100 / 0.85));
    $("#liveResult").innerHTML = `
      <div class="live-result-top"><div><span class="eyebrow">LIVE TOTAL PROJECTION</span><h3>${data.away_team} @ ${data.home_team}</h3><small>${data.period ? `Q${data.period}` : ""} · ${data.clock || "Reloj manual"} · Fuente: ${data.source || "manual"}</small></div><span class="live-current-total">${number(data.current_total, 0)}<small>PTS ACTUALES</small></span></div>
      <div class="live-projection"><small>TOTAL FINAL PROYECTADO</small><strong>${number(data.projected_total)}</strong><span>Proyección de ritmo: ${number(data.pace_projection)}</span></div>
      <div class="live-scoreline"><span>${data.home_team} <b>${score.home ?? "—"}</b></span><span>${data.away_team} <b>${score.away ?? "—"}</b></span></div>
      <div class="live-weight"><div><span>Influencia del ritmo en vivo</span><b>${Math.round(Number(data.live_weight || 0) * 100)}%</b></div><div class="live-track"><i style="width:${progress}%"></i></div></div>
      <p class="lab-method">${data.methodology || ""}</p>`;
  }
  async function runAutomatic() {
    const eventId = $("#liveGameSelect").value;
    if (!eventId) {
      notice("#liveNotice", "No hay un partido seleccionado. Puedes utilizar el modo manual.", true);
      return;
    }
    try {
      const total = Number($("#livePregameTotal").value || 225.5);
      const data = await request(`/api/live/predict?event_id=${encodeURIComponent(eventId)}&pregame_total=${encodeURIComponent(total)}`);
      renderLive(data);
      notice("#liveNotice", "");
    } catch (error) {
      notice("#liveNotice", error.message, true);
    }
  }
  async function runManual(event) {
    event.preventDefault();
    const payload = {
      home_team: $("#liveHomeTeam").value.trim().toUpperCase(),
      away_team: $("#liveAwayTeam").value.trim().toUpperCase(),
      home_score: Number($("#liveHomeScore").value),
      away_score: Number($("#liveAwayScore").value),
      period: Number($("#livePeriod").value),
      clock: $("#liveClock").value.trim(),
      pregame_total: Number($("#liveManualPregame").value),
    };
    try {
      renderLive(await request("/api/live/predict/manual", { method: "POST", body: JSON.stringify(payload) }));
      notice("#liveNotice", "");
    } catch (error) {
      notice("#liveNotice", error.message, true);
    }
  }
  function stopLiveTimer() {
    if (liveTimer) window.clearInterval(liveTimer);
    liveTimer = null;
  }
  function startLiveTimer() {
    stopLiveTimer();
    if (liveMode !== "automatic") return;
    liveTimer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !$("#tab-lab-live").hidden && $("#liveAutoRefresh").checked) {
        runAutomatic();
      }
    }, 15000);
  }
  async function activate() {
    if (activeView === "live") {
      await loadLiveGames();
      if (liveMode === "automatic" && $("#liveGameSelect").value) await runAutomatic();
      startLiveTimer();
    }
  }
  document.addEventListener("DOMContentLoaded", () => {
    if (!$("#tab-lab-live")) return;
    initLab();
    $("#labScenarioForm").addEventListener("submit", runLab);
    $$("[data-lab-view]").forEach((button) => button.addEventListener("click", () => {
      activeView = button.dataset.labView;
      $$("[data-lab-view]").forEach((item) => {
        const active = item === button;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-selected", String(active));
      });
      $("#labView").hidden = activeView !== "lab";
      $("#liveView").hidden = activeView !== "live";
      activate();
    }));
    $$("[data-live-mode]").forEach((button) => button.addEventListener("click", () => setLiveMode(button.dataset.liveMode)));
    $("#liveRefresh").addEventListener("click", async () => { await loadLiveGames(); if ($("#liveGameSelect").value) await runAutomatic(); });
    $("#liveRunAutomatic").addEventListener("click", runAutomatic);
    $("#liveGameSelect").addEventListener("change", runAutomatic);
    $("#liveAutoRefresh").addEventListener("change", () => { if ($("#liveAutoRefresh").checked) startLiveTimer(); else stopLiveTimer(); });
    $("#liveManualForm").addEventListener("submit", runManual);
    $("#tab-btn-lab-live").addEventListener("click", () => { activeView = "lab"; activate(); });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && !$("#tab-lab-live").hidden && activeView === "live") activate();
    });
  });
  window.addEventListener("beforeunload", stopLiveTimer);
})();
