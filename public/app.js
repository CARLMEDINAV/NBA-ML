"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const nf1 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });
const pf = new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 1 });

const state = {
  meta: null,
  line: 225.5,
  active: "upcoming",
  loaded: { upcoming: false, analysis: false, results: false, players: false },
  upcoming: { days: 7, team: "" },
  analysis: { days: 7, team: "" },
  results: { season: "2025-26", team: "", limit: 25 },
  players: {
    season: "2026-27",
    mode: "pergame",
    sort: "pra",
    team: "",
    minGp: 1,
    limit: 25,
    q: "",
  },
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
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return (digits === 0 ? nf0 : nf1).format(Number(value));
}

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return pf.format(Number(value));
}

function signed(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return `${number >= 0 ? "+" : ""}${num(number, digits)}`;
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

function numericDate(value) {
  if (!value) return "—";
  const date = new Date(`${value}T12:00:00`);
  return date.toLocaleDateString("es-ES", {
    day: "numeric",
    month: "numeric",
    year: "numeric",
  });
}

function initials(name) {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!parts.length) return "?";
  return parts
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function leanPill(lean) {
  const value = lean || "—";
  const cls = value === "OVER" ? "pill-over" : value === "UNDER" ? "pill-under" : "pill-neutral";
  return `<span class="pill ${cls}">${esc(value)}</span>`;
}

function resultPill(value) {
  const text = value || "—";
  const cls = text === "OVER" ? "pill-over" : text === "UNDER" ? "pill-under" : "pill-push";
  return `<span class="pill ${cls}">${esc(text)}</span>`;
}

function hitMark(value) {
  if (value === 1 || value === true) return '<span class="hit">✓</span>';
  if (value === 0 || value === false) return '<span class="miss">✗</span>';
  return '<span class="na">—</span>';
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 5200);
}

function notice(element, message, isError = false) {
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

function loadingRow(tbody, columns, message = "Cargando…") {
  tbody.innerHTML = `<tr class="loading"><td colspan="${columns}"><span class="spinner"></span>${esc(message)}</td></tr>`;
}

function emptyRow(tbody, columns, message) {
  tbody.innerHTML = `<tr><td colspan="${columns}"><div class="empty">${esc(message)}</div></td></tr>`;
}

function summaryCard(label, value, sub = "") {
  return `<div class="summary-card"><span class="k">${esc(label)}</span><span class="v">${esc(value)}</span>${sub ? `<span class="s">${esc(sub)}</span>` : ""}</div>`;
}

function setBusy(button, busy) {
  if (!button) return;
  button.disabled = busy;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = "Cargando…";
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
  }
}

