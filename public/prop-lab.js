(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const state = { loaded: false, loading: false, projections: new Map(), view: "props", request: 0 };
  const labels = { PTS: "Puntos", REB: "Rebotes", AST: "Asistencias", "3PM": "Triples", PRA: "PRA" };
  const projectionKeys = { PTS: "projected_pts", REB: "projected_reb", AST: "projected_ast", PRA: "projected_pra" };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const number = (value) => Number.isFinite(Number(value)) ? new Intl.NumberFormat("es-CL", { maximumFractionDigits: 1 }).format(Number(value)) : "—";
  const setStatus = (message, error = false) => {
    const node = $("#propLabStatus");
    if (!node) return;
    node.textContent = message;
    node.classList.toggle("is-error", error);
  };
  const getParams = () => {
    const params = new URLSearchParams({
      market: $("#propLabMarket")?.value || "PTS",
      window: $("#propLabWindow")?.value || "10",
      side: $("#propLabSide")?.value || "ALL",
      min_sample: "1",
      limit: "150"
    });
    const season = $("#propLabSeason")?.value;
    const line = $("#propLabLine")?.value;
    const search = $("#propLabSearch")?.value?.trim();
    if (season) params.set("season", season);
    if (line !== "" && Number.isFinite(Number(line))) params.set("line", line);
    if (search) params.set("q", search);
    return params;
  };
  async function loadProjections() {
    try {
      const response = await fetch("/api/player-props?days=7&limit=20");
      if (!response.ok) return;
      const data = await response.json();
      const map = new Map();
      (data.games || []).forEach((game) => (game.players || []).forEach((player) => {
        map.set(String(player.player_id), { ...player, game });
      }));
      state.projections = map;
    } catch (_) { /* El tablero de tendencias funciona sin el modelo predictivo. */ }
  }
  function renderCards(data) {
    const cards = $("#propLabCards");
    const summary = $("#propLabSummary");
    if (!cards) return;
    const market = data.market;
    const rows = data.players || [];
    const withLine = data.line !== null && data.line !== undefined;
    const projectionField = projectionKeys[market];
    summary.innerHTML = [
      ["Jugadores", rows.length],
      ["Mercado", labels[market] || market],
      ["Ventana", "L" + data.window],
      ["Línea", withLine ? number(data.line) : "Sin línea"]
    ].map(([label, value]) => '<div class="prop-lab-stat"><span>' + esc(label) + '</span><strong>' + esc(value) + '</strong></div>').join("");
    if (!rows.length) {
      cards.innerHTML = '<div class="prop-lab-empty">No hay registros para estos filtros. Prueba otra temporada, mercado o jugador.</div>';
      return;
    }
    cards.innerHTML = rows.map((player) => {
      const projection = state.projections.get(String(player.player_id));
      const projected = projection && projectionField ? Number(projection[projectionField]) : null;
      const edge = Number.isFinite(projected) && withLine ? projected - Number(data.line) : null;
      const hitCount = withLine ? (data.side === "UNDER" ? player.under : player.over) : null;
      const valid = withLine ? player.valid : player.sample;
      const rate = withLine ? (data.side === "UNDER" ? player.under_rate : player.over_rate) : null;
      const trendLabel = withLine ? (data.side === "UNDER" ? "UNDER" : data.side === "OVER" ? "OVER" : "OVER") : "PROMEDIO";
      const recent = (player.values || []).map((value) => '<span class="prop-lab-chip">' + esc(number(value)) + '</span>').join("");
      const statLine = withLine
        ? '<div class="prop-lab-hit"><strong>' + esc(hitCount ?? 0) + '/' + esc(valid ?? 0) + '</strong><span>' + trendLabel + (rate !== null ? ' · ' + Math.round(rate * 100) + '%' : '') + '</span></div>'
        : '<div class="prop-lab-hit"><strong>' + esc(number(player.average)) + '</strong><span>Promedio L' + esc(player.window) + '</span></div>';
      let projectionHtml = '<div class="prop-lab-projection"><span>Proyección NBA TOTALS</span><strong>' + (Number.isFinite(projected) ? esc(number(projected)) : '—') + '</strong></div>';
      if (market === "3PM") projectionHtml = '<div class="prop-lab-projection"><span>Proyección NBA TOTALS</span><strong>En desarrollo</strong></div>';
      const edgeHtml = edge === null ? "" : '<div class="prop-lab-edge ' + (edge >= 0 ? "positive" : "negative") + '"><span>Diferencia vs línea</span><strong>' + (edge > 0 ? "+" : "") + esc(number(edge)) + '</strong></div>';
      const date = player.last_game_date ? new Date(player.last_game_date + "T12:00:00").toLocaleDateString("es-CL") : "—";
      return '<article class="prop-lab-card">' +
        '<div class="prop-lab-card-top"><img src="' + esc(player.headshot_url || "") + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'"><div class="prop-lab-player"><strong>' + esc(player.player_name) + '</strong><span>' + esc(player.team) + ' · Último partido ' + esc(date) + '</span></div><span class="prop-lab-market">' + esc(market) + '</span></div>' +
        '<div class="prop-lab-card-main">' + statLine + '<div class="prop-lab-average"><span>Media</span><strong>' + esc(number(player.average)) + '</strong></div>' + projectionHtml + edgeHtml + '</div>' +
        '<div class="prop-lab-recent-label">ÚLTIMOS ' + esc(player.values?.length || 0) + ' PARTIDOS</div><div class="prop-lab-recent">' + recent + '</div>' +
        (withLine ? '<div class="prop-lab-line-note">Línea de referencia: ' + esc(number(data.line)) + ' · Pushes: ' + esc(player.push || 0) + '</div>' : '<div class="prop-lab-line-note">Introduce una línea para calcular frecuencias Over/Under.</div>') +
        '</article>';
    }).join("");
  }
  async function load() {
    if (state.loading) return;
    state.loading = true;
    const request = ++state.request;
    const button = $("#propLabRefresh");
    if (button) button.disabled = true;
    setStatus("Consultando historial de jugadores…");
    try {
      const response = await fetch("/api/prop-lab/trends?" + getParams().toString(), { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "No se pudieron cargar las tendencias.");
      if (request !== state.request) return;
      if (!state.projections.size) await loadProjections();
      renderCards(data);
      const lineText = data.line === null ? "sin línea de apuesta" : "línea " + number(data.line);
      setStatus(data.count + " jugadores · " + labels[data.market] + " · L" + data.window + " · " + lineText + ". " + data.note);
      state.loaded = true;
    } catch (error) {
      setStatus(error.message || "No se pudo cargar PROP LAB.", true);
      $("#propLabCards").innerHTML = '<div class="prop-lab-empty">El histórico no está disponible. Ejecuta la actualización de props y vuelve a intentarlo.</div>';
    } finally {
      state.loading = false;
      if (button) button.disabled = false;
    }
  }
  function setView(view) {
    state.view = view;
    const page = $(".prop-lab-page");
    if (page) page.dataset.view = view;
    document.querySelectorAll("[data-prop-view]").forEach((button) => {
      const active = button.dataset.propView === view;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    const disclaimer = document.querySelector(".prop-lab-disclaimer");
    if (disclaimer && view === "value") disclaimer.textContent = "Valor: no se calculan EV ni ventaja de cuota porque la fuente actual no proporciona cuotas de jugador. Solo se muestra la diferencia entre proyección y línea manual, que no equivale a valor esperado.";
    else if (disclaimer && view === "trends") disclaimer.textContent = "Tendencias históricas: frecuencia observada sobre partidos válidos. Los pushes se excluyen del denominador y la muestra se limita a la ventana elegida.";
    else if (disclaimer) disclaimer.textContent = "Props: proyecciones disponibles del modelo actual junto con el rendimiento reciente. No se generan líneas ni cuotas de apuestas.";
  }
  document.addEventListener("click", (event) => {
    const tab = event.target.closest('[data-tab="prop-lab"]');
    if (tab) load();
    const view = event.target.closest("[data-prop-view]");
    if (view) setView(view.dataset.propView);
  });
  ["propLabMarket", "propLabWindow", "propLabSide", "propLabSeason"].forEach((id) => $("#" + id)?.addEventListener("change", load));
  ["propLabLine", "propLabSearch"].forEach((id) => $("#" + id)?.addEventListener("change", load));
  $("#propLabRefresh")?.addEventListener("click", load);
  setView("props");
})();
