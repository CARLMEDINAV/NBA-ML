(function () {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const STORAGE_KEY = "nbaTotalsBetBuilder.v1";
  const MAX_SELECTIONS = 20;
  const nf1 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
  const nf2 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2 });
  const pf = new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 1 });

  const state = {
    initialized: false,
    loaded: false,
    loading: false,
    loadedAt: 0,
    events: [],
    meta: null,
    filters: { date: "", days: 1, query: "", market: "all" },
    lines: {},
    ticket: [],
    analysis: null,
    analysisVisible: false,
    analyzing: false,
    error: null,
    revision: 0,
    requestId: 0,
  };
  let analysisTimer = null;

  const MARKET_LABELS = {
    total: "Total del partido",
    winner: "Ganador",
    handicap: "Hándicap",
    team_total_home: "Total local",
    team_total_away: "Total visitante",
  };

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function num(value, digits = 1) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    return (digits === 0 ? new Intl.NumberFormat("es-ES") : digits === 2 ? nf2 : nf1).format(
      Number(value),
    );
  }

  function pct(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    return pf.format(Number(value));
  }

  function signedPct(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    const number = Number(value) * 100;
    return `${number >= 0 ? "+" : ""}${nf1.format(number)}%`;
  }

  function oddsText(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    return nf2.format(Number(value));
  }

  function lineText(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "";
    return nf1.format(Number(value));
  }

  function shortDate(value) {
    if (!value) return "—";
    const date = new Date(`${value}T12:00:00`);
    return date.toLocaleDateString("es-ES", {
      weekday: "short",
      day: "2-digit",
      month: "short",
    });
  }

  function toast(message) {
    if (typeof window.showToast === "function") {
      window.showToast(message);
      return;
    }
    const element = $("#toast");
    if (!element) return;
    element.textContent = message;
    element.hidden = false;
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => {
      element.hidden = true;
    }, 4200);
  }

  function setNotice(element, message, isError = false) {
    if (!element) return;
    if (!message) {
      element.hidden = true;
      element.textContent = "";
      element.classList.remove("error");
      return;
    }
    element.hidden = false;
    element.textContent = message;
    element.classList.toggle("error", isError);
  }

  function selectionKey(selection) {
    const line =
      selection.line === null || selection.line === undefined
        ? ""
        : Number(selection.line).toFixed(2);
    return `${selection.event_id}:${selection.market}:${selection.side}:${line}`;
  }

  function sameLine(left, right) {
    if (left === null || left === undefined || right === null || right === undefined) {
      return left === right;
    }
    return Math.abs(Number(left) - Number(right)) < 1e-9;
  }

  function lineLimits(marketId) {
    if (marketId === "total") return [100, 350];
    if (String(marketId).startsWith("team_total")) return [40, 220];
    return [-80, 80];
  }

  function parsedLine(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function lineIsValid(marketId, line) {
    if (line === null || line === undefined) return false;
    const [low, high] = lineLimits(marketId);
    return Number.isFinite(Number(line)) && Number(line) >= low && Number(line) <= high;
  }

  function lineSourceLabel(selection) {
    if (selection.is_custom_line || selection.line_source === "custom") {
      return "línea personalizada";
    }
    if (selection.line_source === "market") return "línea de mercado";
    return "modelo";
  }

  function marketLabel(marketId) {
    return MARKET_LABELS[marketId] || marketId;
  }

  function sideLabel(selection) {
    if (selection.side_label) return selection.side_label;
    if (selection.side === "OVER") return "Over";
    if (selection.side === "UNDER") return "Under";
    if (selection.side === "HOME") return "Local";
    if (selection.side === "AWAY") return "Visitante";
    return selection.side;
  }

  function selectionTitle(selection) {
    const line = lineText(selection.line);
    const side = sideLabel(selection);
    return `${selection.market_label || marketLabel(selection.market)} · ${side}${
      line ? ` ${line}` : ""
    }`;
  }

  function statusLabel(status) {
    if (status === "ok") return "Lista";
    if (status === "pending") return "Calculando";
    if (status === "stale") return "Revisar";
    if (status === "invalid") return "No válida";
    return status || "—";
  }

  function jointStatusLabel(status) {
    if (status === "independence") return "Independencia entre partidos";
    if (status === "same_game_dependency") return "Dependencia no modelada";
    if (status === "invalid") return "Selecciones no válidas";
    if (status === "empty") return "Ticket vacío";
    return status || "—";
  }

  function oddsSourceLabel(source) {
    if (source === "user") return "introducida por ti";
    if (source === "market") return "combinada de mercado";
    return "sin cuota";
  }

  function ageText(days) {
    if (days === null || days === undefined) return "";
    return `${days} ${Number(days) === 1 ? "día" : "días"}`;
  }

  function statusTag(status) {
    if (status === "ok") return '<span class="bb-tag ok">Lista</span>';
    if (status === "pending") return '<span class="bb-tag muted">Calculando</span>';
    if (status === "stale") return '<span class="bb-tag warn">Revisar</span>';
    return '<span class="bb-tag bad">No válida</span>';
  }

  function summaryStat(label, value) {
    return `<span class="bb-stat">${esc(label)} <strong>${esc(value)}</strong></span>`;
  }

  function metric(label, value, sub = "", className = "") {
    return `<div class="bb-metric ${className}">
      <span>${esc(label)}</span>
      <strong>${esc(value)}</strong>
      ${sub ? `<small>${esc(sub)}</small>` : ""}
    </div>`;
  }

  function saveTicket() {
    try {
      const payload = {
        version: 1,
        saved_at: new Date().toISOString(),
        selections: state.ticket,
        stake: $("#bbStake")?.value || "10",
        odds: $("#bbOdds")?.value || "",
      };
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch (error) {
      // localStorage puede no estar disponible en modo privado; el ticket sigue en memoria.
    }
  }

  function restoreTicket() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (!saved || !Array.isArray(saved.selections)) return;
      state.ticket = saved.selections
        .filter(
          (item) =>
            item &&
            item.event_id &&
            item.market &&
            item.side &&
            (item.line === null || item.line === undefined || Number.isFinite(Number(item.line))),
        )
        .slice(0, MAX_SELECTIONS)
        .map((item) => ({
          ...item,
          line: item.line === null || item.line === undefined ? null : Number(item.line),
          probability: null,
          decimal_odds: null,
          american_odds: null,
          status: "pending",
          stale_reason: "Pendiente de revalidar con los datos actuales.",
        }));
      const stake = $("#bbStake");
      if (stake && saved.stake) stake.value = String(saved.stake);
      const odds = $("#bbOdds");
      if (odds && saved.odds) odds.value = String(saved.odds);
    } catch (error) {
      state.ticket = [];
    }
  }

  function bind() {
    $("#bbRefresh")?.addEventListener("click", () => loadEvents(true));
    $("#bbDate")?.addEventListener("change", (event) => {
      state.filters.date = event.target.value;
      loadEvents(true);
    });
    $("#bbDays")?.addEventListener("change", (event) => {
      state.filters.days = Number(event.target.value) || 1;
      loadEvents(true);
    });
    $("#bbSearch")?.addEventListener("input", (event) => {
      state.filters.query = event.target.value.trim();
      renderEvents();
    });
    $("#bbMarketFilter")?.addEventListener("change", (event) => {
      state.filters.market = event.target.value;
      renderEvents();
    });
    $("#bbClear")?.addEventListener("click", clearTicket);
    $("#bbMobileTicket")?.addEventListener("click", () => {
      $(".bb-slip")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $("#bbAnalyze")?.addEventListener("click", () => analyzeTicket({ showReport: true }));
    $("#bbCopy")?.addEventListener("click", copySummary);
    $("#bbExport")?.addEventListener("click", exportTicket);
    $("#bbOdds")?.addEventListener("input", () => {
      state.revision += 1;
      invalidateAnalysis();
      saveTicket();
      renderSummary();
      renderAnalysis();
      scheduleAnalysis({ showReport: state.analysisVisible });
    });
    $("#bbStake")?.addEventListener("input", () => {
      state.revision += 1;
      invalidateAnalysis();
      saveTicket();
      renderSummary();
      renderAnalysis();
      scheduleAnalysis({ showReport: state.analysisVisible });
    });

    $("#bbEvents")?.addEventListener("click", (event) => {
      const button = event.target.closest(".bb-pick");
      if (!button || button.disabled) return;
      const card = button.closest(".bb-event");
      if (!card) return;
      const marketId = button.dataset.market;
      const side = button.dataset.side;
      let line = parsedLine(button.dataset.line);
      if (button.dataset.requiresLine === "true") {
        const row = button.closest(".bb-market-row");
        const input = row?.querySelector(".bb-line-input");
        line = parsedLine(input?.value);
      }
      addSelection(card.dataset.eventId, marketId, side, line);
    });

    $("#bbEvents")?.addEventListener("input", (event) => {
      const input = event.target.closest(".bb-line-input");
      if (!input) return;
      const key = input.dataset.lineKey;
      if (key) state.lines[key] = input.value;
      updateCustomMarketRow(input.closest(".bb-market-row"));
    });

    $("#bbSelections")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove-key]");
      if (!button) return;
      removeSelection(button.dataset.removeKey);
    });
  }

  function invalidateAnalysis() {
    state.analysis = null;
    state.error = null;
  }

  function scheduleAnalysis(options = {}) {
    window.clearTimeout(analysisTimer);
    if (!state.ticket.length) {
      resetAnalysis();
      return;
    }
    analysisTimer = window.setTimeout(() => analyzeTicket(options), 260);
  }

  function resetAnalysis() {
    state.analysis = null;
    state.analysisVisible = false;
    state.analyzing = false;
    state.error = null;
    renderTicket();
    renderSummary();
    renderAnalysis();
  }

  function activate() {
    if (!state.loaded && !state.loading) {
      loadEvents();
      return;
    }
    if (state.loaded && Date.now() - state.loadedAt > 5 * 60 * 1000) {
      loadEvents(true);
      return;
    }
    if (state.ticket.length && !state.analysis && !state.analyzing) {
      analyzeTicket({ silent: true });
    }
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item) => item.msg || JSON.stringify(item)).join("; ")
        : data.detail || `Error ${response.status}`;
      throw new Error(detail);
    }
    return data;
  }

  async function loadEvents(force = false) {
    if (state.loading) return;
    if (state.loaded && !force) return;
    state.loading = true;
    state.loadedAt = Date.now();
    state.revision += 1;
    setNotice($("#bbNotice"), "");
    const container = $("#bbEvents");
    if (container) {
      container.innerHTML = '<div class="bb-loading"><span class="spinner"></span>Cargando mercados…</div>';
    }
    const params = new URLSearchParams();
    if (state.filters.date) params.set("date", state.filters.date);
    params.set("days", String(state.filters.days || 7));
    try {
      const data = await request(`/api/bet-builder/events?${params.toString()}`);
      state.events = Array.isArray(data.events) ? data.events : [];
      state.meta = data;
      state.loaded = true;
      state.loading = false;
      state.error = null;
      if (!state.filters.date && data.start) {
        state.filters.date = data.start;
        const dateInput = $("#bbDate");
        if (dateInput) dateInput.value = data.start;
      }
      if (data.warning) {
        setNotice($("#bbNotice"), data.warning, false);
      }
      renderEvents();
      renderMarketSummary();
      if (state.ticket.length) {
        invalidateAnalysis();
        scheduleAnalysis({ showReport: state.analysisVisible });
      }
    } catch (error) {
      state.loading = false;
      state.error = error.message;
      setNotice($("#bbNotice"), `No se pudieron cargar los mercados: ${error.message}`, true);
      if (!state.events.length && container) {
        container.innerHTML = `<div class="bb-empty">No se pudieron cargar los mercados. Prueba a actualizar.</div>`;
      }
      renderMarketSummary();
      toast(error.message);
    }
  }

  function filteredEvents() {
    const query = state.filters.query.toLocaleLowerCase("es");
    const filter = state.filters.market;
    return state.events
      .filter((event) => {
        if (!query) return true;
        return [
          event.event_label,
          event.home,
          event.home_name,
          event.away,
          event.away_name,
        ]
          .filter(Boolean)
          .join(" ")
          .toLocaleLowerCase("es")
          .includes(query);
      })
      .map((event) => {
        if (filter === "all") return event;
        return {
          ...event,
          markets: (event.markets || []).filter((market) => market.type === filter),
        };
      })
      .filter((event) => (event.markets || []).length > 0);
  }

  function renderMarketSummary() {
    const box = $("#bbMarketSummary");
    if (!box) return;
    const meta = state.meta || {};
    const marketTotals = Number(meta.market_total_events || 0);
    const stateDate = meta.state_date || "—";
    const oddsLabel = meta.odds_source || "sin cuotas";
    box.innerHTML = [
      summaryStat("Partidos", String(meta.count ?? state.events.length ?? 0)),
      summaryStat("Totales", String(marketTotals)),
      summaryStat("Datos", stateDate),
      summaryStat("Cuotas", oddsLabel),
    ].join("");
    const range = $("#bbRange");
    if (range) {
      range.textContent = meta.start
        ? `${meta.start} – ${meta.end} · ${meta.count ?? 0} partidos`
        : "";
    }
    const note = $("#bbMarketNote");
    if (note) {
      const bits = [];
      if (meta.state_date) bits.push(`Estado de equipos hasta ${meta.state_date}.`);
      if (meta.odds_as_of) bits.push(`Cuotas consultadas ${meta.odds_as_of}.`);
      bits.push("Las líneas personalizadas no son líneas de mercado.");
      note.textContent = bits.join(" ");
    }
  }

  function renderEvents() {
    const container = $("#bbEvents");
    if (!container) return;
    renderMarketSummary();
    if (state.loading) {
      container.innerHTML = '<div class="bb-loading"><span class="spinner"></span>Cargando mercados…</div>';
      renderPlayerLocked(false);
      return;
    }
    if (state.filters.market === "players") {
      container.innerHTML =
        '<div class="bb-empty">Los mercados de jugador necesitan una línea de props válida. Ahora mismo no hay líneas disponibles para este módulo.</div>';
      renderPlayerLocked(true);
      return;
    }
    const events = filteredEvents();
    if (!events.length) {
      const message = state.filters.query
        ? "No hay equipos que coincidan con la búsqueda. La búsqueda de jugadores se activará cuando existan líneas de props."
        : "No hay partidos o mercados para ese filtro.";
      container.innerHTML = `<div class="bb-empty">${esc(message)}</div>`;
      renderPlayerLocked(state.filters.market === "all");
      return;
    }
    container.innerHTML = events.map(renderEvent).join("");
    renderPlayerLocked(state.filters.market === "all");
    updatePickStates();
  }

  function renderEvent(event) {
    const warning = event.data_warning
      ? `<div class="bb-event-warning">${esc(event.data_warning)}</div>`
      : "";
    const status = event.status && event.status !== "Programado" ? event.status : "";
    const meta = [shortDate(event.date), event.time_et ? `${event.time_et} ET` : "", status]
      .filter(Boolean)
      .map(esc)
      .join(" · ");
    return `<article class="bb-event" data-event-id="${esc(event.event_id)}">
      <header class="bb-event-head">
        <div>
          <div class="bb-event-meta">${meta}</div>
          <div class="bb-event-teams">${esc(event.away_name)} <span class="bb-at">@</span> ${esc(event.home_name)}</div>
        </div>
        <div class="bb-event-side">
          <div class="bb-event-total">${num(event.predicted_total, 1)}</div>
          <small>Total proyectado · IC 80% ${num(event.interval_low, 1)}–${num(event.interval_high, 1)}</small>
        </div>
      </header>
      ${warning}
      ${(event.markets || []).map((market) => renderMarket(event, market)).join("")}
    </article>`;
  }

  function renderMarket(event, market) {
    if (!market.available) {
      const reason = market.reason || "Mercado no disponible.";
      return `<div class="bb-market-row is-disabled" data-market-id="${esc(market.id)}">
        <div class="bb-market-label">
          <span>${esc(market.label)}</span>
          <small>${esc(reason)}</small>
        </div>
      </div>`;
    }

    const requiresLine = Boolean(market.requires_line);
    const line = market.line === null || market.line === undefined ? null : Number(market.line);
    const lineKey = `${event.event_id}:${market.id}`;
    const value = state.lines[lineKey] || "";
    const lineInput = requiresLine
      ? `<input class="bb-line-input" type="number" step="0.5" inputmode="decimal"
          data-line-key="${esc(lineKey)}" value="${esc(value)}" placeholder="Línea"
          aria-label="Línea para ${esc(market.label)}">`
      : "";
    const detail = [];
    if (line !== null) detail.push(`Línea ${lineText(line)}`);
    else if (market.projected !== null && market.projected !== undefined) {
      detail.push(`Proyección ${num(market.projected, 1)}`);
    }
    if (market.provider) detail.push(market.provider);
    if (requiresLine) detail.push("línea personalizada");
    if (line === null && !requiresLine && market.source) detail.push(market.source);
    const subtitle = detail.join(" · ");
    const options = (market.options || [])
      .map((option) => renderPick(event, market, option, line, requiresLine))
      .join("");
    return `<div class="bb-market-row${requiresLine ? " bb-custom-row" : ""}"
        data-market-id="${esc(market.id)}" data-event-id="${esc(event.event_id)}">
      <div class="bb-market-label">
        <span>${esc(market.label)}</span>
        <small>${esc(subtitle)}</small>
      </div>
      <div class="bb-market-actions">
        ${requiresLine ? lineInput : ""}
        ${options}
      </div>
    </div>`;
  }

  function renderPick(event, market, option, line, requiresLine) {
    const probability = option.probability;
    const disabled = requiresLine || probability === null || probability === undefined;
    const key = requiresLine
      ? ""
      : selectionKey({
          event_id: event.event_id,
          market: market.id,
          side: option.side,
          line,
        });
    const subtitle = option.decimal_odds
      ? `cuota ${oddsText(option.decimal_odds)}`
      : requiresLine
        ? "introduce línea"
        : market.line_source === "market"
          ? "sin cuota"
          : "modelo";
    return `<button class="bb-pick" type="button"
      data-market="${esc(market.id)}"
      data-side="${esc(option.side)}"
      data-line="${line === null ? "" : esc(line)}"
      data-requires-line="${requiresLine ? "true" : "false"}"
      data-selection-key="${esc(key)}"
      ${disabled ? "disabled" : ""}>
      ${esc(option.label)}
      <b>${probability === null || probability === undefined ? "—" : pct(probability)}</b>
      <small>${esc(subtitle)}</small>
    </button>`;
  }

  function updateCustomMarketRow(row) {
    if (!row) return;
    const input = row.querySelector(".bb-line-input");
    if (!input) return;
    const marketId = row.dataset.marketId;
    const eventId = row.dataset.eventId;
    const line = parsedLine(input.value);
    const valid = lineIsValid(marketId, line);
    $$(".bb-pick", row).forEach((button) => {
      button.disabled = !valid;
      const key = selectionKey({
        event_id: eventId,
        market: marketId,
        side: button.dataset.side,
        line,
      });
      const matched = state.ticket.find((selection) => selectionKey(selection) === key);
      button.classList.toggle("is-added", Boolean(matched));
      const probability = button.querySelector("b");
      if (probability) {
        probability.textContent =
          matched && matched.status === "ok" && matched.probability !== null && matched.probability !== undefined
            ? pct(matched.probability)
            : "-";
      }
      const subtitle = button.querySelector("small");
      if (subtitle) {
        subtitle.textContent = matched
          ? matched.status === "invalid" || matched.status === "stale"
            ? "revisar"
            : "en ticket"
          : valid
            ? "lista"
            : "introduce línea";
      }
    });
  }

  function updatePickStates() {
    $$("#bbEvents .bb-event").forEach((event) => {
      const eventId = event.dataset.eventId;
      $$(".bb-market-row.bb-custom-row", event).forEach(updateCustomMarketRow);
      $$(".bb-pick:not([data-requires-line='true'])", event).forEach((button) => {
        const line = parsedLine(button.dataset.line);
        const key = selectionKey({
          event_id: eventId,
          market: button.dataset.market,
          side: button.dataset.side,
          line,
        });
        button.classList.toggle(
          "is-added",
          state.ticket.some((selection) => selectionKey(selection) === key),
        );
      });
    });
  }

  function renderPlayerLocked(show) {
    const box = $("#bbPlayerLocked");
    if (!box) return;
    const player = state.meta?.player_markets;
    if (!player || player.available || !show) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    const stats = (player.stats || []).map((stat) => `<li>${esc(stat)}</li>`).join("");
    box.hidden = false;
    box.innerHTML = `<h4>${esc(player.label || "Mercados de jugador")} deshabilitados</h4>
      <p>${esc(player.reason || "Faltan líneas de mercado válidas.")}</p>
      ${stats ? `<ul>${stats}</ul>` : ""}
      ${player.next_step ? `<p style="margin-top:8px">${esc(player.next_step)}</p>` : ""}`;
  }

  function addSelection(eventId, marketId, side, line) {
    if (state.ticket.length >= MAX_SELECTIONS) {
      toast(`El ticket admite como máximo ${MAX_SELECTIONS} selecciones.`);
      return;
    }
    const event = state.events.find((item) => item.event_id === eventId);
    const market = event?.markets?.find((item) => item.id === marketId);
    if (!event || !market || !market.available) {
      toast("Ese mercado ya no está disponible.");
      return;
    }
    const requiresLine = Boolean(market.requires_line);
    const normalizedLine = requiresLine ? line : line ?? market.line ?? null;
    if (requiresLine && !lineIsValid(marketId, normalizedLine)) {
      toast("Introduce una línea numérica válida para este mercado.");
      return;
    }
    const option = (market.options || []).find((item) => item.side === side);
    if (!option) {
      toast("El lado seleccionado no está disponible.");
      return;
    }
    const key = selectionKey({
      event_id: eventId,
      market: marketId,
      side,
      line: normalizedLine,
    });
    if (state.ticket.some((selection) => selectionKey(selection) === key)) {
      toast("Esa selección ya está en el ticket.");
      return;
    }
    const opposite = state.ticket.find(
      (selection) =>
        selection.event_id === eventId &&
        selection.market === marketId &&
        sameLine(selection.line, normalizedLine) &&
        selection.side !== side,
    );
    if (opposite) {
      toast("No puedes combinar los dos lados del mismo mercado y línea.");
      return;
    }
    const selection = {
      event_id: eventId,
      event_date: event.date,
      event_label: event.event_label,
      market: marketId,
      market_label: market.label,
      side,
      side_label: option.label,
      line: normalizedLine,
      line_source: requiresLine ? "custom" : market.line_source,
      probability: option.probability,
      decimal_odds: option.decimal_odds,
      american_odds: option.american_odds,
      data_source: market.source,
      data_as_of: event.data_state_date,
      odds_as_of: option.decimal_odds ? event.odds_as_of : null,
      status: option.probability === null || option.probability === undefined ? "pending" : "ok",
      stale_reason: null,
      is_custom_line: requiresLine,
      note: market.note,
    };
    state.ticket.push(selection);
    state.revision += 1;
    invalidateAnalysis();
    saveTicket();
    renderTicket();
    updatePickStates();
    scheduleAnalysis({ showReport: state.analysisVisible });
    toast("Selección añadida al ticket.");
  }

  function removeSelection(key) {
    state.ticket = state.ticket.filter((selection) => selectionKey(selection) !== key);
    state.revision += 1;
    invalidateAnalysis();
    saveTicket();
    renderTicket();
    updatePickStates();
    if (!state.ticket.length) {
      resetAnalysis();
    } else {
      scheduleAnalysis({ showReport: state.analysisVisible });
    }
  }

  function clearTicket() {
    state.ticket = [];
    state.revision += 1;
    state.lines = {};
    saveTicket();
    resetAnalysis();
    renderEvents();
    toast("Ticket vacío.");
  }

  function renderTicket() {
    const title = $("#bbSlipTitle");
    const count = state.ticket.length;
    if (title) {
      title.textContent = `BET BUILDER · ${count} ${count === 1 ? "selección" : "selecciones"}`;
    }
    const tabCount = $("#bbTabCount");
    if (tabCount) {
      tabCount.textContent = String(count);
      tabCount.hidden = count === 0;
    }
    const mobileTicket = $("#bbMobileTicket");
    if (mobileTicket) {
      mobileTicket.hidden = count === 0;
      const mobileCount = $("#bbMobileCount");
      if (mobileCount) mobileCount.textContent = String(count);
    }
    const clear = $("#bbClear");
    if (clear) clear.disabled = count === 0;
    const analyze = $("#bbAnalyze");
    if (analyze) analyze.disabled = count === 0 || state.analyzing;
    const copy = $("#bbCopy");
    if (copy) copy.disabled = count === 0;
    const exportButton = $("#bbExport");
    if (exportButton) exportButton.disabled = count === 0;

    const box = $("#bbSelections");
    if (box) {
      if (!count) {
        box.innerHTML = '<div class="bb-empty">Añade una selección desde los mercados de la izquierda.</div>';
      } else {
        box.innerHTML = state.ticket.map(renderSelection).join("");
      }
    }

    const notice = $("#bbSlipNotice");
    if (state.error) {
      setNotice(notice, state.error, true);
    } else if (state.ticket.some((selection) => selection.status !== "ok" && selection.status !== "pending")) {
      setNotice(
        notice,
        "Hay selecciones que necesitan revisión. No se calcula la probabilidad conjunta hasta que estén actualizadas.",
        false,
      );
    } else if (state.analysis?.joint_status === "same_game_dependency") {
      setNotice(
        notice,
        "Probabilidad conjunta no disponible: hay selecciones del mismo partido y no se modela su dependencia.",
        false,
      );
    } else {
      setNotice(notice, "");
    }
    renderSummary();
    renderAnalysis();
  }

  function renderSelection(selection) {
    const status = selection.status || "pending";
    const probability =
      status === "ok" && selection.probability !== null && selection.probability !== undefined
        ? pct(selection.probability)
        : status === "pending"
          ? "Calculando…"
          : "No disponible";
    const source = selection.data_source || "Modelo Ridge";
    const asOf = selection.data_as_of ? `datos ${selection.data_as_of}` : "";
    const oddsAsOf = selection.odds_as_of ? `cuotas ${selection.odds_as_of}` : "";
    const odds = selection.decimal_odds
      ? `cuota ${oddsText(selection.decimal_odds)}`
      : selection.is_custom_line
        ? "sin cuota de mercado"
        : "sin cuota";
    const warning =
      status !== "ok" && status !== "pending"
        ? `<div class="bb-selection-warning">${esc(selection.stale_reason || "Selección desactualizada.")}</div>`
        : "";
    return `<article class="bb-selection${status === "stale" || status === "invalid" ? " is-stale" : ""}">
      <button class="bb-selection-remove" type="button" data-remove-key="${esc(selectionKey(selection))}" aria-label="Eliminar selección">×</button>
      <div class="bb-selection-top">
        <div>
          <div class="bb-selection-event">${esc(selection.event_label || selection.event_id)}</div>
          <div class="bb-selection-market">${esc(selectionTitle(selection))}</div>
        </div>
        ${statusTag(status)}
      </div>
      <div class="bb-selection-meta">
        <span class="bb-selection-prob">${esc(probability)}</span>
        <span>${esc(source)}</span>
        ${asOf ? `<span>${esc(asOf)}</span>` : ""}
        ${oddsAsOf ? `<span>${esc(oddsAsOf)}</span>` : ""}
        <span>${esc(odds)}</span>
        <span>${esc(lineSourceLabel(selection))}</span>
      </div>
      ${warning}
    </article>`;
  }

  function renderSummary() {
    const box = $("#bbTicketSummary");
    if (!box) return;
    const count = state.ticket.length;
    if (!count) {
      box.innerHTML = "";
      return;
    }
    const analysis = state.analysis;
    if (!analysis && state.analyzing) {
      box.innerHTML = [
        metric("Selecciones", String(count), "ticket"),
        metric("Conjunta", "Calculando…", "probabilidad"),
        metric("Cuota justa", "—", "se calcula al terminar"),
        metric("EV", "—", "requiere cuota"),
      ].join("");
      return;
    }
    if (!analysis) {
      box.innerHTML = [
        metric("Selecciones", String(count), "ticket"),
        metric("Conjunta", "Pendiente", "analiza el ticket"),
        metric("Cuota justa", "—", "sin cálculo"),
        metric("EV", "—", "requiere cuota"),
      ].join("");
      return;
    }

    const joint = analysis.joint_probability;
    const jointSub =
      analysis.joint_status === "independence"
        ? "independencia entre partidos"
        : analysis.joint_status === "same_game_dependency"
          ? "dependencia no modelada"
          : analysis.joint_status === "invalid"
            ? "revisa las selecciones"
            : "sin selecciones";
    const jointClass = joint === null || joint === undefined ? "is-warn" : "";
    const fair = analysis.fair_decimal_odds;
    const marketCombined = analysis.market_combined_decimal_odds;
    const used = analysis.decimal_odds;
    const ev = analysis.ev;
    const potential = analysis.potential_return;
    const usedSource = oddsSourceLabel(analysis.decimal_odds_source);
    box.innerHTML = [
      metric("Selecciones", String(count), jointSub),
      metric(
        "Conjunta",
        joint === null || joint === undefined ? "No disponible" : pct(joint),
        analysis.joint_status === "independence" ? "supuesto de independencia" : "no se multiplica",
        jointClass,
      ),
      metric("Cuota justa", fair === null || fair === undefined ? "—" : oddsText(fair), "1 / P(conjunta)"),
      metric(
        "Cuota mercado",
        marketCombined === null || marketCombined === undefined ? "—" : oddsText(marketCombined),
        marketCombined ? "producto de cuotas" : "faltan cuotas",
      ),
      metric("Cuota usada", used === null || used === undefined ? "—" : oddsText(used), usedSource),
      metric(
        "Retorno",
        potential === null || potential === undefined ? "—" : `${nf2.format(Number(potential))} u`,
        "stake incluido",
      ),
      metric(
        "EV",
        ev === null || ev === undefined ? "—" : signedPct(ev),
        "por unidad · no garantizado",
        ev === null || ev === undefined ? "" : ev > 0 ? "is-good" : "is-bad",
      ),
    ].join("");
  }

  function renderAnalysis() {
    const box = $("#bbAnalysis");
    if (!box) return;
    if (!state.analysisVisible) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    if (!state.analysis) {
      box.hidden = false;
      box.innerHTML = state.analyzing
        ? '<div class="bb-loading"><span class="spinner"></span>Calculando análisis…</div>'
        : '<p class="bb-disclaimer">Pulsa “Analizar combinada” para generar el informe con los datos actuales.</p>';
      return;
    }
    const analysis = state.analysis;
    const rows = (analysis.selections || [])
      .map((selection) => {
        const probability =
          selection.probability === null || selection.probability === undefined
            ? statusLabel(selection.status)
            : pct(selection.probability);
        const line =
          selection.line === null || selection.line === undefined
            ? ""
            : ` ${lineText(selection.line)}`;
        const custom = selection.is_custom_line ? " · personalizada" : "";
        const label = `${selection.event_label || selection.event_id} · ${
          selection.market_label || marketLabel(selection.market)
        } · ${sideLabel(selection)}${line}${custom}`;
        return `<div class="bb-report-row"><span>${esc(label)}</span><strong>${esc(probability)}</strong></div>`;
      })
      .join("");
    const sameGame = (analysis.same_game_groups || []).length
      ? `<ul>${analysis.same_game_groups
          .map(
            (group) =>
              `<li>${esc(group.event_label || group.event_id)}: ${group.count} selecciones del mismo partido.</li>`,
          )
          .join("")}</ul>`
      : analysis.joint_status === "independence"
        ? "<p>No hay selecciones del mismo partido. Se aplica el supuesto de independencia entre partidos.</p>"
        : "<p>No se calcula una probabilidad conjunta para este ticket; no se aplica el supuesto de independencia.</p>";
    const quality = analysis.data_quality || {};
    const warnings = (analysis.warnings || []).length
      ? `<ul>${analysis.warnings.map((warning) => `<li>${esc(warning)}</li>`).join("")}</ul>`
      : "<p>Sin advertencias adicionales.</p>";
    const assumptions = (analysis.assumptions || [])
      .map((item) => `<li>${esc(item)}</li>`)
      .join("");
    const limitations = (analysis.limitations || [])
      .map((item) => `<li>${esc(item)}</li>`)
      .join("");
    const value =
      analysis.ev === null || analysis.ev === undefined
        ? "<p>Introduce una cuota decimal válida o usa selecciones con cuota de mercado para calcular el EV y el retorno potencial.</p>"
        : `<p>Con cuota ${oddsText(analysis.decimal_odds)} y P(conjunta) ${pct(
            analysis.joint_probability,
          )}, EV = p × O − 1 = <strong>${signedPct(analysis.ev)}</strong> por unidad. Retorno potencial con stake ${num(
            analysis.stake,
            2,
          )}: <strong>${num(analysis.potential_return, 2)} u</strong>.</p>`;
    const fairComparison =
      analysis.decimal_odds && analysis.fair_decimal_odds
        ? `<div class="bb-report-row"><span>Cuota usada frente a cuota justa</span><strong>${oddsText(
            analysis.decimal_odds,
          )} vs ${oddsText(analysis.fair_decimal_odds)}</strong></div>`
        : "";
    box.hidden = false;
    box.innerHTML = `<h4>ANÁLISIS DEL MODELO</h4>
      <div class="bb-report-section">
        <h5>Probabilidades</h5>
        <div class="bb-report-row"><span>Probabilidad conjunta</span><strong>${
          analysis.joint_probability === null || analysis.joint_probability === undefined
            ? "No disponible"
            : pct(analysis.joint_probability)
        }</strong></div>
        ${rows || "<p>No hay selecciones válidas para analizar.</p>"}
        ${
          analysis.joint_reason
            ? `<p style="margin-top:6px">${esc(analysis.joint_reason)}</p>`
            : ""
        }
      </div>
      <div class="bb-report-section">
        <h5>Dependencias entre selecciones</h5>
        ${sameGame}
      </div>
      <div class="bb-report-section">
        <h5>Calidad y antigüedad de los datos</h5>
        <div class="bb-report-row"><span>Estado de equipos</span><strong>${esc(
          quality.state_date || "—",
        )}${quality.state_age_days !== null && quality.state_age_days !== undefined ? ` · ${ageText(quality.state_age_days)}` : ""}</strong></div>
        <div class="bb-report-row"><span>Cuotas</span><strong>${esc(
          quality.odds_source || "sin cuota de mercado",
        )}${quality.odds_as_of ? ` · ${esc(quality.odds_as_of)}` : ""}</strong></div>
        <div class="bb-report-row"><span>Selecciones con cuota de mercado</span><strong>${esc(
          quality.selections_with_market_odds ?? 0,
        )}</strong></div>
        <div class="bb-report-row"><span>Líneas personalizadas</span><strong>${esc(
          quality.custom_lines ?? 0,
        )}</strong></div>
      </div>
      <div class="bb-report-section">
        <h5>Valor esperado y retorno</h5>
        <div class="bb-report-row"><span>Cuota combinada de mercado</span><strong>${esc(
          analysis.market_combined_decimal_odds === null ||
            analysis.market_combined_decimal_odds === undefined
            ? "—"
            : oddsText(analysis.market_combined_decimal_odds),
        )}</strong></div>
        <div class="bb-report-row"><span>Cuota usada</span><strong>${esc(
          analysis.decimal_odds === null || analysis.decimal_odds === undefined
            ? "—"
            : oddsText(analysis.decimal_odds),
        )}${analysis.decimal_odds_source ? ` · ${esc(oddsSourceLabel(analysis.decimal_odds_source))}` : ""}</strong></div>
        ${value}
        <p>Fórmula del EV: p × O − 1, donde p es la probabilidad conjunta y O la cuota decimal. No es una garantía de beneficio.</p>
        ${fairComparison}
      </div>
      <div class="bb-report-section">
        <h5>Advertencias</h5>
        ${warnings}
      </div>
      <div class="bb-report-section">
        <h5>Supuestos</h5>
        <ul>${assumptions}</ul>
      </div>
      <div class="bb-report-section">
        <h5>Limitaciones del modelo</h5>
        <ul>${limitations}</ul>
      </div>`;
  }

  function updateTicketFromAnalysis(analysis) {
    const responseSelections = Array.isArray(analysis.selections) ? analysis.selections : [];
    state.ticket = state.ticket.map((selection, index) => {
      const current = responseSelections[index];
      if (!current) {
        return {
          ...selection,
          status: "stale",
          stale_reason: "No se pudo revalidar esta selección con los datos actuales.",
          probability: null,
        };
      }
      return {
        ...selection,
        ...current,
        line:
          current.line === null || current.line === undefined ? selection.line : current.line,
        probability: current.probability,
        status: current.status || "ok",
        stale_reason: current.stale_reason || null,
      };
    });
  }

  async function analyzeTicket(options = {}) {
    if (!state.ticket.length) {
      resetAnalysis();
      return;
    }
    const requestId = ++state.requestId;
    const revision = state.revision;
    const oddsInput = $("#bbOdds");
    const stakeInput = $("#bbStake");
    const odds = parsedLine(oddsInput?.value);
    const stakeValue = stakeInput?.value?.trim() || "";
    const stake = stakeValue ? parsedLine(stakeValue) : 10;
    if (oddsInput?.value && (!odds || odds <= 1)) {
      setNotice($("#bbNotice"), "La cuota decimal debe ser mayor que 1.", true);
      return;
    }
    if (stakeValue && (!stake || stake <= 0)) {
      setNotice($("#bbNotice"), "La stake debe ser mayor que 0.", true);
      return;
    }
    state.analyzing = true;
    state.error = null;
    renderTicket();
    try {
      const payload = {
        selections: state.ticket.map((selection) => ({
          event_id: selection.event_id,
          event_date: selection.event_date,
          market: selection.market,
          side: selection.side,
          line: selection.line,
        })),
        stake,
        decimal_odds: odds,
      };
      const analysis = await request("/api/bet-builder/analyze", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (requestId !== state.requestId || revision !== state.revision) return;
      state.analysis = analysis;
      state.analysisVisible = Boolean(options.showReport || state.analysisVisible);
      updateTicketFromAnalysis(analysis);
      saveTicket();
      setNotice($("#bbNotice"), "");
    } catch (error) {
      if (requestId === state.requestId) {
        state.error = error.message;
        toast(error.message);
      }
    } finally {
      if (requestId === state.requestId) {
        state.analyzing = false;
        renderTicket();
        updatePickStates();
      }
    }
  }

  function copySummary() {
    if (!state.ticket.length) return;
    const analysis = state.analysis;
    const lines = [
      `BET BUILDER · ${state.ticket.length} ${
        state.ticket.length === 1 ? "selección" : "selecciones"
      }`,
      "",
      ...state.ticket.map(
        (selection, index) =>
          `${index + 1}. ${selection.event_label || selection.event_id} — ${selectionTitle(
            selection,
          )}${selection.is_custom_line ? " · personalizada" : ""} — ${
            selection.probability === null || selection.probability === undefined
              ? "probabilidad no disponible"
              : pct(selection.probability)
          }`,
      ),
    ];
    if (analysis) {
      lines.push(
        "",
        `Probabilidad conjunta: ${
          analysis.joint_probability === null || analysis.joint_probability === undefined
            ? "No disponible"
            : pct(analysis.joint_probability)
        }`,
        `Estado: ${jointStatusLabel(analysis.joint_status)}`,
        `Cuota justa teórica: ${
          analysis.fair_decimal_odds === null || analysis.fair_decimal_odds === undefined
            ? "—"
            : oddsText(analysis.fair_decimal_odds)
        }`,
      );
      if (analysis.decimal_odds) {
        lines.push(
          `Cuota usada: ${oddsText(analysis.decimal_odds)} (${oddsSourceLabel(
            analysis.decimal_odds_source,
          )})`,
          `EV: ${signedPct(analysis.ev)} por unidad`,
          `Retorno potencial: ${num(analysis.potential_return, 2)} u (stake ${num(
            analysis.stake,
            2,
          )})`,
        );
      }
    }
    lines.push(
      "",
      "Probabilidades estimadas por el modelo; no son cuotas de casa ni garantizan beneficios.",
    );
    const text = lines.join("\n");
    const done = () => toast("Resumen copiado.");
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    } else {
      fallbackCopy(text, done);
    }
  }

  function fallbackCopy(text, done) {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
      done();
    } catch (error) {
      toast("No se pudo copiar el resumen.");
    } finally {
      textarea.remove();
    }
  }

  function exportTicket() {
    if (!state.ticket.length) return;
    const payload = {
      app: "NBA TOTALS",
      module: "bet-builder",
      exported_at: new Date().toISOString(),
      selections: state.ticket,
      analysis: state.analysis,
      disclaimer:
        "Probabilidades estimadas por el modelo. No son cuotas de casa ni garantizan beneficios.",
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `nba-totals-bet-builder-${new Date()
      .toISOString()
      .slice(0, 10)}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast("Ticket exportado.");
  }

  function init() {
    if (state.initialized) return;
    state.initialized = true;
    restoreTicket();
    bind();
    renderTicket();
    renderSummary();
    window.addEventListener("betbuilder:activate", activate);
    if ($("#tab-betbuilder")?.classList.contains("is-active")) {
      activate();
    }
  }

  document.addEventListener("DOMContentLoaded", init);
})();