async function api(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") return;
    url.searchParams.set(key, value);
  });
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Error ${response.status} al consultar la API`);
  }
  return data;
}

function fillTeams(select, teams, includeAll = false) {
  const current = select.value;
  select.innerHTML = "";
  if (includeAll) {
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "Todos";
    select.append(all);
  }
  teams.forEach((team) => {
    const option = document.createElement("option");
    option.value = team.abbreviation;
    option.textContent = `${team.abbreviation} · ${team.name}`;
    select.append(option);
  });
  if (current && Array.from(select.options).some((option) => option.value === current)) {
    select.value = current;
  }
}

function populateTeams(meta) {
  const teams = meta.teams || [];
  fillTeams($("#upcomingTeam"), teams, true);
  fillTeams($("#analysisTeam"), teams, true);
  fillTeams($("#resultsTeam"), teams, true);
  fillTeams($("#playersTeam"), teams, true);
  fillTeams($("#predictHome"), teams);
  fillTeams($("#predictAway"), teams);

  const home = $("#predictHome");
  const away = $("#predictAway");
  if (teams.some((team) => team.abbreviation === "BOS")) home.value = "BOS";
  if (teams.some((team) => team.abbreviation === "LAL")) away.value = "LAL";
  if (home.value === away.value && teams.length > 1) away.value = teams[1].abbreviation;
}

function setLine(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return;
  state.line = Math.min(350, Math.max(100, Math.round(parsed * 2) / 2));
  $("#lineInput").value = state.line.toFixed(1);
  if (state.active === "upcoming") loadUpcoming();
  if (state.active === "analysis") loadAnalysis();
  if (state.active === "results") loadResults();
  if (state.active === "predict" && $("#predictResult").dataset.ready === "1") {
    $("#predictForm").requestSubmit();
  }
}

async function loadUpcoming(silent = false) {
  const tbody = $("#upcomingBody");
  const button = $("#upcomingRefresh");
  if (!silent) {
    loadingRow(tbody, 8);
    setBusy(button, true);
    notice($("#upcomingNotice"), "");
    $("#upcomingSummary").innerHTML = "";
    $("#upcomingNote").textContent = "";
  }
  try {
    const data = await api("/api/upcoming", {
      days: state.upcoming.days,
      team: state.upcoming.team,
      line: state.line,
      live: 1,
    });
    state.loaded.upcoming = true;
    $("#upcomingRange").textContent = `${data.start} → ${data.end}`;
    const probabilities = data.games.map((game) => game.probability_over).filter((value) => value != null);
    const average = probabilities.length
      ? probabilities.reduce((sum, value) => sum + value, 0) / probabilities.length
      : null;
    $("#upcomingSummary").innerHTML = [
      summaryCard("Partidos", String(data.count), `${data.skipped} omitidos`),
      summaryCard("Línea", num(data.line, 1), "referencia"),
      summaryCard("P(over) media", pct(average), "si hay señal"),
      summaryCard("Modelo", state.meta?.model_name || "Ridge", `hasta ${state.meta?.trained_through || "—"}`),
    ].join("");

    if (!data.games.length) {
      emptyRow(tbody, 8, "No hay partidos programados en ese rango para el filtro elegido.");
      $("#upcomingNote").textContent = "Prueba a ampliar los días o quitar el filtro de equipo.";
      notice($("#upcomingNotice"), data.warning || "");
      return;
    }

    tbody.innerHTML = data.games
      .map(
        (game) => `
        <tr>
          <td>${esc(shortDate(game.date))}</td>
          <td>${esc(game.time_et || "—")}</td>
          <td><span class="team">${esc(game.away)}</span> <span class="vs">@</span> <span class="team">${esc(game.home)}</span></td>
          <td class="num">${num(game.predicted_total)}</td>
          <td>${num(game.interval_low, 0)} – ${num(game.interval_high, 0)}</td>
          <td class="num">${pct(game.probability_over)}</td>
          <td>${leanPill(game.lean)}</td>
          <td>${esc(game.model_handicap || "—")}</td>
        </tr>`
      )
      .join("");
    $("#upcomingNote").textContent = `${data.count} partidos · línea ${num(data.line, 1)} · P(over) calculada con los residuos fuera de muestra.${data.live_updated ? ` · actualizado ${data.live_updated}` : ""}`;
    notice($("#upcomingNotice"), data.warning || "");
  } catch (error) {
    if (!silent) {
      emptyRow(tbody, 8, "No se pudieron cargar los próximos partidos.");
      notice($("#upcomingNotice"), error.message, true);
      showToast(error.message);
    }
  } finally {
    setBusy(button, false);
  }
}

async function loadAnalysis(silent = false) {
  const tbody = $("#analysisBody");
  const cards = $("#analysisCards");
  const button = $("#analysisRefresh");
  if (!silent) {
    loadingRow(tbody, 11);
    setBusy(button, true);
    notice($("#analysisNotice"), "");
    $("#analysisSummary").innerHTML = "";
    $("#analysisNote").textContent = "";
    cards.innerHTML = "";
  }
  try {
    const data = await api("/api/upcoming", {
      days: state.analysis.days,
      team: state.analysis.team,
      line: state.line,
      live: 1,
      odds: 1,
      analysis: 1,
    });
    state.loaded.analysis = true;
    $("#analysisRange").textContent = `${data.start} → ${data.end}`;
    const games = data.games || [];
    const signalSide = (game) =>
      game.market_total !== null && game.market_total !== undefined
        ? game.market_total_side
        : game.total_side;
    const overSignals = games.filter((game) => signalSide(game) === "OVER").length;
    const underSignals = games.filter((game) => signalSide(game) === "UNDER").length;
    $("#analysisSummary").innerHTML = [
      summaryCard("Partidos", String(games.length), `${overSignals} over · ${underSignals} under`),
      summaryCard("Línea", num(data.line, 1), "referencia"),
      summaryCard("Cuotas", data.market_source ? "ESPN" : "—", data.market_source || "sin mercado"),
      summaryCard("Modelo", state.meta?.model_name || "Ridge", `hasta ${state.meta?.trained_through || "—"}`),
    ].join("");

    if (!games.length) {
      emptyRow(tbody, 11, "No hay partidos para ese rango o filtro.");
      $("#analysisNote").textContent = "Prueba a ampliar los días o quitar el filtro de equipo.";
      notice($("#analysisNotice"), data.warning || "");
      return;
    }

    tbody.innerHTML = games
      .map((game) => {
        const hasMarketTotal = game.market_total !== null && game.market_total !== undefined;
        const side = hasMarketTotal ? game.market_total_side : game.total_side;
        const label = hasMarketTotal ? game.market_total_label : game.total_label;
        const cls = side === "OVER" ? "total-over" : side === "UNDER" ? "total-under" : "total-neutral";
        return `
        <tr>
          <td>${esc(numericDate(game.date))}</td>
          <td>${esc(game.time_et || "—")}</td>
          <td><span class="team">${esc(game.home_name || game.home)}</span></td>
          <td><span class="team">${esc(game.away_name || game.away)}</span></td>
          <td class="num">${num(game.projected_home_score)}</td>
          <td class="num">${num(game.projected_away_score)}</td>
          <td>
            <div class="total-cell">
              <strong>${num(game.predicted_total)}</strong>
              <span class="${cls}">${esc(label || "—")}</span>
              ${hasMarketTotal ? `<small>Mercado ${num(game.market_total)}</small>` : ""}
            </div>
          </td>
          <td>${esc(game.possible_winner || "—")}</td>
          <td>
            <span>${esc(game.model_handicap || "—")}</span>
            ${game.market_handicap ? `<small class="market-line">Mercado: ${esc(game.market_handicap)}</small>` : ""}
          </td>
          <td class="num">${esc(game.over_odds || "—")}</td>
          <td class="num">${esc(game.under_odds || "—")}</td>
        </tr>`;
      })
      .join("");

    cards.innerHTML = games
      .map((game) => {
        const hasMarketTotal = game.market_total !== null && game.market_total !== undefined;
        const side = hasMarketTotal ? game.market_total_side : game.total_side;
        return `
        <article class="analysis-card">
          <div class="analysis-card-head">
            <div>
              <span class="analysis-date">${esc(shortDate(game.date))} · ${esc(game.time_et || "hora por confirmar")}</span>
              <h3>${esc(game.home_name || game.home)} <span class="vs">vs</span> ${esc(game.away_name || game.away)}</h3>
            </div>
            ${leanPill(side)}
          </div>
          <div class="analysis-metrics">
            <div><span>Puntos proyectados</span><strong>Local ${num(game.projected_home_score)} · Visitante ${num(game.projected_away_score)}</strong></div>
            <div><span>Total previsto</span><strong>${num(game.predicted_total)}</strong></div>
            <div><span>Posible ganador</span><strong>${esc(game.possible_winner || "—")}</strong></div>
            <div><span>Hándicap modelo</span><strong>${esc(game.model_handicap || "—")}</strong></div>
            <div><span>Mercado</span><strong>${esc(game.market_handicap || "Sin cuota")}${hasMarketTotal ? ` · ${num(game.market_total)}` : ""}</strong></div>
            <div><span>Cuotas over/under</span><strong>${esc(game.over_odds || "—")} / ${esc(game.under_odds || "—")}</strong></div>
          </div>
          <p class="analysis-text">${esc(game.analysis || "Sin análisis disponible.")}</p>
        </article>`;
      })
      .join("");

    const source = data.market_source
      ? ` Cuotas: ${data.market_source}. En la línea de mercado, Alta/Baja = probabilidad ≥55% / ≤45%; Leve = 50-55%.`
      : " Sin cuotas de mercado para esas fechas.";
    $("#analysisNote").textContent = `${games.length} partidos · línea de referencia ${num(data.line, 1)}.${source} ${data.handicap_note || ""}`;
    notice($("#analysisNotice"), data.warning || "");
  } catch (error) {
    if (!silent) {
      emptyRow(tbody, 11, "No se pudo cargar el análisis.");
      notice($("#analysisNotice"), error.message, true);
      showToast(error.message);
    }
  } finally {
    setBusy(button, false);
  }
}

async function loadResults(silent = false) {
  const tbody = $("#resultsBody");
  const button = $("#resultsRefresh");
  if (!silent) {
    loadingRow(tbody, 11);
    setBusy(button, true);
    notice($("#resultsNotice"), "");
    $("#resultsSummary").innerHTML = "";
    $("#resultsNote").textContent = "";
  }
  try {
    const data = await api("/api/results", {
      season: state.results.season,
      team: state.results.team,
      limit: state.results.limit,
      line: state.line,
      live: state.results.season === state.meta?.current_season ? 1 : 0,
    });
    state.loaded.results = true;
    const summary = data.summary || {};
    $("#resultsSummary").innerHTML = [
      summaryCard("Partidos", String(summary.n ?? 0), data.season),
      summaryCard("MAE", num(summary.mae, 2), "puntos"),
      summaryCard("Acierto", pct(summary.hit_rate), `${summary.n_leans ?? 0} señales`),
      summaryCard("Over real", pct(summary.over_rate), "sin modelo"),
      summaryCard("Brier", num(summary.brier, 3), "probabilidad"),
      summaryCard("Sesgo", signed(summary.bias, 2), "previsto − real"),
    ].join("");

    if (!data.games.length) {
      emptyRow(tbody, 11, "Todavía no hay partidos con resultado para esta temporada o filtro.");
      $("#resultsNote").textContent = data.caveat || "";
      return;
    }

    tbody.innerHTML = data.games
      .map((game) => {
        const error = Number(game.error);
        return `
        <tr>
          <td>${esc(shortDate(game.game_date))}</td>
          <td><span class="team">${esc(game.away_team)}</span> <span class="vs">@</span> <span class="team">${esc(game.home_team)}</span></td>
          <td class="num">${num(game.total_points, 0)}</td>
          <td class="num">${num(game.predicted_total)}</td>
          <td class="num">${error >= 0 ? "+" : ""}${num(error, 1)}</td>
          <td class="num">${pct(game.probability_over)}</td>
          <td>${resultPill(game.actual_result)}</td>
          <td>${leanPill(game.model_lean)}</td>
          <td>${hitMark(game.hit)}</td>
          <td>${esc(game.model_handicap || "—")}</td>
          <td class="num">${game.actual_margin == null ? "—" : signed(game.actual_margin, 1)}</td>
        </tr>`;
      })
      .join("");
    $("#resultsNote").textContent = `${data.source} · ${data.count} partidos · línea de referencia ${num(data.line, 1)}.${data.live_updated ? ` Actualizado ${data.live_updated}.` : ""} ${data.caveat || ""}`;
  } catch (error) {
    if (!silent) {
      emptyRow(tbody, 11, "No se pudieron cargar los resultados.");
      notice($("#resultsNotice"), error.message, true);
      showToast(error.message);
    }
  } finally {
    setBusy(button, false);
  }
}

async function loadPlayers() {
  const tbody = $("#playersBody");
  const button = $("#playersRefresh");
  loadingRow(tbody, 9);
  setBusy(button, true);
  notice($("#playersNotice"), "");
  $("#playersSummary").innerHTML = "";
  $("#playersBars").innerHTML = "";
  $("#playersNote").textContent = "";
  try {
    const data = await api("/api/players", {
      season: state.players.season,
      mode: state.players.mode,
      sort: state.players.sort,
      team: state.players.team,
      min_gp: state.players.minGp,
      limit: state.players.limit,
      q: state.players.q,
    });
    state.loaded.players = true;
    const players = data.players || [];
    const leader = players[0];
    const average = players.length
      ? players.reduce((sum, player) => sum + Number(player.pra || 0), 0) / players.length
      : null;
    $("#playersSummary").innerHTML = [
      summaryCard("Jugadores", String(data.count), `${data.season} · ${data.mode === "pergame" ? "por partido" : "totales"}`),
      summaryCard("Líder PRA", leader ? `${leader.player_name}` : "—", leader ? `${num(leader.pra)} PRA` : ""),
      summaryCard("Media PRA", num(average), "del top mostrado"),
      summaryCard("Mín. partidos", String(state.players.minGp), "filtro"),
    ].join("");

    if (data.season === "2026-27" && state.players.minGp > 1) {
      notice($("#playersNotice"), "La 2026-27 acaba de empezar: baja el mínimo de partidos para ver más jugadores.", false);
    }

    if (!players.length) {
      emptyRow(tbody, 9, "No hay jugadores que cumplan esos filtros.");
      $("#playersNote").textContent = "Prueba a bajar el mínimo de partidos o quitar la búsqueda.";
      return;
    }

    const maxPra = Math.max(...players.map((player) => Number(player.pra || 0)), 1);
    $("#playersBars").innerHTML = players
      .slice(0, 8)
      .map((player) => {
        const pra = Number(player.pra || 0);
        const pts = Number(player.pts || 0);
        const reb = Number(player.reb || 0);
        const ast = Number(player.ast || 0);
        const total = pts + reb + ast || 1;
        return `
          <div class="bar-row">
            <div class="bar-name" title="${esc(player.player_name)}">${esc(player.player_name)} <small>${esc(player.team)}</small></div>
            <div class="bar-track"><div style="width:${(pra / maxPra) * 100}%;display:flex;height:100%">
              <div class="bar-seg seg-pts" style="width:${(pts / total) * 100}%"></div>
              <div class="bar-seg seg-reb" style="width:${(reb / total) * 100}%"></div>
              <div class="bar-seg seg-ast" style="width:${(ast / total) * 100}%"></div>
            </div></div>
            <div class="bar-value">${num(pra)}</div>
          </div>`;
      })
      .join("");

    tbody.innerHTML = players
      .map((player, index) => {
        const headshotUrl =
          player.headshot_url ||
          (player.player_id
            ? `https://cdn.nba.com/headshots/nba/latest/260x190/${player.player_id}.png`
            : "");
        return `
        <tr>
          <td class="na">${index + 1}</td>
          <td>
            <div class="player-cell">
              <span class="player-avatar" aria-hidden="true">
                <span class="player-initials">${esc(initials(player.player_name))}</span>
                ${
                  headshotUrl
                    ? `<img class="player-headshot" src="${esc(headshotUrl)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">`
                    : ""
                }
              </span>
              <strong class="player-name">${esc(player.player_name)}</strong>
            </div>
          </td>
          <td>${esc(player.team)}</td>
          <td class="num">${num(player.gp, 0)}</td>
          <td class="num">${num(player.min)}</td>
          <td class="num">${num(player.pts)}</td>
          <td class="num">${num(player.reb)}</td>
          <td class="num">${num(player.ast)}</td>
          <td class="num"><strong>${num(player.pra)}</strong></td>
        </tr>`;
      })
      .join("");
    const rosterNote = data.season === "2025-26" ? "equipos de esa temporada" : "equipos actuales";
    $("#playersNote").textContent = `PRA = PTS + REB + AST · ${data.season} · ${rosterNote} · ${data.count} jugadores con al menos ${state.players.minGp} partidos.`;
  } catch (error) {
    emptyRow(tbody, 9, "No se pudieron cargar los jugadores.");
    notice($("#playersNotice"), error.message, true);
    showToast(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function submitPrediction(event) {
  event?.preventDefault();
  const home = $("#predictHome").value;
  const away = $("#predictAway").value;
  const date = $("#predictDate").value;
  const box = $("#predictResult");
  if (!home || !away || !date) return;
  if (home === away) {
    notice($("#predictNotice"), "Elige dos equipos distintos.", true);
    return;
  }
  notice($("#predictNotice"), "");
  box.innerHTML = '<div class="empty"><span class="spinner"></span>Calculando predicción…</div>';
  try {
    const result = await api("/api/predict", { home, away, date, line: state.line });
    const probability = Number(result.probability_over);
    const low = Number(result.interval_80[0]);
    const high = Number(result.interval_80[1]);
    box.dataset.ready = "1";
    box.innerHTML = `
      <div class="result-hero">
        <div class="result-total">
          <div class="k">Total previsto</div>
          <div class="big">${num(result.predicted_total)}<small> pts</small></div>
          <div class="sub">${esc(result.home)} (local) vs ${esc(result.away)} · ${esc(result.as_of)}</div>
          <div class="prob-bar"><div class="prob-over" style="width:${Math.max(0, Math.min(100, probability * 100))}%"></div></div>
          <div class="prob-labels"><span>UNDER ${pct(1 - probability)}</span><span>OVER ${pct(probability)}</span></div>
        </div>
        <div class="result-side">
          <div class="result-line"><span>Intervalo 80%</span><strong>${num(low, 0)} – ${num(high, 0)}</strong></div>
          <div class="result-line"><span>Línea</span><strong>${num(result.line, 1)}</strong></div>
          <div class="result-line"><span>Señal</span><strong>${leanPill(result.lean)}</strong></div>
          <div class="result-line"><span>Puntos previstos</span><strong>${esc(result.home)} ${num(result.projected_home_score)} · ${esc(result.away)} ${num(result.projected_away_score)}</strong></div>
          <div class="result-line"><span>Ganador probable</span><strong>${esc(result.possible_winner || "—")}</strong></div>
          <div class="result-line"><span>Hándicap modelo</span><strong>${esc(result.model_handicap || "—")}</strong></div>
          <div class="result-line"><span>P(gana ${esc(result.home)})</span><strong>${pct(result.home_win_probability)}</strong></div>
          <div class="result-line"><span>Descanso</span><strong>${esc(result.home)} ${num(result.home_rest, 0)}d · ${esc(result.away)} ${num(result.away_rest, 0)}d</strong></div>
          <div class="result-line"><span>Elo</span><strong>${num(result.home_elo, 0)} · ${num(result.away_elo, 0)}</strong></div>
          <div class="result-line"><span>Último partido</span><strong>${esc(result.home_last_game)} · ${esc(result.away_last_game)}</strong></div>
        </div>
      </div>
      ${result.analysis ? `<p class="analysis-text predict-analysis">${esc(result.analysis)}</p>` : ""}`;
    notice($("#predictNotice"), result.warning || "");
  } catch (error) {
    box.innerHTML = "";
    notice($("#predictNotice"), error.message, true);
    showToast(error.message);
  }
}

function activateTab(name) {
  state.active = name;
  $$(".tab").forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $$(".tab-panel").forEach((panel) => {
    const active = panel.id === `tab-${name}`;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
  if (name === "upcoming") loadUpcoming(state.loaded.upcoming);
  if (name === "analysis") loadAnalysis(state.loaded.analysis);
  if (name === "results") loadResults(state.loaded.results);
  if (name === "players" && !state.loaded.players) loadPlayers();
}

function bindEvents() {
  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
  });

  $("#lineInput").addEventListener("change", (event) => setLine(event.target.value));
  $("#lineDown").addEventListener("click", () => setLine(state.line - 0.5));
  $("#lineUp").addEventListener("click", () => setLine(state.line + 0.5));

  $("#upcomingRefresh").addEventListener("click", loadUpcoming);
  $("#upcomingDays").addEventListener("change", (event) => {
    state.upcoming.days = Number(event.target.value);
    loadUpcoming();
  });
  $("#upcomingTeam").addEventListener("change", (event) => {
    state.upcoming.team = event.target.value;
    loadUpcoming();
  });

  $("#analysisRefresh").addEventListener("click", () => loadAnalysis());
  $("#analysisDays").addEventListener("change", (event) => {
    state.analysis.days = Number(event.target.value);
    loadAnalysis();
  });
  $("#analysisTeam").addEventListener("change", (event) => {
    state.analysis.team = event.target.value;
    loadAnalysis();
  });

  $("#resultsRefresh").addEventListener("click", loadResults);
  $("#resultsSeason").addEventListener("change", (event) => {
    state.results.season = event.target.value;
    loadResults();
  });
  $("#resultsTeam").addEventListener("change", (event) => {
    state.results.team = event.target.value;
    loadResults();
  });
  $("#resultsLimit").addEventListener("change", (event) => {
    state.results.limit = Number(event.target.value);
    loadResults();
  });

  $("#playersRefresh").addEventListener("click", loadPlayers);
  $("#playersSeason").addEventListener("change", (event) => {
    state.players.season = event.target.value;
    state.players.minGp = event.target.value === "2025-26" ? 10 : 1;
    $("#playersMinGp").value = String(state.players.minGp);
    loadPlayers();
  });
  $("#playersMode").addEventListener("change", (event) => {
    state.players.mode = event.target.value;
    loadPlayers();
  });
  $("#playersSort").addEventListener("change", (event) => {
    state.players.sort = event.target.value;
    loadPlayers();
  });
  $("#playersTeam").addEventListener("change", (event) => {
    state.players.team = event.target.value;
    loadPlayers();
  });
  $("#playersMinGp").addEventListener("change", (event) => {
    const value = Number(event.target.value);
    state.players.minGp = Math.min(82, Math.max(1, value || 1));
    event.target.value = String(state.players.minGp);
    loadPlayers();
  });
  $("#playersLimit").addEventListener("change", (event) => {
    state.players.limit = Number(event.target.value);
    loadPlayers();
  });
  let searchTimer;
  $("#playersSearch").addEventListener("input", (event) => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.players.q = event.target.value.trim();
      loadPlayers();
    }, 350);
  });

  $("#predictForm").addEventListener("submit", submitPrediction);

  // Refresco silencioso: la NBA actualiza el marcador y los finales aparecen solos.
  window.setInterval(() => {
    if (document.visibilityState !== "visible") return;
    if (state.active === "upcoming") loadUpcoming(true);
    if (state.active === "analysis") loadAnalysis(true);
    if (state.active === "results") loadResults(true);
  }, 60000);
}

async function init() {
  bindEvents();
  try {
    const meta = await api("/api/meta");
    state.meta = meta;
    state.line = Number(meta.default_line || 225.5);
    $("#lineInput").value = state.line.toFixed(1);
    const modelName = String(meta.model_name || "Ridge");
    $("#statusBadge").textContent = `${modelName.charAt(0).toUpperCase()}${modelName.slice(1)} · hasta ${meta.trained_through}`;
    populateTeams(meta);

    if (meta.current_season === "2026-27") {
      state.results.season = "2026-27";
      $("#resultsSeason").value = "2026-27";
    }

    const playerSeasons = meta.players_seasons || [];
    const playerSeason = playerSeasons.includes(meta.current_season)
      ? meta.current_season
      : playerSeasons[0] || "2025-26";
    state.players.season = playerSeason;
    state.players.minGp = playerSeason === "2025-26" ? 10 : 1;
    $("#playersSeason").value = playerSeason;
    const playersMin = $("#playersMinGp");
    playersMin.max = "82";
    playersMin.value = String(state.players.minGp);

    const dateInput = $("#predictDate");
    dateInput.min = meta.min_date;
    dateInput.value = meta.today >= meta.min_date ? meta.today : meta.min_date;

    // En el móvil, 7 días suponen demasiadas filas; empezamos con 3.
    if (window.matchMedia("(max-width: 640px)").matches) {
      state.upcoming.days = 3;
      $("#upcomingDays").value = "3";
      state.analysis.days = 3;
      $("#analysisDays").value = "3";
    }

    await loadUpcoming();
  } catch (error) {
    $("#statusBadge").textContent = "API no disponible";
    showToast(error.message);
    const tbody = $("#upcomingBody");
    if (tbody) emptyRow(tbody, 8, "No se pudo conectar con la API.");
  }
}

document.addEventListener("DOMContentLoaded", init);
