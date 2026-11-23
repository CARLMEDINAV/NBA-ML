

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const PLAYER_SORT_LABELS = { pra: "PRA", pts: "PTS", reb: "REB", ast: "AST" };
const PROP_GRADE_LABELS = { A: "Muy probable", B: "Probable", C: "Media", D: "Baja" };
const NBA_TEAMS_FALLBACK = [{"abbreviation":"ATL","name":"Atlanta Hawks"},{"abbreviation":"BOS","name":"Boston Celtics"},{"abbreviation":"BKN","name":"Brooklyn Nets"},{"abbreviation":"CHA","name":"Charlotte Hornets"},{"abbreviation":"CHI","name":"Chicago Bulls"},{"abbreviation":"CLE","name":"Cleveland Cavaliers"},{"abbreviation":"DAL","name":"Dallas Mavericks"},{"abbreviation":"DEN","name":"Denver Nuggets"},{"abbreviation":"DET","name":"Detroit Pistons"},{"abbreviation":"GSW","name":"Golden State Warriors"},{"abbreviation":"HOU","name":"Houston Rockets"},{"abbreviation":"IND","name":"Indiana Pacers"},{"abbreviation":"LAC","name":"LA Clippers"},{"abbreviation":"LAL","name":"Los Angeles Lakers"},{"abbreviation":"MEM","name":"Memphis Grizzlies"},{"abbreviation":"MIA","name":"Miami Heat"},{"abbreviation":"MIL","name":"Milwaukee Bucks"},{"abbreviation":"MIN","name":"Minnesota Timberwolves"},{"abbreviation":"NOP","name":"New Orleans Pelicans"},{"abbreviation":"NYK","name":"New York Knicks"},{"abbreviation":"OKC","name":"Oklahoma City Thunder"},{"abbreviation":"ORL","name":"Orlando Magic"},{"abbreviation":"PHI","name":"Philadelphia 76ers"},{"abbreviation":"PHX","name":"Phoenix Suns"},{"abbreviation":"POR","name":"Portland Trail Blazers"},{"abbreviation":"SAC","name":"Sacramento Kings"},{"abbreviation":"SAS","name":"San Antonio Spurs"},{"abbreviation":"TOR","name":"Toronto Raptors"},{"abbreviation":"UTA","name":"Utah Jazz"},{"abbreviation":"WAS","name":"Washington Wizards"}];

const nf1 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });
const pf = new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 1 });

const state = {
  meta: null,
  line: 225.5,
  active: "upcoming",
  loaded: { upcoming: false, analysis: false, external: false, results: false, players: false, props: false, comparison: false },
  comparisonModel: "ridge",
  upcoming: { days: 5, team: "", requestId: 0 },
  analysis: { days: 7, team: "" },
  results: { season: "2026-27", team: "", limit: 25, window: 200 },
  players: {
    season: "2026-27",
    mode: "pergame",
    sort: "pra",
    team: "",
    minGp: 1,
    limit: 25,
    q: "",
  },
  props: { days: 5, team: "", limit: 8, date: "", gameId: "", players: [], selectedPlayerId: "", selectedStat: "pts", line: null, card: null, cardCache: {}, requestId: 0 },
  detail: {
    playerId: null,
    data: null,
    stat: "pts",
    line: null,
    range: 10,
    venue: "all",
    opener: null,
    requestId: 0,
  },
};

const DETAIL_STATS = [
  { key: "pts", label: "PTS" },
  { key: "reb", label: "REB" },
  { key: "ast", label: "AST" },
  { key: "pts_ast", label: "PTS+AST" },
  { key: "pts_reb", label: "PTS+REB" },
  { key: "reb_ast", label: "REB+AST" },
  { key: "pra", label: "P+R+A" },
  { key: "min", label: "MIN" },
  { key: "stl", label: "STL" },
  { key: "blk", label: "BLK" },
  { key: "fg3m", label: "3PM" },
  { key: "stl_blk", label: "STL+BLK" },
  { key: "dd", label: "DD" },
  { key: "td", label: "TD" },
];

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

function availabilityText(availability) {
  if (!availability || !availability.model_adjusted) return "";
  const parts = [];
  for (const side of [availability.home, availability.away]) {
    if (!side || !(Number(side.missing_share) > 0)) continue;
    const names = (side.players || [])
      .map((player) => player.player_name)
      .filter(Boolean)
      .slice(0, 2);
    const team = side.team || "Equipo";
    const impact = signed(side.impact_points, 1);
    parts.push(`${team} ${impact} pts${names.length ? ` (${names.join(", ")})` : ""}`);
  }
  return parts.join(" · ");
}

function availabilityHtml(availability) {
  const text = availabilityText(availability);
  if (!text) return "";
  return `<span class="injury-adjust-badge" title="${esc(availability.note || "")}">Ajuste por bajas</span><span class="injury-adjust-text">${esc(text)}</span>`;
}

function winProbabilityCell(game) {
  const homeProbability = Number(game.home_win_probability);
  if (!Number.isFinite(homeProbability)) return "—";
  const awayProbability = 1 - homeProbability;
  const adjusted = Boolean(game.availability?.model_adjusted);
  const base =
    game.base_home_win_probability === null ||
    game.base_home_win_probability === undefined
      ? null
      : Number(game.base_home_win_probability);
  const baseAway = adjusted && base !== null ? 1 - base : null;
  const baseLines = (value) =>
    value === null ? "" : `<small class="win-prob-base">base ${pct(value)}</small>`;
  return `<div class="win-probability-cell">
    <span class="win-prob-away"><small>VISITANTE</small><strong>${pct(awayProbability)}</strong>${baseLines(baseAway)}</span>
    <span class="win-prob-home"><small>LOCAL</small><strong>${pct(homeProbability)}</strong>${baseLines(adjusted ? base : null)}</span>
  </div>`;
}


const NBA_DISPLAY_TIME_ZONE = "America/Santiago";
const NBA_SOURCE_TIME_ZONE = "America/New_York";
const nbaSourceTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: NBA_SOURCE_TIME_ZONE,
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const nbaChileTimeFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: NBA_DISPLAY_TIME_ZONE,
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

function nbaChileGameDateTime(dateValue, timeValue) {
  if (!dateValue || !timeValue) return null;
  const dateMatch = String(dateValue).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  const timeMatch = String(timeValue).match(/^(\d{1,2}):(\d{2})/);
  if (!dateMatch || !timeMatch) return null;

  const [, year, month, day] = dateMatch;
  const hour = Number(timeMatch[1]);
  const minute = Number(timeMatch[2]);
  if (hour > 23 || minute > 59) return null;

  const targetUtc = Date.UTC(Number(year), Number(month) - 1, Number(day), hour, minute);
  let instant = targetUtc;

  // Interpret the schedule's local Eastern time correctly across EST/EDT.
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const parts = Object.fromEntries(
      nbaSourceTimeFormatter.formatToParts(new Date(instant))
        .filter((part) => part.type !== "literal")
        .map((part) => [part.type, Number(part.value)]),
    );
    const representedUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute);
    const correction = targetUtc - representedUtc;
    instant += correction;
    if (correction === 0) break;
  }

  const chileParts = Object.fromEntries(
    nbaChileTimeFormatter.formatToParts(new Date(instant))
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return {
    date: `${chileParts.year}-${chileParts.month}-${chileParts.day}`,
    time: `${chileParts.hour}:${chileParts.minute}`,
  };
}

function chileGameDate(dateValue, timeValue) {
  return nbaChileGameDateTime(dateValue, timeValue)?.date || dateValue;
}

function chileGameTime(dateValue, timeValue) {
  return nbaChileGameDateTime(dateValue, timeValue)?.time || timeValue || "";
}

window.NBATime = {
  date: chileGameDate,
  time: chileGameTime,
};

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

const NBA_TEAM_IDS = {
  ATL:1610612737,BOS:1610612738,CLE:1610612739,NOP:1610612740,CHI:1610612741,DAL:1610612742,DEN:1610612743,GSW:1610612744,HOU:1610612745,LAC:1610612746,LAL:1610612747,MIA:1610612748,MIL:1610612749,MIN:1610612750,BKN:1610612751,NYK:1610612752,ORL:1610612753,IND:1610612754,PHI:1610612755,PHX:1610612756,POR:1610612757,SAC:1610612758,SAS:1610612759,OKC:1610612760,TOR:1610612761,UTA:1610612762,MEM:1610612763,WAS:1610612764,DET:1610612765,CHA:1610612766
};
function nbaTeamLogoUrl(team) {
  const id = NBA_TEAM_IDS[String(team || "").toUpperCase()];
  return id ? `https://cdn.nba.com/logos/nba/${id}/global/L/logo.svg` : "";
}
function nbaTeamLogoMarkup(team, label = "") {
  const code = String(team || "").toUpperCase();
  const url = nbaTeamLogoUrl(code);
  return `<span class="nba-team-logo-wrap" title="${esc(label || code)}"><span class="nba-team-logo-fallback">${esc(code || "NBA")}</span>${url ? `<img class="nba-team-logo" src="${esc(url)}" alt="${esc(label || code)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}</span>`;
}
function nbaPlayerHeadshotUrl(playerId) {
  const id = Number(playerId);
  return Number.isFinite(id) && id > 0 ? `https://cdn.nba.com/headshots/nba/latest/260x190/${id}.png` : "";
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

function dayMonth(value) {
  if (!value) return "—";
  const date = new Date(`${value}T12:00:00`);
  return date
    .toLocaleDateString("es-ES", { day: "numeric", month: "short" })
    .replace(".", "");
}

function pdNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function pdStatValue(game, key) {
  const pts = pdNumber(game.pts);
  const reb = pdNumber(game.reb);
  const ast = pdNumber(game.ast);
  const stl = pdNumber(game.stl);
  const blk = pdNumber(game.blk);
  switch (key) {
    case "pts_ast":
      return pts + ast;
    case "pts_reb":
      return pts + reb;
    case "reb_ast":
      return reb + ast;
    case "pra":
      return Number.isFinite(Number(game.pra)) ? Number(game.pra) : pts + reb + ast;
    case "stl_blk":
      return stl + blk;
    case "dd":
      return [pts, reb, ast].filter((value) => value >= 10).length >= 2 ? 1 : 0;
    case "td":
      return [pts, reb, ast].filter((value) => value >= 10).length >= 3 ? 1 : 0;
    default:
      return pdNumber(game[key]);
  }
}

function pdAverageFor(data, key) {
  const averages = data.player?.averages || {};
  const value = (() => {
    switch (key) {
      case "pts_ast":
        return Number(averages.pts) + Number(averages.ast);
      case "pts_reb":
        return Number(averages.pts) + Number(averages.reb);
      case "reb_ast":
        return Number(averages.reb) + Number(averages.ast);
      case "pra":
        return Number(averages.pra);
      case "stl_blk":
        return Number(averages.stl) + Number(averages.blk);
      case "dd":
      case "td":
        return null;
      default:
        return averages[key];
    }
  })();
  return Number.isFinite(Number(value)) && value !== null ? Number(value) : null;
}

function pdProjectionFor(data, key) {
  const projection = data.upcoming?.projection;
  if (!projection) return null;
  const pts = Number(projection.pts);
  const reb = Number(projection.reb);
  const ast = Number(projection.ast);
  const value = (() => {
    switch (key) {
      case "pts_ast":
        return pts + ast;
      case "pts_reb":
        return pts + reb;
      case "reb_ast":
        return reb + ast;
      case "pra":
        return Number(projection.pra);
      default:
        return projection[key];
    }
  })();
  return Number.isFinite(Number(value)) && value !== null ? Number(value) : null;
}

function pdDefaultLine(data, key) {
  if (key === "dd" || key === "td") return 0.5;
  const projection = pdProjectionFor(data, key);
  const average = pdAverageFor(data, key);
  const value = projection ?? average;
  if (value !== null) return Math.max(0.5, Math.round(value * 2) / 2);
  const values = (data.games || []).map((game) => pdStatValue(game, key));
  if (!values.length) return 0.5;
  const mean = values.reduce((sum, item) => sum + item, 0) / values.length;
  return Math.max(0.5, Math.round(mean * 2) / 2);
}

function detailGames(data) {
  const games = data.games || [];
  if (state.detail.venue === "home") return games.filter((game) => game.is_home);
  if (state.detail.venue === "away") return games.filter((game) => !game.is_home);
  return games;
}

function detailVisibleGames(data) {
  const games = detailGames(data);
  return state.detail.range === "all" ? games : games.slice(0, state.detail.range);
}

function detailStatLabel() {
  return DETAIL_STATS.find((item) => item.key === state.detail.stat)?.label || "PTS";
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

function propGradeInfo(player, fallbackSigma) {
  let confidence = Number(player.confidence);
  let grade = String(player.grade || "").toUpperCase();
  if (!Number.isFinite(confidence) || !PROP_GRADE_LABELS[grade]) {
    const sigma = Number(fallbackSigma || 0);
    const projected = Number(player.projected_pra || 0);
    confidence =
      sigma > 0 && projected > 0
        ? Math.max(0, Math.min(1, 1 - sigma / projected))
        : 0;
    const percentage = Math.round(confidence * 100);
    grade = percentage >= 80 ? "A" : percentage >= 65 ? "B" : percentage >= 50 ? "C" : "D";
  }
  return { confidence, grade };
}

function propGradeBadge(player, fallbackSigma) {
  const { confidence, grade } = propGradeInfo(player, fallbackSigma);
  const percentage = Math.round(confidence * 100);
  const label = PROP_GRADE_LABELS[grade] || "Baja";
  return `<span class="prop-grade prop-grade-${grade.toLowerCase()}" title="${esc(
    `${label}: ${percentage}% de confianza del modelo sobre PRA`
  )}"><b>${grade}</b><small>${percentage}%</small></span>`;
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

function loadingRows(tbody, columns, rows = 6, message = "Cargando…") {
  if (rows <= 1) {
    loadingRow(tbody, columns, message);
    return;
  }
  tbody.innerHTML = Array.from({ length: rows }, (_, index) => {
    if (index === 0) {
      return `<tr class="loading skeleton-row"><td colspan="${columns}"><span class="spinner"></span>${esc(message)}</td></tr>`;
    }
    return `<tr class="loading skeleton-row" aria-hidden="true"><td colspan="${columns}"><span class="skeleton-bar"></span></td></tr>`;
  }).join("");
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
  const response = await fetch(url, { headers: { Accept: "application/json" }, cache: path === "/api/player-props" ? "no-store" : "default" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => typeof item === "string" ? item : item?.msg || JSON.stringify(item)).join(" · ")
        : detail && typeof detail === "object"
          ? detail.message || detail.msg || JSON.stringify(detail)
          : `Error ${response.status} al consultar la API`;
    throw new Error(message);
  }
  return data;
}


function fillTeams(select, teams, includeAll = false) {
  // Algunas secciones opcionales pueden no estar montadas en esta versión
  // de la interfaz. Su ausencia nunca debe interrumpir la carga global.
  if (!select) return;
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
  // Evita selectores vacíos si /api/meta no entrega el catálogo completo.
  const teams = Array.isArray(meta.teams) && meta.teams.length === 30
    ? meta.teams
    : NBA_TEAMS_FALLBACK;
  fillTeams($("#upcomingTeam"), teams, true);
  fillTeams($("#analysisTeam"), teams, true);
  fillTeams($("#externalTeam"), teams, true);
  fillTeams($("#resultsTeam"), teams, true);
  fillTeams($("#playersTeam"), teams, true);
  fillTeams($("#injuriesTeam"), teams, true);
  fillTeams($("#propsTeam"), teams, true);
  fillTeams($("#positionVsTeam"), teams);
  const positionVsPlaceholder = document.createElement("option");
  positionVsPlaceholder.value = "";
  positionVsPlaceholder.textContent = "Selecciona rival";
  $("#positionVsTeam")?.prepend(positionVsPlaceholder);
  if ($("#positionVsTeam")) $("#positionVsTeam").value = "";
  fillTeams($("#teamImpactTeam"), teams);
  const teamImpactSelect = $("#teamImpactTeam");
  if (teamImpactSelect && !teamImpactSelect.value && teamImpactSelect.options.length) {
    teamImpactSelect.selectedIndex = 0;
  }
  if (state.active === "teams" && teamImpactSelect?.value) {
    loadTeamImpactPlayers();
  }
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

const NBA_LOGO_SLUGS = {ATL:"atl",BOS:"bos",BKN:"bkn",CHA:"cha",CHI:"chi",CLE:"cle",DAL:"dal",DEN:"den",DET:"det",GSW:"gs",HOU:"hou",IND:"ind",LAC:"lac",LAL:"lal",MEM:"mem",MIA:"mia",MIL:"mil",MIN:"min",NOP:"no",NYK:"ny",OKC:"okc",ORL:"orl",PHI:"phi",PHX:"phx",POR:"por",SAC:"sac",SAS:"sa",TOR:"tor",UTA:"utah",WAS:"wsh"};
function upcomingTeamLogo(team) { const slug = NBA_LOGO_SLUGS[String(team || "").toUpperCase()]; return slug ? '<img class="upcoming-team-logo" src="https://a.espncdn.com/i/teamlogos/nba/500/' + slug + '.png" alt="" loading="lazy" onerror="this.hidden=true">' : ""; }
function upcomingCard(game) {
  const side = String(game.lean || "").toUpperCase();
  const tone = side === "OVER" ? "over" : side === "UNDER" ? "under" : "neutral";
  const raw = Number(game.probability_over);
  const probability = Number.isFinite(raw) ? Math.max(0, Math.min(1, raw > 1 ? raw / 100 : raw)) : null;
  const width = probability === null ? 0 : Math.round(probability * 100);
  return '<article class="upcoming-game-card signal-' + tone + '"><div class="upcoming-card-top"><span>' + esc(shortDate(chileGameDate(game.date, game.time_et))) + ' · ' + esc(chileGameTime(game.date, game.time_et) || "Hora por confirmar") + '</span><span class="pill ' + (tone === "over" ? "pill-over" : tone === "under" ? "pill-under" : "pill-neutral") + '">' + esc(side || "NEUTRAL") + '</span></div>' +
    '<div class="upcoming-matchup"><div class="upcoming-team">' + upcomingTeamLogo(game.away) + '<strong>' + esc(game.away || "Visitante") + '</strong><small>VISITANTE</small></div><span class="upcoming-at">@</span><div class="upcoming-team">' + upcomingTeamLogo(game.home) + '<strong>' + esc(game.home || "Local") + '</strong><small>LOCAL</small></div></div>' +
    '<div class="upcoming-total"><span>Total proyectado</span><strong>' + num(game.predicted_total) + '</strong></div><div class="upcoming-card-stats"><div><small>IC 80%</small><strong>' + num(game.interval_low,0) + "–" + num(game.interval_high,0) + '</strong></div><div><small>P(over)</small><strong>' + pct(game.probability_over) + '</strong></div><div><small>Hándicap</small><strong>' + esc(game.model_handicap || "—") + '</strong></div></div>' +
    '<div class="upcoming-probability"><div><span>Probabilidad OVER</span><b>' + (probability === null ? "—" : width + "%") + '</b></div><div class="upcoming-probability-track"><i style="width:' + width + '%"></i></div></div></article>';
}
function setUpcomingView(view, persist) {
  const selected = "cards";
  const cards = $("#upcomingCards"), table = $(".upcoming-table-wrap");
  if (!cards || !table) return;
  cards.hidden = selected !== "cards"; table.hidden = selected !== "table";
  $$("[data-upcoming-view]").forEach((button) => { const active = button.dataset.upcomingView === selected; button.classList.toggle("is-active", active); button.setAttribute("aria-pressed", String(active)); });
  if (persist !== false) { try { localStorage.setItem("nba-upcoming-view", selected); } catch (_) {} }
}
async function loadUpcoming(silent = false) {
  const requestId = ++state.upcoming.requestId;
  const tbody = $("#upcomingBody");
  const button = $("#upcomingRefresh");
  if (!silent) {
    $("#upcomingCards").innerHTML = Array.from({length: 4}, () => '<div class="upcoming-card-skeleton"><i></i><i></i><i></i><i></i></div>').join("");
    loadingRows(tbody, 9, 6);
    setBusy(button, true);
    notice($("#upcomingNotice"), "");
    $("#upcomingNote").textContent = "";
    $("#upcomingNote").classList.remove("is-warning");
  }
  try {
    const data = await api("/api/upcoming", {
      days: state.upcoming.days,
      team: state.upcoming.team,
      line: state.line,
      live: 1,
      injuries: 1,
    });
    if (requestId !== state.upcoming.requestId) return;
    state.loaded.upcoming = true;
    $("#upcomingRange").textContent = `${data.start} → ${data.end}`;
    if (!data.games.length) {
      $("#upcomingCards").innerHTML = '<div class="empty upcoming-empty">No hay partidos programados en ese rango para el filtro elegido.</div>';
      emptyRow(tbody, 9, "No hay partidos programados en ese rango para el filtro elegido.");
      $("#upcomingNote").textContent = [
        "Prueba a ampliar los días o quitar el filtro de equipo.",
        data.warning || "",
      ]
        .filter(Boolean)
        .join(" ");
      $("#upcomingNote").classList.toggle("is-warning", Boolean(data.warning));
      return;
    }

    $("#upcomingCards").innerHTML = data.games.map(upcomingCard).join("");
    tbody.innerHTML = data.games
      .map(
        (game) => `
        <tr>
          <td>${esc(shortDate(chileGameDate(game.date, game.time_et)))}</td>
          <td>${esc(chileGameTime(game.date, game.time_et) || "—")}</td>
          <td><span class="team">${esc(game.away)}</span> <span class="vs">@</span> <span class="team">${esc(game.home)}</span></td>
          <td class="num">${num(game.predicted_total)}</td>
          <td>${num(game.interval_low, 0)} – ${num(game.interval_high, 0)}</td>
          <td class="num">${pct(game.probability_over)}</td>
          <td class="num">${winProbabilityCell(game)}</td>
          <td>${leanPill(game.lean)}</td>
          <td>${esc(game.model_handicap || "—")}</td>
        </tr>`
      )
      .join("");
    const adjustedCount = Number(data.injury_adjusted_games || 0);
    const injuryNote = adjustedCount
      ? ` · ${adjustedCount} partido${adjustedCount === 1 ? "" : "s"} con ajuste por bajas`
      : "";
    const upcomingNote = `${data.count} partidos · línea ${num(data.line, 1)} · P(over) calculada con los residuos fuera de muestra${injuryNote}.${data.live_updated ? ` · actualizado ${data.live_updated}` : ""}`;
    $("#upcomingNote").textContent = [upcomingNote, data.warning, data.injuries_warning]
      .filter(Boolean)
      .join(" ");
    $("#upcomingNote").classList.toggle(
      "is-warning",
      Boolean(data.warning || data.injuries_warning),
    );
  } catch (error) {
    if (requestId !== state.upcoming.requestId) return;
    if (!silent) {
      $("#upcomingCards").innerHTML = '<div class="upcoming-error"><strong>No se pudieron cargar los partidos.</strong><p>Comprueba la conexión y vuelve a intentarlo.</p><button type="button" class="btn btn-ghost" id="upcomingRetry">Reintentar</button></div>';
      emptyRow(tbody, 9, "No se pudieron cargar los próximos partidos.");
      $("#upcomingRetry")?.addEventListener("click", () => loadUpcoming());
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
      injuries: 1,
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
      summaryCard("Cuotas", data.market_source || "—", data.market_source ? "líneas Over/Under" : "sin mercado"),
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
          <td>${esc(chileGameTime(game.date, game.time_et) || "—")}</td>
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
              <span class="analysis-date">${esc(shortDate(chileGameDate(game.date, game.time_et)))} · ${esc(game.time_et || "hora por confirmar")}</span>
              <h3>${esc(game.home_name || game.home)} <span class="vs">vs</span> ${esc(game.away_name || game.away)}</h3>
            </div>
            ${leanPill(side)}
          </div>
          <div class="analysis-metrics">
            <div><span>Puntos proyectados</span><strong>Local ${num(game.projected_home_score)} · Visitante ${num(game.projected_away_score)}</strong></div>
            <div><span>Total previsto</span><strong>${num(game.predicted_total)}</strong></div>
            <div><span>Posible ganador</span><strong>${esc(game.possible_winner || "—")}</strong></div>
            <div><span>Hándicap modelo</span><strong>${esc(game.model_handicap || "—")}</strong></div>
            <div><span>P(gana local)</span><strong>${pct(game.home_win_probability)}${game.availability?.model_adjusted ? ` <small>base ${pct(game.base_home_win_probability)}</small>` : ""}</strong></div>
            <div><span>Ajuste por bajas</span><strong>${availabilityHtml(game.availability) || "Sin bajas clave"}</strong></div>
            <div><span>Mercado</span><strong>${esc(game.market_handicap || "Sin cuota")}${hasMarketTotal ? ` · ${num(game.market_total)}` : ""}</strong></div>
            <div><span>Cuotas over/under</span><strong>${esc(game.over_odds || "—")} / ${esc(game.under_odds || "—")}</strong></div>
            <div><span>Comparación de casas</span><strong>${(game.market_bookmakers || []).length ? (game.market_bookmakers || []).map((book) => `${esc(book.bookmaker)} ${num(book.line)} · O ${Number.isFinite(Number(book.over_odds)) ? Number(book.over_odds).toFixed(2) : "—"} / U ${Number.isFinite(Number(book.under_odds)) ? Number(book.under_odds).toFixed(2) : "—"}`).join(" | ") : "No disponible"}</strong></div>
          </div>
          <p class="analysis-text">${esc(game.analysis || "Sin análisis disponible.")}</p>
        </article>`;
      })
      .join("");

    const source = data.market_source
      ? ` Cuotas: ${data.market_source}. En la línea de mercado, Alta/Baja = probabilidad ≥55% / ≤45%; Leve = 50-55%.`
      : " Sin cuotas de mercado para esas fechas.";
    const adjustedCount = Number(data.injury_adjusted_games || 0);
    const injuryNote = adjustedCount
      ? ` ${adjustedCount} partido${adjustedCount === 1 ? "" : "s"} con ajuste por bajas confirmadas; el total del partido no se ajusta.`
      : "";
    $("#analysisNote").textContent = `${games.length} partidos · línea de referencia ${num(data.line, 1)}.${source}${injuryNote} ${data.handicap_note || ""}`;
    notice($("#analysisNotice"), [data.warning, data.injuries_warning].filter(Boolean).join(" "));
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



function resultOutcomePill(hit) {
  if (hit === true) return '<span class="results-outcome is-hit">ACIERTO</span>';
  if (hit === false) return '<span class="results-outcome is-miss">FALLO</span>';
  return '<span class="results-outcome is-na">N/D</span>';
}
function resultsHandicapGrade(game) {
  const handicap = String(game.model_handicap || "").trim();
  const margin = Number(game.actual_margin);
  if (!handicap || !Number.isFinite(margin)) return null;
  if (/^pick.?em$/i.test(handicap)) {
    if (Math.abs(margin) < 0.0001) return "push";
    return game.possible_winner && game.actual_winner && String(game.possible_winner).trim().toLowerCase() === String(game.actual_winner).trim().toLowerCase() ? "hit" : "miss";
  }
  const match = handicap.match(/^(.*?)\s+-([\d.]+)$/);
  if (!match) return null;
  const favorite = match[1].trim().toLowerCase();
  const points = Number(match[2]);
  if (!Number.isFinite(points)) return null;
  const isHome = favorite === String(game.home_name || "").trim().toLowerCase();
  const isAway = favorite === String(game.away_name || "").trim().toLowerCase();
  if (!isHome && !isAway) return null;
  const difference = (isHome ? margin : -margin) - points;
  return Math.abs(difference) < 0.0001 ? "push" : difference > 0 ? "hit" : "miss";
}
function resultsCard(game) {
  const total = Number(game.total_points);
  const line = Number(game.line);
  const lean = String(game.model_lean || "").toUpperCase();
  const overHit = game.actual_result === "PUSH" ? "push" : game.hit === 1 ? true : game.hit === 0 ? false : null;
  const handicapGrade = resultsHandicapGrade(game);
  const winnerHit = game.possible_winner && game.actual_winner
    ? String(game.possible_winner).trim().toLowerCase() === String(game.actual_winner).trim().toLowerCase()
    : null;
  const handicapHit = handicapGrade === "hit" ? true : handicapGrade === "miss" ? false : null;
  const handicapOutcome = handicapGrade === "push"
    ? '<span class="results-outcome is-push">PUSH</span>'
    : resultOutcomePill(handicapHit);
  const overOutcome = overHit === "push"
    ? '<span class="results-outcome is-push">PUSH</span>'
    : resultOutcomePill(overHit);
  const actualSide = game.actual_result || "—";
  const predictedSide = lean || "SIN SEÑAL";
  const homeScore = Number(game.home_score);
  const awayScore = Number(game.away_score);
  const scoreMarkup = Number.isFinite(homeScore) && Number.isFinite(awayScore)
    ? '<strong>' + num(awayScore, 0) + '</strong><span>–</span><strong>' + num(homeScore, 0) + '</strong>'
    : '<strong>' + esc(game.away_team) + '</strong><span>vs</span><strong>' + esc(game.home_team) + '</strong>';
  return '<article class="results-game-card">' +
    '<div class="results-card-head"><span>' + esc(shortDate(game.game_date)) + '</span><span class="results-final-tag">FINAL</span></div>' +
    '<div class="results-matchup">' +
      '<div class="results-team results-away">' + upcomingTeamLogo(game.away_team) + '<span>' + esc(game.away_team) + '</span></div>' +
      '<div class="results-score">' + scoreMarkup + '</div>' +
      '<div class="results-team results-home">' + upcomingTeamLogo(game.home_team) + '<span>' + esc(game.home_team) + '</span></div>' +
    '</div>' +
    '<div class="results-card-total">' +
      '<span>Total real<strong>' + num(total, 0) + '</strong></span>' +
      '<span>Proyección<strong>' + num(game.predicted_total, 1) + '</strong></span>' +
      '<span>Línea<strong>' + (Number.isFinite(line) ? num(line, 1) : "—") + '</strong></span>' +
    '</div>' +
    '<div class="results-card-picks">' +
      '<div class="results-pick"><span class="results-pick-label">OVER / UNDER</span><strong>' + esc(predictedSide) + '</strong><small>Real: ' + esc(actualSide) + '</small>' + overOutcome + '</div>' +
      '<div class="results-pick"><span class="results-pick-label">HÁNDICAP</span><strong>' + esc(game.model_handicap || "Sin proyección") + '</strong><small>Margen real: ' + (game.actual_margin == null ? "—" : signed(game.actual_margin, 1)) + '</small>' + handicapOutcome + '</div>' +
      '<div class="results-pick"><span class="results-pick-label">GANADOR</span><strong>' + esc(game.possible_winner || "Sin proyección") + '</strong><small>Ganó: ' + esc(game.actual_winner || "—") + '</small>' + resultOutcomePill(winnerHit) + '</div>' +
    '</div></article>';
}

async function loadResults(silent = false) {
  // La selección visible es la fuente de verdad: evita consultar 2025-26
  // cuando la interfaz ya muestra 2026-27 (estado antiguo o caché previa).
  const selectedSeason = $("#resultsSeason")?.value;
  if (selectedSeason) state.results.season = selectedSeason;
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
      evaluation_window: state.results.window,
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

    const evaluation = data.evaluation || {};
    const evalSummary = evaluation.summary || {};
    $("#resultsEvalSummary").innerHTML = [
      summaryCard("Muestra evaluada", String(evaluation.window ?? 0), evaluation.requested_window === 0 ? "temporada completa" : "partidos más recientes"),
      summaryCard("MAE reciente", num(evalSummary.mae, 2), "puntos"),
      summaryCard("Brier reciente", num(evalSummary.brier, 3), "probabilidades"),
      summaryCard("Acierto direccional", pct(evalSummary.hit_rate), `${evalSummary.n_leans ?? 0} señales`),
      summaryCard("Sesgo reciente", signed(evalSummary.bias, 2), "previsto − real"),
    ].join("");
    $("#resultsCalibrationBody").innerHTML = (evaluation.calibration || []).map((bucket) => `
      <tr>
        <td>${esc(bucket.range)}</td>
        <td class="num">${bucket.n}</td>
        <td class="num">${pct(bucket.mean_probability)}</td>
        <td class="num">${pct(bucket.observed_over_rate)}</td>
        <td class="num">${bucket.calibration_gap == null ? "—" : signed(bucket.calibration_gap * 100, 1) + " pp"}</td>
        <td class="num">${num(bucket.brier, 3)}</td>
      </tr>`).join("") || '<tr><td colspan="6">Sin datos de calibración.</td></tr>';
    $("#resultsExtremesBody").innerHTML = (evaluation.error_extremes || []).map((game) => `
      <tr>
        <td>${esc(game.matchup)}</td>
        <td>${esc(shortDate(game.game_date))}</td>
        <td class="num">${num(game.predicted_total)}</td>
        <td class="num">${num(game.actual_total, 0)}</td>
        <td class="num">${signed(game.error, 1)}</td>
      </tr>`).join("") || '<tr><td colspan="5">Sin errores extremos disponibles.</td></tr>';
    $("#resultsEvalNote").textContent = "Brecha = porcentaje OVER observado menos P(over) media. Los rangos con pocos partidos son descriptivos, no evidencia de calibración. Los errores extremos identifican partidos para revisar; no demuestran por sí solos qué variable causó el fallo.";

    if (!data.games.length) {
      $("#resultsCards").innerHTML = "";
      emptyRow(tbody, 11, "Todavía no hay partidos con resultado para esta temporada o filtro.");
      $("#resultsNote").textContent = data.caveat || "";
      return;
    }

    $("#resultsCards").innerHTML = data.games.map(resultsCard).join("");
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


function modelDisplayName(name) {
  return ({
    ridge: "Ridge",
    random_forest: "Random Forest",
    hist_gradient_boosting: "HistGradientBoosting",
    baseline_media_liga: "Media de liga",
    baseline_forma_reciente: "Forma reciente",
  })[name] || name || "Modelo";
}

function comparisonMetricRows(rows) {
  return (rows || []).map((row) => `
    <tr class="${["ridge", "random_forest", "hist_gradient_boosting"].includes(row.model) ? "comparison-model-row" : ""}">
      <td><strong>${esc(modelDisplayName(row.model))}</strong></td>
      <td class="num">${num(row.n, 0)}</td>
      <td class="num">${num(row.mae, 3)}</td>
      <td class="num">${num(row.rmse, 3)}</td>
      <td class="num">${num(row.r2, 3)}</td>
      <td class="num">${signed(row.bias, 3)}</td>
      <td class="num">${pct(row.within_10)}</td>
    </tr>`).join("") || '<tr><td colspan="7">No hay métricas guardadas.</td></tr>';
}

async function loadModelComparison(silent = false) {
  const button = $("#modelComparisonRefresh");
  if (!silent) {
    setBusy(button, true);
    notice($("#modelComparisonNotice"), "");
    $("#modelValidationBody").innerHTML = '<tr><td colspan="7"><span class="spinner"></span> Cargando…</td></tr>';
    $("#modelTestBody").innerHTML = '<tr><td colspan="7"><span class="spinner"></span> Cargando…</td></tr>';
    $("#modelPredictionsBody").innerHTML = '<tr><td colspan="5"><span class="spinner"></span> Cargando…</td></tr>';
  }
  try {
    const data = await api("/api/model-comparison", {
      model: state.comparisonModel,
      limit: 30,
    });
    state.loaded.comparison = true;
    if (!data.available) {
      $("#modelComparisonSummary").innerHTML = "";
      $("#modelValidationBody").innerHTML = '<tr><td colspan="7">Comparación todavía no disponible.</td></tr>';
      $("#modelTestBody").innerHTML = '<tr><td colspan="7">Comparación todavía no disponible.</td></tr>';
      $("#modelPredictionsBody").innerHTML = '<tr><td colspan="5">Sin predicciones guardadas.</td></tr>';
      notice($("#modelComparisonNotice"), data.message || "Ejecuta un nuevo entrenamiento para generar la comparación de modelos.");
      $("#modelPredictionsNote").textContent = "";
      return;
    }

    $("#modelComparisonSummary").innerHTML = [
      summaryCard("Modelo seleccionado", modelDisplayName(data.best_model), "menor MAE en validación"),
      summaryCard("Validación", data.validation_season || "—", "selección del modelo"),
      summaryCard("Test", data.test_season || "—", "evaluación fuera de muestra"),
      summaryCard("Comparación", data.generated_at ? new Date(data.generated_at).toLocaleString("es-CL") : "—", "última ejecución"),
    ].join("");
    $("#modelValidationCaption").textContent = `Temporada ${data.validation_season || "—"} · se utiliza para elegir el modelo.`;
    $("#modelTestCaption").textContent = `Temporada ${data.test_season || "—"} · evaluación final, sin intervenir en la selección.`;
    $("#modelValidationBody").innerHTML = comparisonMetricRows(data.validation);
    $("#modelTestBody").innerHTML = comparisonMetricRows(data.test);

    const predictions = data.games || [];
    if (data.predictions_available && predictions.length) {
      $("#modelPredictionsBody").innerHTML = predictions.map((game) => `
        <tr>
          <td>${esc(shortDate(game.game_date))}</td>
          <td><span class="team">${esc(game.away_team)}</span> <span class="vs">@</span> <span class="team">${esc(game.home_team)}</span></td>
          <td class="num">${num(game.actual_total, 0)}</td>
          <td class="num">${num(game.predicted_total, 1)}</td>
          <td class="num ${Number(game.error) > 0 ? "miss" : Number(game.error) < 0 ? "hit" : ""}">${signed(game.error, 1)}</td>
        </tr>`).join("");
      $("#modelPredictionsNote").textContent = `Mostrando ${predictions.length} de ${data.count} partidos de test. Error = real − predicho; positivo indica subestimación.`;
    } else {
      $("#modelPredictionsBody").innerHTML = '<tr><td colspan="5">El artefacto actual no contiene el detalle por partido.</td></tr>';
      $("#modelPredictionsNote").textContent = "Ejecuta un nuevo entrenamiento y exporta los artefactos para guardar las predicciones individuales de los tres modelos.";
    }
    notice($("#modelComparisonNotice"), "");
  } catch (error) {
    notice($("#modelComparisonNotice"), error.message, true);
    $("#modelComparisonSummary").innerHTML = "";
  } finally {
    setBusy(button, false);
  }
}

const MODEL_LABELS = { ridge: "Ridge", random_forest: "Random Forest", hist_gradient_boosting: "HistGradientBoosting" };

function comparisonMetricRow(row) {
  const label = MODEL_LABELS[row.model] || row.model || "—";
  const winner = state.comparisonModel === row.model;
  return `<tr class="${winner ? "is-best-model" : ""}"><td><strong>${esc(label)}</strong>${winner ? ' <span class="pill pill-success">Mejor validación</span>' : ""}</td><td class="num">${num(row.mae, 2)}</td><td class="num">${num(row.rmse, 2)}</td><td class="num">${signed(row.bias, 2)}</td><td class="num">${pct(row.within_10)}</td><td class="num">${num(row.n, 0)}</td></tr>`;
}

async function loadModelComparison(silent = false) {
  const button = $("#modelComparisonRefresh");
  if (!silent) { setBusy(button, true); notice($("#modelComparisonNotice"), ""); }
  try {
    const data = await api("/api/model-comparison");
    state.loaded.comparison = true;
    state.comparisonModel = data.best_model || "ridge";
    const testRows = data.test || [];
    const bestTest = [...testRows].sort((a, b) => Number(a.mae) - Number(b.mae))[0];
    $("#modelComparisonSummary").innerHTML = [
      summaryCard("Modelo seleccionado", MODEL_LABELS[data.best_model] || data.best_model || "—", "Menor MAE en validación"),
      summaryCard("Mejor MAE de test", bestTest ? num(bestTest.mae, 2) : "—", bestTest ? (MODEL_LABELS[bestTest.model] || bestTest.model) : "Sin datos"),
      summaryCard("Partidos comparados", String(data.prediction_count || 0), data.test_season || "Test temporal"),
      summaryCard("Entrenamiento", data.generated_at && Number.isFinite(Date.parse(data.generated_at)) ? new Date(data.generated_at).toLocaleString("es-CL", { dateStyle: "short", timeStyle: "short" }) : "—", "Última ejecución registrada"),
    ].join("");
    $("#modelComparisonValidationBody").innerHTML = (data.validation || []).length ? data.validation.map(comparisonMetricRow).join("") : '<tr><td colspan="6">Sin métricas. Ejecuta un entrenamiento.</td></tr>';
    $("#modelComparisonTestBody").innerHTML = testRows.length ? testRows.map(comparisonMetricRow).join("") : '<tr><td colspan="6">Sin métricas de test disponibles.</td></tr>';
    const keys = ["ridge", "random_forest", "hist_gradient_boosting"];
    const rows = data.predictions || [];
    $("#modelComparisonPredictionsBody").innerHTML = rows.length ? rows.map(game => {
      const cells = keys.map(key => { const item = game.models?.[key]; return item ? `<td class="num">${num(item.predicted, 1)} <small class="muted">(${signed(item.error, 1)})</small></td>` : '<td class="num">—</td>'; }).join("");
      return `<tr><td>${esc(shortDate(chileGameDate(game.date, game.time_et)))}</td><td>${esc(game.matchup)}</td><td class="num">${num(game.actual, 1)}</td>${cells}</tr>`;
    }).join("") : '<tr><td colspan="6">No hay predicciones individuales exportadas. Ejecuta entrenamiento y exportación de artefactos.</td></tr>';
    const diagnostics = data.diagnostics || {};
    const diagnosticModels = diagnostics.models || [];
    $("#modelErrorSummaryBody").innerHTML = diagnosticModels.length ? diagnosticModels.map(row => {
      const label = MODEL_LABELS[row.model] || row.model;
      return `<tr><td><strong>${esc(label)}</strong></td><td class="num">${num(row.mae, 2)}</td><td class="num">${signed(row.bias, 2)}</td><td class="num">${num(row.underestimated, 0)}</td><td class="num">${num(row.overestimated, 0)}</td><td class="num">${pct(row.within_10)}</td><td class="num">${num(row.n, 0)}</td></tr>`;
    }).join("") : '<tr><td colspan="7">Sin datos suficientes para el diagnóstico.</td></tr>';
    const buckets = diagnostics.actual_buckets || [];
    $("#modelErrorBucketsBody").innerHTML = buckets.length ? buckets.map(bucket => {
      const values = ["ridge", "random_forest", "hist_gradient_boosting"].map(key => {
        const row = (bucket.models || []).find(item => item.model === key);
        return row ? `<td class="num">${num(row.mae, 2)} <small class="muted">sesgo ${signed(row.bias, 1)}</small></td>` : '<td class="num">—</td>';
      }).join("");
      return `<tr><td>${esc(bucket.range)}</td><td class="num">${num(bucket.n, 0)}</td>${values}</tr>`;
    }).join("") : '<tr><td colspan="5">No hay partidos en los rangos disponibles.</td></tr>';
    const worst = diagnostics.worst_games || [];
    $("#modelWorstErrorsBody").innerHTML = worst.length ? worst.map(row => `
      <tr><td>${esc(shortDate(row.date))}</td><td>${esc(row.matchup)}</td><td class="num">${num(row.actual, 1)}</td><td class="num">${num(row.predicted, 1)}</td><td class="num">${signed(row.error, 1)}</td><td class="num">${num(row.absolute_error, 1)}</td></tr>`
    ).join("") : '<tr><td colspan="6">Sin errores extremos disponibles.</td></tr>';
    $("#modelErrorNote").textContent = `Diagnóstico sobre ${diagnosticModels[0]?.n || 0} partidos alineados. Error = real − predicción: positivo significa que el modelo subestimó el total. Los rangos se basan en el total real y los peores partidos corresponden al modelo elegido por validación (${MODEL_LABELS[data.best_model] || data.best_model || "—"}).`;
        $("#modelComparisonNote").textContent = data.selection_note || "MAE y RMSE más bajos son mejores; sesgo cercano a cero es preferible.";
    if (!data.predictions_available) notice($("#modelComparisonNotice"), "Faltan las predicciones individuales exportadas. Vuelve a entrenar y ejecuta scripts/export_vercel_artifacts.py.", true);
  } catch (error) { notice($("#modelComparisonNotice"), error.message, true); if (!silent) showToast(error.message); }
  finally { setBusy(button, false); }
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
    const modeLabel = data.mode === "pergame" ? "por partido" : "totales";
    $("#playersSubtitle").textContent = `PRA = puntos + rebotes + asistencias · ${data.season} · ${modeLabel}.`;
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
      $("#playersCards").innerHTML = '<div class="nba-cards-empty">No hay jugadores que cumplan esos filtros.</div>';
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
        // La ficha /api/player-card trabaja con el estado de la temporada actual.
        // No depender de /api/meta evita que una discrepancia de temporada
        // desactive todos los enlaces de jugadores.
        const canOpenDetail = Number.isFinite(Number(player.player_id)) && Number(player.player_id) > 0;
        const rowAttrs = canOpenDetail
          ? ` data-player-id="${esc(player.player_id)}" class="is-clickable" title="Abrir ficha del jugador"`
          : "";
        const nameMarkup = canOpenDetail
          ? `<button class="player-link" type="button" data-player-id="${esc(player.player_id)}">${esc(player.player_name)}</button>`
          : `<strong class="player-name">${esc(player.player_name)}</strong>`;
        return `
        <tr${rowAttrs}>
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
              ${nameMarkup}
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
    $("#playersCards").innerHTML = players.map((player, index) => {
      const headshotUrl = player.headshot_url ||
        (player.player_id ? `https://cdn.nba.com/headshots/nba/latest/260x190/${player.player_id}.png` : "");
      const canOpenDetail = Number.isFinite(Number(player.player_id)) && Number(player.player_id) > 0;
      const avatar = headshotUrl
        ? `<img src="${esc(headshotUrl)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">`
        : `<span class="nba-player-card-fallback">${esc(initials(player.player_name))}</span>`;
      return `<button type="button" class="nba-player-card" ${canOpenDetail ? `data-player-id="${esc(player.player_id)}"` : "disabled"} title="${canOpenDetail ? "Abrir ficha del jugador" : "Ficha no disponible"}">
        <span class="nba-player-card-head"><small>#${index + 1} · ${esc(player.team || "NBA")}</small><span>${num(player.gp, 0)} PJ</span></span>
        <span class="nba-player-card-identity">${avatar}<span><strong>${esc(player.player_name)}</strong><small>${esc(data.season)} · ${data.mode === "pergame" ? "Por partido" : "Totales"}</small></span></span>
        <span class="nba-player-card-stats">
          <span><small>PTS</small><b>${num(player.pts)}</b></span>
          <span><small>REB</small><b>${num(player.reb)}</b></span>
          <span><small>AST</small><b>${num(player.ast)}</b></span>
          <span class="is-highlight"><small>PRA</small><b>${num(player.pra)}</b></span>
        </span>
        <span class="nba-player-card-foot"><span>MIN ${num(player.min)}</span><span>Ver ficha <i aria-hidden="true">↗</i></span></span>
      </button>`;
    }).join("");
    const rosterNote = data.season === "2025-26" ? "equipos de esa temporada" : "equipos actuales";
    const sortLabel = PLAYER_SORT_LABELS[state.players.sort] || "PRA";
    $("#playersNote").textContent = `${data.count} jugadores · ${rosterNote} · ordenado por ${sortLabel} · mínimo ${state.players.minGp} partidos.`;
  } catch (error) {
    $("#playersCards").innerHTML = '<div class="nba-cards-empty">No se pudieron cargar los jugadores.</div>';
    emptyRow(tbody, 9, "No se pudieron cargar los jugadores.");
    notice($("#playersNotice"), error.message, true);
    showToast(error.message);
  } finally {
    setBusy(button, false);
  }
}

let positionVsPlayers = [];

function renderPositionVsPlayers() {
  const [metric, direction] = $("#positionVsSort").value.split("_");
  const field = metric === "min" ? "min" : "pts";
  const sign = direction === "asc" ? 1 : -1;
  const players = [...positionVsPlayers].sort((a, b) => {
    const av = Number(a[field]);
    const bv = Number(b[field]);
    const aValid = Number.isFinite(av);
    const bValid = Number.isFinite(bv);
    if (!aValid && !bValid) return 0;
    if (!aValid) return 1;
    if (!bValid) return -1;
    return (av - bv) * sign;
  });
  const fmt = (value) => value == null ? "—" : Number(value).toFixed(1);
  $("#positionVsBody").innerHTML = players.map((player) => `<tr>
    <td><strong>${esc(player.player_name)}</strong></td>
    <td>${numericDate(player.date)}</td>
    <td>${esc(player.matchup || "—")}</td>
    <td class="num">${fmt(player.min)}</td>
    <td class="num"><strong>${player.pts}</strong></td>
  </tr>`).join("");
}

async function loadPositionVsOpponent() {
  const opponent = $("#positionVsTeam").value;
  const status = $("#positionVsStatus");
  const wrap = $("#positionVsTableWrap");
  const summary = $("#positionVsSummary");
  if (!opponent) {
    status.textContent = "Selecciona un equipo rival.";
    wrap.hidden = true;
    summary.hidden = true;
    return;
  }
  status.textContent = "Consultando historial y posiciones oficiales…";
  wrap.hidden = true;
  summary.hidden = true;
  try {
    const data = await api("/api/position-vs-opponent", {
      opponent, position: $("#positionVsRole").value, window: $("#positionVsWindow").value,
    });
    if (!data.available) {
      positionVsPlayers = [];
      status.textContent = data.reason || "No hay datos para esta selección.";
      return;
    }
    const fmt = (value) => value == null ? "—" : Number(value).toFixed(1);
    summary.innerHTML = [
      summaryCard("Promedio de puntos", fmt(data.average), "por actuación"),
      summaryCard("Mediana", fmt(data.median), "puntos"),
      summaryCard("Máximo", String(data.max ?? "—"), "puntos"),
      summaryCard("Registros", String(data.sample), `${data.games_checked} partidos del rival`),
    ].join("");
    summary.hidden = false;
    positionVsPlayers = data.players || [];
    renderPositionVsPlayers();
    wrap.hidden = false;
    status.textContent = `${data.sample} actuaciones encontradas · ${data.position} vs. ${data.opponent} · temporada ${data.season}. ${data.note || ""}`;
  } catch (error) {
    status.textContent = error.message || "No se pudo consultar el historial.";
  }
}

async function loadProps(silent = false) {
  const daysControl = $("#propsDays");
  if (daysControl) state.props.days = Math.max(1, Math.min(14, Number(daysControl.value) || 5));
  const button = $("#propsRefresh");
  if (!silent) {
    setBusy(button, true);
    notice($("#propsNotice"), "");
    $("#propsSummary").innerHTML = "";
    $("#propsNote").textContent = "";
    $("#propsRange").textContent = "";
  }
  try {
    const data = await api("/api/player-props", {
      days: state.props.days,
      team: state.props.team,
      limit: state.props.limit,
    });
    state.loaded.props = true;
    const metrics = data.metrics || {};
    const praMetrics = data.pra_metrics || {};
    $("#propsRange").textContent = `${data.start} → ${data.end}`;
    const games = data.games || [];
    const players = games.flatMap((game) =>
      (game.players || []).map((player) => ({ ...player, game }))
    );
    // Se omite el resumen de métricas para dar prioridad a los partidos y props.
    $("#propsSummary").innerHTML = "";
    $("#propsSummary").hidden = true;
    const missingTeams = data.teams_without_games || [];
    const missingTeamsText = missingTeams.length
      ? `Aún no hay minutos en 2026-27 para ${missingTeams.join(", ")}; esos equipos aparecen sin proyecciones hasta que jueguen.`
      : "";
    notice($("#propsNotice"), [data.warning, missingTeamsText].filter(Boolean).join(" "));

    state.props.players = players;
    populatePropsGameFilters();
    renderPropsCandidates();
    window.dispatchEvent(new CustomEvent("nba:props-ready"));
    if (state.props.selectedPlayerId && !players.some((player) => String(player.player_id) === String(state.props.selectedPlayerId))) {
      state.props.selectedPlayerId = "";
      state.props.card = null;
    }
    if (!state.props.selectedPlayerId && players.length) state.props.selectedPlayerId = String(players[0].player_id);
    renderPropsPlayerOptions();
    if (state.props.selectedPlayerId) await loadPropsPlayerCard(state.props.selectedPlayerId, silent);
    else {
      renderPropsAnalyzer();
      $("#propsNote").textContent = "No hay jugadores para esos filtros. Prueba a ampliar los días o quitar el filtro de equipo.";
      return;
    }
    const validation = data.validation || {};
    const validationText = validation.from
      ? `Validación ${validation.from} → ${validation.to}: MAE PTS ${num(metrics.pts?.mae)}, REB ${num(metrics.reb?.mae)}, AST ${num(metrics.ast?.mae)}, PRA ${num(praMetrics.mae)}.`
      : "";
    const maxGp = players.reduce((max, player) => Math.max(max, Number(player.gp || 0)), 0);
    const earlySeasonText =
      maxGp <= 2
        ? "La 2026-27 acaba de empezar: con 1-2 partidos por jugador, el modelo se apoya en la temporada anterior."
        : "";
    $("#propsNote").textContent = `${data.note || ""} ${data.grade_note || ""} ${validationText} ${earlySeasonText}`.trim();
  } catch (error) {
    notice($("#propsNotice"), error.message, true);
    $("#propsAnalyzerStatus").textContent = "No se pudieron cargar los jugadores para el análisis.";
    showToast(error.message);
  } finally {
    setBusy(button, false);
  }
}


function populatePropsGameFilters() {
  const dateSelect = $("#propsDate");
  const gameSelect = $("#propsGame");
  if (!dateSelect || !gameSelect) return;
  const allPlayers = state.props.players || [];
  const dates = [...new Set(allPlayers.map((player) => player.game?.date).filter(Boolean))].sort();
  const previousDate = state.props.date || dateSelect.value || "";
  dateSelect.innerHTML = '<option value="">Todas las fechas</option>' + dates.map((date) =>
    '<option value="' + esc(date) + '">' + esc(shortDate(date)) + '</option>'
  ).join("");
  state.props.date = dates.includes(previousDate) ? previousDate : "";
  dateSelect.value = state.props.date;
  const filteredGames = allPlayers
    .filter((player) => !state.props.date || player.game?.date === state.props.date)
    .map((player) => player.game || {})
    .filter((game) => game.game_id !== undefined && game.game_id !== null);
  const uniqueGames = [...new Map(filteredGames.map((game) => [String(game.game_id), game])).values()]
    .sort((a, b) => String(a.time_et || "").localeCompare(String(b.time_et || "")));
  const previousGame = state.props.gameId || gameSelect.value || "";
  gameSelect.innerHTML = '<option value="">Todos los partidos</option>' + uniqueGames.map((game) =>
    '<option value="' + esc(game.game_id) + '">' + esc((game.away || "—") + " @ " + (game.home || "—")) + ' · ' + esc(chileGameTime(game.date, game.time_et) || "Hora por confirmar") + '</option>'
  ).join("");
  state.props.gameId = uniqueGames.some((game) => String(game.game_id) === String(previousGame)) ? String(previousGame) : "";
  gameSelect.value = state.props.gameId;
}

function renderPropsCandidates() {
  const container = $("#propsCandidates");
  const count = $("#propsCandidateCount");
  if (!container) return;
  const market = $("#propsMarket")?.value || "pra";
  const fields = { pts: "projected_pts", reb: "projected_reb", ast: "projected_ast", pra: "projected_pra" };
  const labels = { pts: "PTS", reb: "REB", ast: "AST", pra: "PRA" };
  const players = [...(state.props.players || [])]
    .filter((player) => Number.isFinite(Number(player[fields[market]])))
    .filter((player) => !state.props.date || player.game?.date === state.props.date)
    .filter((player) => !state.props.gameId || String(player.game?.game_id) === String(state.props.gameId))
    .sort((a, b) => {
      const ga = propGradeInfo(a).confidence;
      const gb = propGradeInfo(b).confidence;
      return gb - ga || Number(b[fields[market]]) - Number(a[fields[market]]);
    });
  if (count) count.textContent = players.length + (players.length === 1 ? " candidato" : " candidatos");
  if (!players.length) {
    container.innerHTML = '<div class="props-candidates-empty">No hay candidatos para este partido o fecha. Selecciona otro encuentro o amplía los días.</div>';
    return;
  }
  container.innerHTML = players.map((player) => {
    const game = player.game || {};
    const opponent = player.opponent || (String(player.team) === String(game.home) ? game.away : game.home) || "—";
    const isHome = player.is_home !== undefined ? Boolean(player.is_home) : String(player.team) === String(game.home);
    const { confidence, grade } = propGradeInfo(player);
    const gradeLabel = PROP_GRADE_LABELS[grade] || "Baja";
    const headshot = player.headshot_url || "";
    const statValue = Number(player[fields[market]]);
    const metrics = [
      ["PTS", player.projected_pts], ["REB", player.projected_reb],
      ["AST", player.projected_ast], ["PRA", player.projected_pra]
    ].map(([label, value]) => '<div><small>' + label + '</small><strong>' + (Number.isFinite(Number(value)) ? num(value) : "—") + '</strong></div>').join("");
    const confidencePct = Math.max(0, Math.min(100, Math.round(confidence * 100)));
    return '<article class="props-candidate-card upcoming-game-card signal-neutral">' +
      '<div class="upcoming-card-top"><span>' + esc(shortDate(chileGameDate(game.date, game.time_et))) + ' · ' + esc(chileGameTime(game.date, game.time_et) || "Hora por confirmar") + '</span><span class="props-candidate-grade grade-' + grade.toLowerCase() + '"><b>' + grade + '</b><small>' + confidencePct + '%</small></span></div>' +
      '<div class="upcoming-matchup"><div class="upcoming-team">' + upcomingTeamLogo(game.away) + '<strong>' + esc(game.away || "Visitante") + '</strong><small>VISITANTE</small></div><span class="upcoming-at">@</span><div class="upcoming-team">' + upcomingTeamLogo(game.home) + '<strong>' + esc(game.home || "Local") + '</strong><small>LOCAL</small></div></div>' +
      '<div class="props-player-card-identity">' + (headshot ? '<img src="' + esc(headshot) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">' : '<span class="props-candidate-avatar">' + esc((player.player_name || "NBA").split(" ").map((part) => part[0]).slice(0,2).join("").toUpperCase()) + '</span>') + '<div><strong>' + esc(player.player_name || "Jugador") + '</strong><small>' + esc(player.team || "") + ' · ' + (isHome ? "LOCAL" : "VISITA") + ' vs ' + esc(opponent) + '</small></div></div>' +
      '<div class="upcoming-total"><span>Proyección ' + labels[market] + '</span><strong>' + num(statValue) + '</strong></div>' +
      '<div class="upcoming-card-stats props-candidate-metrics">' + metrics + '</div>' +
      '<div class="upcoming-probability"><div><span>Confianza general del modelo · ' + esc(gradeLabel) + '</span><b>' + confidencePct + '%</b></div><div class="upcoming-probability-track"><i style="width:' + confidencePct + '%"></i></div></div>' +
      '<div class="props-candidate-footer"><span>' + esc(Number.isFinite(Number(player.projected_min)) ? num(player.projected_min) + " MIN proyectados" : "Proyección del modelo") + '</span><button type="button" class="props-candidate-open" data-props-player="' + esc(player.player_id) + '" data-props-stat="' + market + '">Ver análisis <span aria-hidden="true">→</span></button></div>' +
      '</article>';
  }).join("");
}

function renderPropsPlayerOptions() {
  const select = $("#propsPlayer");
  if (!select) return;
  select.innerHTML = state.props.players.map((player) => {
    const game = player.game || {};
    const matchup = game.away && game.home ? " · " + game.away + "@" + game.home : "";
    return '<option value="' + esc(player.player_id) + '">' + esc(player.player_name) + " · " + esc(player.team || "") + esc(matchup) + "</option>";
  }).join("") || '<option value="">No hay jugadores disponibles</option>';
  select.value = state.props.selectedPlayerId || "";
}

async function loadPropsPlayerCard(playerId, silent = false) {
  if (!playerId) return;
  if (state.props.cardCache[playerId]) {
    state.props.card = state.props.cardCache[playerId];
    if (state.props.line === null) state.props.line = pdDefaultLine(state.props.card, state.props.selectedStat);
    renderPropsAnalyzer();
    return;
  }
  const requestId = ++state.props.requestId;
  state.props.card = null;
  if (!silent) $("#propsAnalyzerStatus").textContent = "Cargando historial del jugador…";
  renderPropsAnalyzer();
  try {
    const data = await api("/api/player-card", { player_id: Number(playerId) });
    if (requestId !== state.props.requestId || String(state.props.selectedPlayerId) !== String(playerId)) return;
    state.props.cardCache[playerId] = data;
    state.props.card = data;
    state.props.line = pdDefaultLine(data, state.props.selectedStat);
    renderPropsAnalyzer();
  } catch (error) {
    if (requestId !== state.props.requestId) return;
    state.props.card = null;
    $("#propsAnalyzerStatus").textContent = "No se pudo cargar el historial: " + error.message;
    renderPropsAnalyzer();
  }
}

function propsLineValue() {
  const value = Number(state.props.line);
  return Number.isFinite(value) && value >= 0 ? value : 0.5;
}

function renderPropsAnalyzer() {
  const card = state.props.card;
  const stat = state.props.selectedStat || "pts";
  const line = propsLineValue();
  const statLabel = DETAIL_STATS.find((item) => item.key === stat)?.label || stat.toUpperCase();
  const status = $("#propsAnalyzerStatus");
  const summary = $("#propsPlayerSummary");
  const results = $("#propsHitResults");
  const chart = $("#propsHistoryChart");
  const context = $("#propsMatchupContext");
  const lineInput = $("#propsLine");
  if (lineInput && document.activeElement !== lineInput) lineInput.value = line.toFixed(1);
  if (!card) {
    if (summary) summary.hidden = true;
    if (results) results.innerHTML = [5,10].map((n) => '<div class="props-hit-card is-empty"><span>ÚLTIMOS ' + n + '</span><strong>—</strong><small>Esperando datos</small></div>').join("");
    if (chart) chart.innerHTML = "";
    if (context) context.innerHTML = "";
    if (status && !status.textContent.includes("No se pudo")) status.textContent = state.props.selectedPlayerId ? "Cargando historial del jugador…" : "Selecciona un jugador para analizar sus últimos partidos.";
    return;
  }
  const player = card.player || {};
  const games = (Array.isArray(card.games) ? card.games : [])
    .slice()
    .sort((a, b) => String(b.game_date || b.date || "").localeCompare(String(a.game_date || a.date || "")));
  const requiredFields = {
    pts: ["pts"], reb: ["reb"], ast: ["ast"], min: ["min"],
    stl: ["stl"], blk: ["blk"], fg3m: ["fg3m"],
    pts_ast: ["pts", "ast"], pts_reb: ["pts", "reb"],
    reb_ast: ["reb", "ast"], pra: ["pts", "reb", "ast"],
    stl_blk: ["stl", "blk"], dd: ["pts", "reb", "ast"], td: ["pts", "reb", "ast"],
  };
  const hasStatData = (game) => (requiredFields[stat] || [stat]).every((key) =>
    game[key] !== null && game[key] !== undefined && game[key] !== "" && Number.isFinite(Number(game[key]))
  );
  const values = games.filter(hasStatData)
    .map((game) => ({ game, value: pdStatValue(game, stat) }))
    .filter((item) => Number.isFinite(item.value));
  const buildCard = (items, label) => {
    const hits = items.filter((item) => item.value >= line).length;
    const total = items.length;
    const rate = total ? hits / total : null;
    const average = total ? items.reduce((sum, item) => sum + item.value, 0) / total : null;
    const tone = rate === null ? "is-empty" : rate >= .7 ? "is-strong" : rate >= .5 ? "is-mid" : "is-low";
    const bar = rate === null ? 0 : Math.round(rate * 100);
    return '<article class="props-hit-card ' + tone + '"><span>' + label + '</span><strong>' + (rate === null ? "—" : pct(rate)) + '</strong><small>' + hits + ' de ' + total + ' partidos · media ' + (average === null ? "—" : num(average)) + '</small><div class="props-hit-track" aria-hidden="true"><i style="width:' + bar + '%"></i></div><em>' + (total ? hits + ' aciertos / ' + (total - hits) + ' fallos' : "Sin partidos disponibles") + '</em></article>';
  };
  if (summary) {
    summary.hidden = false;
    const headshot = player.headshot_url || "";
    summary.innerHTML = '<div class="props-player-identity">' + (headshot ? '<img src="' + esc(headshot) + '" alt="" loading="lazy" onerror="this.remove()">' : "") + '<div><strong>' + esc(player.player_name || "Jugador") + '</strong><small>' + esc(player.team || "") + ' · ' + esc(player.season || "") + ' · ' + games.length + ' partidos recientes</small></div></div><span class="props-selected-stat">' + esc(statLabel) + ' · L5 / L10</span>';
  }
  const ranking = DETAIL_STATS.map((item) => {
    const fields = requiredFields[item.key] || [item.key];
    const sample = games.filter((game) => fields.every((key) =>
      game[key] !== null && game[key] !== undefined && game[key] !== "" && Number.isFinite(Number(game[key]))
    )).slice(0, 10).map((game) => ({ game, value: pdStatValue(game, item.key) }))
      .filter((entry) => Number.isFinite(entry.value));
    const referenceLine = pdDefaultLine(card, item.key);
    const hits = sample.filter((entry) => entry.value >= referenceLine).length;
    return { ...item, sample, hits, total: sample.length, rate: sample.length ? hits / sample.length : null, line: referenceLine,
      average: sample.length ? sample.reduce((sum, entry) => sum + entry.value, 0) / sample.length : null };
  }).sort((a, b) => (b.rate ?? -1) - (a.rate ?? -1) || b.total - a.total || a.label.localeCompare(b.label));
  const rankingCards = ranking.map((item, index) => {
    const rate = item.rate;
    const tone = rate === null ? "is-empty" : rate >= .7 ? "is-strong" : rate >= .5 ? "is-mid" : "is-low";
    return '<article class="props-rank-card ' + tone + '"><div class="props-rank-top"><span class="props-rank-position">#' + (index + 1) + '</span><strong>' + esc(item.label) + '</strong><b>' + (rate === null ? "—" : pct(rate)) + '</b></div><div class="props-hit-track"><i style="width:' + (rate === null ? 0 : Math.round(rate * 100)) + '%"></i></div><small>' + item.hits + '/' + item.total + ' aciertos · línea ' + num(item.line) + ' · media ' + (item.average === null ? "—" : num(item.average)) + '</small></article>';
  }).join("");
  const best = ranking.find((item) => item.total > 0);
  if (results) results.innerHTML =
    buildCard(values.slice(0,5), "ÚLTIMOS 5") +
    buildCard(values.slice(0,10), "ÚLTIMOS 10") +
    '<section class="props-market-ranking"><div class="props-market-ranking-head"><div><strong>Hit rate por estadística</strong><small>Últimos 10 partidos · proyección del modelo o media de temporada si no existe</small></div>' +
    (best ? '<span>Mejor mercado: <b>' + esc(best.label) + ' · ' + pct(best.rate) + '</b></span>' : '') +
    '</div><div class="props-rank-grid">' + rankingCards + '</div></section>';

  const lastTen = values.slice(0,10).reverse();
  if (chart) {
    if (!lastTen.length) {
      chart.innerHTML = '<div class="props-context-empty">Sin partidos suficientes para dibujar el historial.</div>';
    } else {
      const maxValue = Math.max(line, ...lastTen.map((item) => item.value), 1);
      const scaleMax = maxValue * 1.18;
      chart.innerHTML = '<div class="props-chart-head"><div><strong>Últimos ' + lastTen.length + ' partidos</strong><small>Rendimiento real vs. línea ' + num(line) + ' ' + esc(statLabel) + '</small></div><span class="props-chart-legend"><i class="over"></i> Supera línea <i class="under"></i> No alcanza</span></div><div class="props-bars">' +
        lastTen.map(({game,value},index) => {
          const hit = value >= line;
          const height = Math.max(5, Math.round(value / scaleMax * 100));
          const date = game.game_date || game.date || "";
          const opponent = game.opponent || game.matchup || game.team_abbreviation || "PJ";
          return '<div class="props-bar-item" title="' + esc((date ? date + " · " : "") + opponent + " · " + num(value) + " " + statLabel) + '"><strong>' + num(value,0) + '</strong><div class="props-bar-track"><i class="' + (hit ? "over" : "under") + '" style="height:' + height + '%"></i></div><small>' + esc(String(opponent).replace(/^.*?@\s*|^.*?vs\.?\s*/i,"").slice(0,5)) + '</small></div>';
        }).join("") + '</div><div class="props-chart-line-note">La línea es ' + num(line) + '. Verde = iguala o supera; rojo = queda por debajo.</div>';
    }
  }

  const upcoming = card.upcoming || {};
  const h2h = card.h2h || {};
  const defender = card.likely_defender || null;
  const h2hAvg = h2h.averages || {};
  const h2hValue = (() => {
    if (stat === "pts_ast") return Number(h2hAvg.pts) + Number(h2hAvg.ast);
    if (stat === "pts_reb") return Number(h2hAvg.pts) + Number(h2hAvg.reb);
    if (stat === "reb_ast") return Number(h2hAvg.reb) + Number(h2hAvg.ast);
    if (stat === "pra") return Number(h2hAvg.pra);
    if (stat === "stl_blk") return null;
    return h2hAvg[stat];
  })();
  if (context) {
    const opponent = upcoming.opponent || h2h.opponent || "";
    const matchup = opponent
      ? '<div class="props-context-cell"><small>PRÓXIMO RIVAL</small><strong>' + esc(opponent) + '</strong><span>' + (upcoming.is_home ? "Local" : "Visita") + '</span></div>'
      : '<div class="props-context-cell"><small>PRÓXIMO RIVAL</small><strong>Sin partido próximo</strong></div>';
    const h2hHtml = opponent && h2h.games
      ? '<div class="props-context-cell"><small>H2H · ' + esc(opponent) + '</small><strong>' + esc(num(h2hValue)) + ' ' + esc(statLabel) + '</strong><span>Media en ' + esc(String(h2h.games)) + ' enfrentamientos</span></div>'
      : '<div class="props-context-cell"><small>H2H</small><strong>Sin muestra</strong><span>No hay enfrentamientos registrados</span></div>';
    const defenderHtml = defender
      ? '<div class="props-context-cell"><small>POSIBLE DEFENSOR</small><strong>' + esc(defender.player_name || "—") + '</strong><span>' + esc(defender.basis === "historial" ? "Identificado por historial de marcajes" : "Estimado por rol y minutos") + ' · confianza ' + esc(defender.confidence || "baja") + '</span></div>'
      : '<div class="props-context-cell"><small>DEFENSOR</small><strong>Sin dato disponible</strong><span>La API no identificó un emparejamiento</span></div>';
    context.innerHTML = '<div class="props-context-head"><strong>Contexto del enfrentamiento</strong><span>Datos disponibles del jugador</span></div><div class="props-context-grid">' + matchup + h2hHtml + defenderHtml + '</div><p class="props-context-disclaimer">El H2H es una muestra histórica y el posible defensor no es una asignación oficial. El repositorio aún no dispone de una tabla DvP (puntos/rebotes/asistencias permitidos por posición); no se muestra un ranking defensivo ficticio.</p>';
  }
  if (status) status.textContent = values.length ? "Línea de " + num(line) + " " + statLabel + ". Se cuentan valores iguales o superiores a la línea; la gráfica muestra los últimos 10 disponibles." : "No hay partidos recientes con datos para esta estadística.";
}

function formatDetailValue(value) {
  const digits = state.detail.stat === "dd" || state.detail.stat === "td" ? 0 : 1;
  return num(value, digits);
}

function pdHeaderStat(label, value) {
  return `<span class="pd-stat">${label} <b>${value === null || value === undefined ? "—" : num(value)}</b></span>`;
}

function pdHeaderPct(label, value) {
  return `<span class="pd-stat">${label} <b>${pct(value)}</b></span>`;
}

async function openPlayerDetail(playerId, options = {}) {
  const numericId = Number(playerId);
  if (!Number.isFinite(numericId) || numericId <= 0) return;
  const overlay = $("#playerDetail");
  const loading = $("#pdLoading");
  const content = $("#pdContent");
  const requestId = ++state.detail.requestId;
  const wasOpen = !overlay.hidden;

  state.detail.playerId = numericId;
  state.detail.data = null;
  state.detail.stat = options.stat || "pts";
  state.detail.line = null;
  state.detail.range = 10;
  state.detail.venue = "all";
  if (!wasOpen) {
    state.detail.opener = options.opener || document.activeElement;
  }

  overlay.hidden = false;
  document.body.classList.add("modal-open");
  content.hidden = true;
  loading.hidden = false;
  loading.innerHTML = '<span class="spinner"></span>Cargando ficha…';
  notice($("#pdNotice"), "");
  window.setTimeout(() => $("#pdClose").focus(), 0);

  try {
    const data = await api("/api/player-card", { player_id: numericId });
    if (requestId !== state.detail.requestId) return;
    state.detail.data = data;
    window.dispatchEvent(new CustomEvent("nba:player-card-open", { detail: {
      playerId: numericId,
      averagePoints: data.player?.averages?.pts ?? null
    }}));
    state.detail.stat = DETAIL_STATS.some((item) => item.key === options.stat)
      ? options.stat
      : "pts";
    state.detail.line = pdDefaultLine(data, state.detail.stat);
    renderPlayerDetail();
    loading.hidden = true;
    content.hidden = false;
  } catch (error) {
    if (requestId !== state.detail.requestId) return;
    loading.innerHTML = `<div class="pd-empty">No se pudo cargar la ficha.<br><small>${esc(error.message)}</small></div>`;
    showToast(error.message);
  }
}

function closePlayerDetail() {
  const overlay = $("#playerDetail");
  if (!overlay || overlay.hidden) return;
  state.detail.requestId += 1;
  overlay.hidden = true;
  document.body.classList.remove("modal-open");
  state.detail.data = null;
  state.detail.playerId = null;
  if (window.location.hash.startsWith("#ficha/")) {
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}`
    );
  }
  const opener = state.detail.opener;
  state.detail.opener = null;
  if (opener && typeof opener.focus === "function") opener.focus();
}

function renderPlayerDetail() {
  const data = state.detail.data;
  if (!data) return;
  renderDetailHero(data);
  renderDetailTabs(data);
  renderDetailGames(data);
  renderDetailChart(data);
  renderDetailLinePanel(data);
  renderDetailEfficiency(data);
  renderDetailH2H(data);
  renderDetailDefenders(data);
  renderDetailTeammates(data);

  const notes = [];
  if (!data.upcoming) {
    notes.push("No hay un próximo partido programado; la línea se apoya en partidos recientes.");
  }
  if (!data.games?.length) {
    notes.push("No hay partidos recientes en la foto actual del jugador.");
  }
  notice($("#pdNotice"), notes.join(" "));
  $("#pdNote").textContent =
    "Barras verdes: el partido supera la línea; rojas: no la alcanza. " +
    "El acierto cuenta los partidos iguales o superiores a la línea. " +
    "La fila PRÓX. es una proyección, no un resultado. " +
    `Foto de jugadores: ${data.player_state_date}. ` +
    "No incluye lesiones, alineaciones ni cuotas de props; no es consejo de apuestas.";
}

function renderDetailHero(data) {
  const player = data.player;
  const averages = player.averages || {};
  const efficiency = data.efficiency || {};
  const avatar = player.headshot_url
    ? `<img class="pd-headshot" src="${esc(player.headshot_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">`
    : "";
  const teamLogo = player.team_logo_url
    ? `<span class="pd-team-badge"><img src="${esc(player.team_logo_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.parentElement.remove()"></span>`
    : "";
  $("#pdHero").innerHTML = `
    <div class="pd-avatar">
      <span class="pd-headshot-fallback" aria-hidden="true">${esc(initials(player.player_name))}</span>
      ${avatar}
      ${teamLogo}
    </div>
    <div class="pd-id">
      <div class="pd-name-row">
        <h2 id="pdName">${esc(player.player_name)}</h2>
        <span class="pd-chip">${esc(player.team)}</span>
        <span class="pd-chip pd-chip-soft">${esc(player.season)} · ${esc(player.gp)} PJ</span>
      </div>
      <div class="pd-stats">
        ${pdHeaderStat("PTS", averages.pts)}
        ${pdHeaderStat("REB", averages.reb)}
        ${pdHeaderStat("AST", averages.ast)}
        ${pdHeaderStat("MIN", averages.min)}
        ${pdHeaderStat("PRA", averages.pra)}
        ${pdHeaderPct("USO", efficiency.usage)}
      </div>
    </div>`;
}

function renderDetailTabs() {
  $("#pdTabs").innerHTML = DETAIL_STATS.map((item) => {
    const active = item.key === state.detail.stat;
    const modeled = ["pts", "reb", "ast", "pts_ast", "pts_reb", "reb_ast", "pra", "min"].includes(item.key);
    return `<button class="pd-tab${active ? " is-active" : ""}" type="button" role="tab" aria-selected="${active}" data-stat="${item.key}" title="${modeled ? "Proyección del modelo" : "Media histórica; sin proyección del modelo"}">${item.label}</button>`;
  }).join("");
}

function renderDetailGames(data) {
  const games = detailVisibleGames(data);
  const rows = [];
  if (data.upcoming) {
    const projection = pdProjectionFor(data, state.detail.stat);
    const value = projection === null ? "—" : formatDetailValue(projection);
    const logos = [
      data.player.team_logo_url
        ? `<img src="${esc(data.player.team_logo_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">`
        : "",
      data.upcoming.opponent_logo_url
        ? `<img src="${esc(data.upcoming.opponent_logo_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">`
        : "",
    ].join("");
    rows.push(`
      <button class="pd-game is-next" type="button" data-game="next" title="Ver análisis del partido">
        <span class="pd-game-logos">${logos}</span>
        <span class="pd-game-main">
          <strong>${data.upcoming.is_home ? "vs" : "@"} ${esc(data.upcoming.opponent)}</strong>
          <small>PRÓX. · ${dayMonth(chileGameDate(data.upcoming.date, data.upcoming.time_et))}${data.upcoming.time_et ? ` · ${esc(chileGameTime(data.upcoming.date, data.upcoming.time_et))} Chile` : ""}${state.detail.stat !== "min" && data.upcoming.projection?.min != null ? ` · ${num(data.upcoming.projection.min)} min proy.` : ""}</small>
        </span>
        <span class="pd-game-value">${value}</span>
      </button>`);
  }

  games.forEach((game) => {
    const value = pdStatValue(game, state.detail.stat);
    const over = value >= state.detail.line;
    const logos = [
      game.team_logo_url
        ? `<img src="${esc(game.team_logo_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">`
        : "",
      game.opponent_logo_url
        ? `<img src="${esc(game.opponent_logo_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">`
        : "",
    ].join("");
    rows.push(`
      <button class="pd-game" type="button" data-game-id="${esc(game.game_id)}" title="Usar este valor como línea">
        <span class="pd-game-logos">${logos}</span>
        <span class="pd-game-main">
          <strong>${game.is_home ? "vs" : "@"} ${esc(game.opponent)}</strong>
          <small>${dayMonth(game.date)}${game.wl ? ` · ${esc(game.wl)}` : ""}${state.detail.stat !== "min" && game.min != null ? ` · ${num(game.min)} min` : ""}</small>
        </span>
        <span class="pd-game-value ${over ? "is-over" : "is-under"}">${formatDetailValue(value)}</span>
      </button>`);
  });

  $("#pdGameList").innerHTML = rows.length
    ? rows.join("")
    : '<div class="pd-empty">Sin partidos con ese filtro.</div>';
}

function renderDetailChart(data) {
  const games = detailVisibleGames(data);
  const projection = pdProjectionFor(data, state.detail.stat);
  const values = games.map((game) => pdStatValue(game, state.detail.stat));
  if (projection !== null) values.push(projection);
  values.push(state.detail.line);

  if (!games.length && projection === null) {
    $("#pdChart").innerHTML =
      '<div class="pd-empty">Sin partidos para dibujar. Prueba a cambiar el filtro.</div>';
    $("#pdChartLine").textContent = num(state.detail.line);
    $("#pdChartRange").textContent = "Sin partidos";
    return;
  }

  const max = Math.max(1, ...values) * 1.12;
  const linePct = Math.max(0, Math.min(100, (state.detail.line / max) * 100));
  const rows = [];
  if (projection !== null && data.upcoming) {
    const over = projection >= state.detail.line;
    const width = Math.max(0, Math.min(100, (projection / max) * 100));
    rows.push(`
      <div class="pd-chart-row is-projected">
        <span class="pd-chart-date">PRÓX.</span>
        <span class="pd-chart-opp">
          ${data.upcoming.opponent_logo_url ? `<img src="${esc(data.upcoming.opponent_logo_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}
          ${esc(data.upcoming.opponent)}
        </span>
        <span class="pd-bar-track"><span class="pd-bar-fill ${over ? "is-over" : ""}" style="width:${width}%"></span></span>
        <span class="pd-chart-value">${formatDetailValue(projection)}</span>
      </div>`);
  }

  games.forEach((game) => {
    const value = pdStatValue(game, state.detail.stat);
    const over = value >= state.detail.line;
    const width = Math.max(0, Math.min(100, (value / max) * 100));
    rows.push(`
      <div class="pd-chart-row">
        <span class="pd-chart-date">${dayMonth(game.date)}</span>
        <span class="pd-chart-opp">
          ${game.opponent_logo_url ? `<img src="${esc(game.opponent_logo_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}
          ${esc(game.opponent)}
        </span>
        <span class="pd-bar-track"><span class="pd-bar-fill ${over ? "is-over" : ""}" style="width:${width}%"></span></span>
        <span class="pd-chart-value">${formatDetailValue(value)}</span>
      </div>`);
  });

  $("#pdChart").innerHTML = `<div class="pd-chart-rows" style="--line-pct:${linePct}%">${rows.join("")}</div>`;
  $("#pdChartLine").textContent = num(state.detail.line);
  const rangeText = state.detail.range === "all" ? `${games.length} partidos` : `últimos ${games.length}`;
  const venueText =
    state.detail.venue === "home" ? " · local" : state.detail.venue === "away" ? " · visita" : "";
  $("#pdChartRange").textContent = `${rangeText}${venueText}`;
}

function renderDetailLinePanel(data) {
  const stat = state.detail.stat;
  const label = detailStatLabel();
  const line = state.detail.line;
  const games = detailVisibleGames(data);
  const values = games.map((game) => pdStatValue(game, stat));
  const hits = values.filter((value) => value >= line).length;
  const total = values.length;
  const rate = total ? hits / total : null;

  $("#pdLineInput").value = line.toFixed(1);
  $("#pdHitRate").innerHTML = total
    ? `${hits}<span>/${total} · ${pct(rate)}</span>`
    : "—";
  $("#pdHitSub").textContent = total
    ? `Línea ${num(line)} · ${total} partidos reales`
    : "Sin partidos para comparar.";

  const projection = pdProjectionFor(data, stat);
  const average = pdAverageFor(data, stat);
  let meta;
  if (stat === "dd" || stat === "td") {
    meta =
      stat === "dd"
        ? "Cuenta partidos con 10+ en al menos dos de PTS, REB o AST."
        : "Cuenta partidos con 10+ en PTS, REB y AST.";
  } else if (projection !== null) {
    meta = `Proyección del modelo para el próximo partido: ${num(projection)} ${label}.`;
  } else if (average !== null) {
    meta = `Media ${data.player.season}: ${num(average)} ${label}. Sin proyección del modelo para esta estadística.`;
  } else {
    meta = "Ajusta la línea para comparar los partidos recientes.";
  }
  $("#pdLineMeta").textContent = meta;

  $$("#pdQuickViews button").forEach((button) => {
    const value = button.dataset.range;
    const active = value === "all" ? state.detail.range === "all" : Number(value) === state.detail.range;
    button.classList.toggle("is-active", active);
  });
  $$("#pdVenueFilter button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.venue === state.detail.venue);
  });

  const cta = $("#pdAnalysis");
  cta.disabled = !data.upcoming;
  cta.textContent = data.upcoming ? "VER ANÁLISIS DEL PARTIDO" : "SIN PRÓXIMO PARTIDO";
}

function renderDetailEfficiency(data) {
  const efficiency = data.efficiency || {};
  const sample = efficiency.usage_gp
    ? `${data.player.season} · ${efficiency.usage_gp} PJ`
    : data.player.season;
  $("#pdEfficiencySample").textContent = sample;
  const items = [
    { label: "FG%", value: efficiency.fg_pct, title: "Tiros de campo" },
    { label: "3P%", value: efficiency.fg3_pct, title: "Triples" },
    { label: "FT%", value: efficiency.ft_pct, title: "Tiros libres" },
    { label: "TS%", value: efficiency.ts_pct, title: "True shooting" },
    { label: "eFG%", value: efficiency.efg_pct, title: "Effective field goal" },
    { label: "USO", value: efficiency.usage, title: "Uso estimado" },
  ];
  $("#pdEfficiency").innerHTML = items
    .map(
      (item) => `
        <div class="pd-eff-item" title="${esc(item.title)}">
          <span>${esc(item.label)}</span>
          <b>${pct(item.value)}</b>
        </div>`
    )
    .join("");
  $("#pdEfficiencyVolume").textContent =
    efficiency.fgm == null
      ? "Sin volumen de tiro disponible."
      : `Por partido: ${num(efficiency.fgm)}/${num(efficiency.fga)} FG · ${num(efficiency.fg3m)}/${num(efficiency.fg3a)} 3P · ${num(efficiency.ftm)}/${num(efficiency.fta)} FT`;
}

function renderDetailH2H(data) {
  const h2h = data.h2h;
  const meta = $("#pdH2HMeta");
  const body = $("#pdH2H");
  const note = $("#pdH2HNote");
  if (!h2h) {
    meta.textContent = "";
    body.innerHTML =
      '<div class="pd-empty">Sin próximo partido para calcular un H2H.</div>';
    note.textContent = "";
    return;
  }

  meta.textContent = `${h2h.is_home ? "vs" : "@"} ${h2h.opponent}`;
  note.textContent = `Muestra: últimos ${h2h.sample_size} partidos del jugador.`;
  const logo = h2h.opponent_logo_url
    ? `<img src="${esc(h2h.opponent_logo_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">`
    : "";
  if (!h2h.games) {
    body.innerHTML = `
      <div class="pd-h2h-top">
        <span class="pd-h2h-logo">${logo}</span>
        <span class="pd-h2h-name"><strong>${esc(h2h.opponent_name)}</strong><small>0 PJ en la muestra</small></span>
        <span class="pd-h2h-record">—</span>
      </div>
      <div class="pd-empty">Sin cruces recientes contra ${esc(h2h.opponent)}.</div>`;
    return;
  }

  const averages = h2h.averages || {};
  const stats = [
    ["PTS", averages.pts],
    ["REB", averages.reb],
    ["AST", averages.ast],
    ["MIN", averages.min],
    ["FG%", averages.fg_pct, true],
    ["USO", averages.usage, true],
  ];
  const meetings = (h2h.meetings || [])
    .slice(0, 3)
    .map((game) => {
      const result = game.wl || "—";
      const resultClass =
        result === "W" ? "is-win" : result === "L" ? "is-loss" : "";
      return `
        <div class="pd-h2h-meeting">
          <span class="pd-h2h-date">${dayMonth(game.date)}</span>
          <span class="pd-h2h-result ${resultClass}">${esc(result)}</span>
          <span class="pd-h2h-line">
            <b>${num(game.pts, 0)}</b> PTS · <b>${num(game.reb, 0)}</b> REB · <b>${num(game.ast, 0)}</b> AST
            <small>${num(game.min)} min · ${pct(game.fg_pct)} FG</small>
          </span>
        </div>`;
    })
    .join("");

  body.innerHTML = `
    <div class="pd-h2h-top">
      <span class="pd-h2h-logo">${logo}</span>
      <span class="pd-h2h-name"><strong>${esc(h2h.opponent_name)}</strong><small>${h2h.games} PJ</small></span>
      <span class="pd-h2h-record">${h2h.wins}-${h2h.losses}</span>
    </div>
    <div class="pd-h2h-stats">
      ${stats
        .map(
          ([label, value, isPct]) => `
            <span class="pd-h2h-stat">
              <small>${label}</small>
              <b>${isPct ? pct(value) : num(value)}</b>
            </span>`
        )
        .join("")}
    </div>
    <div class="pd-h2h-meetings">${meetings}</div>`;
}

function renderDetailDefenders(data) {
  const defenders = data.defenders || [];
  const likely = data.likely_defender;
  const opponent = data.upcoming?.opponent;
  $("#pdDefendersTeam").textContent = opponent
    ? `${data.upcoming.is_home ? "vs" : "@"} ${opponent}`
    : "";

  const likelyContainer = $("#pdLikelyDefender");
  if (!likely) {
    likelyContainer.innerHTML = `<div class="pd-empty">${
      opponent
        ? "Sin defensor probable identificado para el próximo partido."
        : "Sin próximo partido para estimar un defensor."
    }</div>`;
  } else {
    const roleLabels = { big: "interior", guard: "perímetro", wing: "alero" };
    const roleLabel = roleLabels[likely.role] || "rol";
    const historical = Boolean(likely.is_historical);
    const confidenceLabel =
      likely.confidence === "media" ? "Conf. media" : "Conf. baja";
    const confidenceClass =
      likely.confidence === "media" ? "is-medium" : "is-low";
    let stats;
    if (historical) {
      stats = [
        ["PTS perm.", likely.player_pts_pg],
        ["MIN/part.", likely.matchup_min_pg],
        ["FG% perm.", likely.fg_pct, true],
      ];
    } else if (likely.role === "guard") {
      stats = [
        ["MIN", likely.min],
        ["AST", likely.ast],
        ["STL", likely.stl],
      ];
    } else if (likely.role === "big") {
      stats = [
        ["MIN", likely.min],
        ["REB", likely.reb],
        ["BLK", likely.blk],
      ];
    } else {
      stats = [
        ["MIN", likely.min],
        ["PTS", likely.pts],
        ["STL", likely.stl],
      ];
    }
    const reason = historical
      ? `Basado en ${num(likely.matchup_gp, 0)} PJ de matchup (${num(likely.matchup_min)} min totales). ${
          Number(likely.matchup_min || 0) < 10 ? "Muestra corta." : "Muestra limitada."
        }`
      : `Estimado por rol (${roleLabel}) y minutos. No es una asignación oficial.`;
    likelyContainer.innerHTML = `
      <div class="pd-likely">
        <div class="pd-likely-top">
          <span class="pd-likely-avatar" aria-hidden="true">
            <span>${esc(initials(likely.player_name))}</span>
            ${
              likely.headshot_url
                ? `<img src="${esc(likely.headshot_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">`
                : ""
            }
          </span>
          <span class="pd-likely-main">
            <strong>${esc(likely.player_name)}</strong>
            <small>${esc(likely.team)} · ${historical ? "historial de matchup" : `rol ${roleLabel}`}</small>
          </span>
          <span class="pd-likely-badge ${confidenceClass}">${confidenceLabel}</span>
        </div>
        <div class="pd-likely-stats">
          ${stats
            .map(
              ([label, value, isPct]) => `
                <span class="pd-likely-stat">
                  <small>${label}</small>
                  <b>${isPct ? pct(value) : num(value)}</b>
                </span>`
            )
            .join("")}
        </div>
        <div class="pd-likely-reason">${reason}</div>
      </div>`;
  }

  const historicalDefenders = defenders.filter(
    (defender) => !likely || defender.player_id !== likely.player_id
  );
  $("#pdDefenders").innerHTML = historicalDefenders.length
    ? historicalDefenders
        .slice(0, 4)
        .map((defender) => {
          const teamText = defender.team ? `${esc(defender.team)} · ` : "";
          const nextClass = defender.is_next_opponent ? " is-next" : "";
          return `
            <div class="pd-defender${nextClass}">
              <span class="pd-defender-avatar" aria-hidden="true">
                <span>${esc(initials(defender.player_name))}</span>
                ${
                  defender.headshot_url
                    ? `<img src="${esc(defender.headshot_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">`
                    : ""
                }
              </span>
              <span class="pd-defender-main">
                <strong>${esc(defender.player_name)}</strong>
                <small>${teamText}${num(defender.matchup_min_pg)} min/partido · ${num(defender.gp, 0)} PJ${defender.is_next_opponent ? " · próximo rival" : ""}</small>
              </span>
              <span class="pd-defender-stats">
                <b>${num(defender.player_pts_pg)}</b>
                <small>PTS/part.</small>
                <small>${pct(defender.fg_pct)} FG</small>
              </span>
            </div>`;
        })
        .join("")
    : '<div class="pd-empty">Sin otros defensores con muestra reciente.</div>';
}

function renderDetailTeammates(data) {
  $("#pdTeamName").textContent = data.player.team;
  const teammates = data.teammates || [];
  $("#pdTeammates").innerHTML = teammates.length
    ? teammates
        .map((teammate) => {
          const score = teammate.projected_pra ?? teammate.recent_pra;
          const scoreLabel = teammate.projected_pra != null ? "PRA proy." : "PRA reciente";
          return `
            <button class="pd-teammate" type="button" data-player-id="${esc(teammate.player_id)}" title="Abrir ficha de ${esc(teammate.player_name)}">
              <span class="pd-teammate-top">
                ${teammate.headshot_url ? `<img src="${esc(teammate.headshot_url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}
                <span>
                  <strong>${esc(teammate.player_name)}</strong>
                  <small>${esc(teammate.team)} · ${scoreLabel}</small>
                </span>
              </span>
              <span class="pd-teammate-score">${score != null ? num(score) : "—"}</span>
            </button>`;
        })
        .join("")
    : '<div class="pd-empty">Sin compañeros disponibles.</div>';
}

function renderDetailDynamic() {
  const data = state.detail.data;
  if (!data) return;
  renderDetailGames(data);
  renderDetailChart(data);
  renderDetailLinePanel(data);
}

function adjustDetailLine(delta) {
  const line = Math.max(0, Math.round((Number(state.detail.line) + delta) * 2) / 2);
  state.detail.line = line;
  renderDetailDynamic();
}

function openAnalysisForTeam(team) {
  closePlayerDetail();
  const select = $("#analysisTeam");
  if (select && team && Array.from(select.options).some((option) => option.value === team)) {
    select.value = team;
    state.analysis.team = team;
  }
  state.loaded.analysis = false;
  activateTab("analysis");
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
    const result = await api("/api/predict", {
      home,
      away,
      date,
      line: state.line,
      injuries: 1,
    });    const probability = Number(result.probability_over);
    const low = Number(result.interval_80[0]);
    const high = Number(result.interval_80[1]);
    box.dataset.ready = "1";
    box.innerHTML = `
      <div class="result-hero nba-prediction-card">
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
          <div class="result-line"><span>P(gana ${esc(result.home)})</span><strong>${pct(result.home_win_probability)}${result.availability?.model_adjusted ? ` <small>base ${pct(result.base_home_win_probability)}</small>` : ""}</strong></div>
          ${result.availability?.model_adjusted ? `<div class="result-line"><span>Ajuste por bajas</span><strong>${availabilityHtml(result.availability)}</strong></div>` : ""}
          <div class="result-line"><span>Descanso</span><strong>${esc(result.home)} ${num(result.home_rest, 0)}d · ${esc(result.away)} ${num(result.away_rest, 0)}d</strong></div>
          <div class="result-line"><span>Elo</span><strong>${num(result.home_elo, 0)} · ${num(result.away_elo, 0)}</strong></div>
          <div class="result-line"><span>Último partido</span><strong>${esc(result.home_last_game)} · ${esc(result.away_last_game)}</strong></div>
        </div>
      </div>
      ${result.analysis ? `<p class="analysis-text predict-analysis">${esc(result.analysis)}</p>` : ""}`;
    notice(
      $("#predictNotice"),
      [result.warning, result.injuries_warning].filter(Boolean).join(" "),
    );
  } catch (error) {
    box.innerHTML = "";
    notice($("#predictNotice"), error.message, true);
    showToast(error.message);
  }
}


async function loadTeamImpactPlayers() {
  const team = $("#teamImpactTeam").value;
  const select = $("#teamImpactPlayer");
  if (!team) {
    select.innerHTML = '<option value="">Selecciona primero un equipo</option>';
    $("#teamImpactSummary").innerHTML = "";
    $("#teamImpactCards").innerHTML = "";
    $("#teamImpactGames").innerHTML = ""; $("#teamImpactGamesCards").innerHTML = "";
    notice($("#teamImpactNotice"), "Selecciona un equipo para cargar sus jugadores.");
    return;
  }
  select.innerHTML = '<option value="">Cargando jugadores…</option>';
  $("#teamImpactSummary").innerHTML = "";
  $("#teamImpactCards").innerHTML = "";
  $("#teamImpactGames").innerHTML = ""; $("#teamImpactGamesCards").innerHTML = "";
  try {
    const data = await api("/api/team-impact-players", { team });
    select.innerHTML = '<option value="">Selecciona jugador</option>';
    (data.players || []).forEach((player) => {
      const option = document.createElement("option");
      option.value = player.player_id;
      option.textContent = player.name + " · " + player.gp + " PJ";
      select.append(option);
    });
    notice($("#teamImpactNotice"), data.players?.length ? "Selecciona un jugador para comparar." : "No hay jugadores disponibles para este equipo.");
  } catch (error) {
    select.innerHTML = '<option value="">Sin datos</option>';
    notice($("#teamImpactNotice"), error.message, true);
  }
}

async function loadTeamImpact() {
  const team = $("#teamImpactTeam").value;
  const playerId = $("#teamImpactPlayer").value;
  if (!team || !playerId) {
    notice($("#teamImpactNotice"), "Selecciona equipo y jugador para comenzar.");
    return;
  }
  const button = $("#teamImpactRefresh");
  setBusy(button, true);
  notice($("#teamImpactNotice"), "Analizando partidos disponibles…");
  try {
    const data = await api("/api/team-absence-impact", { team, player_id: playerId, window: $("#teamImpactWindow").value });
    if (!data.available) {
      $("#teamImpactSummary").innerHTML = "";
      $("#teamImpactCards").innerHTML = "";
      $("#teamImpactGames").innerHTML = ""; $("#teamImpactGamesCards").innerHTML = "";
      notice($("#teamImpactNotice"), data.reason || "No hay datos suficientes.");
      return;
    }
    const yes = data.with_player;
    const partial = data.partial_participation || {};
    const no = data.without_player;
    const delta = (a, b) => a == null || b == null ? "—" : signed(b - a, 1);
    const labels = [["Puntos a favor","points_for"],["Puntos recibidos","points_against"],["Diferencial","point_diff"],["Total del partido","game_total"],["Ritmo (PACE)","pace"],["Eficiencia ofensiva","off_rtg"],["Eficiencia defensiva","def_rtg"]];
    $("#teamImpactSummary").innerHTML =
      summaryCard("Participación completa", yes.games + " PJ", yes.wins + " victorias · " + pct(yes.win_rate)) +
      summaryCard("Participación parcial", partial.games + " PJ", "Media de " + num(partial.minutes) + " min") +
      summaryCard("Sin participación", no.games + " PJ", no.wins + " victorias · " + pct(no.win_rate)) +
      summaryCard("Cobertura", data.window + " partidos", "desde " + numericDate(data.coverage_start));
    $("#teamImpactCards").innerHTML = labels.map(([label,key]) =>
      '<div class="team-impact-metric"><span>' + label + '</span><b>' + num(yes[key]) +
      '</b><small>Parcial: ' + num(partial[key]) + ' · Sin participación: ' + num(no[key]) + '</small><em>Completa vs ausencia: ' +
      delta(yes[key], no[key]) + '</em></div>'
    ).join("");
    $("#teamImpactGamesCards").innerHTML = (data.games || []).map((game) => {
      const label = game.participation_label || (game.participated ? "Participó" : "Sin participación");
      const detail = game.status_detail ? " · " + game.status_detail : "";
      const opponentCode = (String(game.opponent || "").match(/\b[A-Z]{2,3}\b/g) || []).find((code) => code !== String(team).toUpperCase()) || "";
      const matchup = nbaTeamLogoMarkup(team) + '<span class="nba-matchup-vs">VS</span>' + nbaTeamLogoMarkup(opponentCode);
      return '<article class="nba-team-game-card">' +
        '<div class="nba-card-topline"><span>' + esc(numericDate(game.date)) + '</span><span class="pill ' + (game.partial ? "pill-neutral" : game.participated ? "pill-over" : "pill-neutral") + '">' + esc(label) + '</span></div>' +
        '<div class="nba-team-matchup">' + matchup + '<span class="nba-matchup-opponent">' + esc(game.opponent) + '</span></div>' +
        '<div class="nba-team-game-result"><strong>' + esc(game.result || "—") + '</strong><span>Total <b>' + num(game.game_total) + '</b></span></div>' +
        '<div class="nba-team-game-stats"><span><small>PF</small><b>' + num(game.points_for) + '</b></span><span><small>PC</small><b>' + num(game.points_against) + '</b></span><span><small>MIN</small><b>' + num(game.minutes, 1) + '</b></span></div>' +
        (detail ? '<small class="market-line">' + esc(detail) + '</small>' : '') + '</article>';
    }).join("");
    notice($("#teamImpactNotice"), [data.note, data.sample_warning].filter(Boolean).join(" "));
  } catch (error) {
    notice($("#teamImpactNotice"), error.message, true);
    showToast(error.message);
  } finally {
    setBusy(button, false);
  }
}

async function loadTeammateAbsenceImpact() {
  const team = $("#teamImpactTeam").value;
  const playerId = $("#teamImpactPlayer").value;
  if (!team || !playerId) {
    notice($("#teammateImpactNotice"), "Selecciona un equipo y un jugador en Impacto de ausencias.");
    return;
  }
  const button = $("#teammateImpactRefresh");
  setBusy(button, true);
  notice($("#teammateImpactNotice"), "Comparando el rendimiento de los compañeros…");
  try {
    const data = await api("/api/teammate-absence-impact", {
      team, player_id: playerId, window: $("#teamImpactWindow").value
    });
    $("#teammateImpactSummary").innerHTML = [
      summaryCard("Con participación", String(data.games_with || 0), "partidos"),
      summaryCard("Participación parcial", String(data.games_partial || 0), "partidos separados"),
      summaryCard("Sin participación", String(data.games_without || 0), "partidos"),
      summaryCard("Compañeros evaluados", String((data.teammates || []).length), "muestras comparables")
    ].join("");
    if (!data.available) {
      $("#teammateImpactBody").innerHTML = ""; $("#teammateImpactCards").innerHTML = "";
      notice($("#teammateImpactNotice"), data.reason || "No hay datos suficientes.");
      return;
    }
    $("#teammateImpactCards").innerHTML = data.teammates.map((player) => {
      const delta = player.delta_pts == null ? "—" : signed(player.delta_pts, 1);
      const cls = player.delta_pts > 0 ? "pill-over" : player.delta_pts < 0 ? "pill-under" : "pill-neutral";
      const headshot = nbaPlayerHeadshotUrl(player.player_id);
      const avatar = '<span class="nba-teammate-avatar"><span class="nba-teammate-initials">' + esc(initials(player.name)) + '</span>' +
        (headshot ? '<img src="' + esc(headshot) + '" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">' : '') + '</span>';
      return '<article class="nba-teammate-card"><div class="nba-teammate-heading">' + avatar +
        '<div class="nba-teammate-name"><strong>' + esc(player.name) + '</strong><small>Compañero · ' + esc(team) + '</small></div>' +
        '<span class="pill ' + cls + '">' + delta + ' PTS</span></div>' +
        '<div class="nba-teammate-comparison"><span><small>PTS con</small><b>' + num(player.with_star.pts) + '</b></span><span><small>PTS sin</small><b>' + num(player.without_star.pts) + '</b></span><span><small>MIN con/sin</small><b>' + num(player.with_star.min) + ' / ' + num(player.without_star.min) + '</b></span></div>' +
        '<div class="nba-teammate-foot"><span>AST sin ' + num(player.without_star.ast) + ' · REB sin ' + num(player.without_star.reb) + '</span><span>USO ' + signed((player.delta_usage || 0) * 100, 1) + ' pp</span><span>Muestras ' + player.with_star.games + ' / ' + player.without_star.games + '</span></div></article>';
    }).join("");
    notice($("#teammateImpactNotice"), data.note || "");
  } catch (error) {
    notice($("#teammateImpactNotice"), error.message, true);
  } finally {
    setBusy(button, false);
  }
}

function activateResultsView(name) {
  const validViews = ["history", "performance", "calibration", "errors", "models"];
  const view = validViews.includes(name) ? name : "history";
  $$(".results-nav-btn").forEach((button) => {
    const active = button.dataset.resultsView === view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  $$(".results-view").forEach((panel) => {
    const active = panel.id === `results-view-${view}`;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
}


async function loadExternalAnalysis(silent = false) {
  const container = $("#externalGames");
  const button = $("#externalRefresh");
  if (!silent) {
    container.innerHTML = '<div class="empty"><span class="spinner"></span> Cargando contexto externo…</div>';
    setBusy(button, true);
    notice($("#externalNotice"), "");
  }
  try {
    const [schedule, injuryData, externalContext] = await Promise.all([
      api("/api/upcoming", { days: Number($("#externalDays").value || 3), team: $("#externalTeam").value, line: state.line, live: 1, injuries: 1 }),
      api("/api/injuries"),
      api("/api/external-context", { days: Number($("#externalDays").value || 3), team: $("#externalTeam").value }),
    ]);
    state.loaded.external = true;
    $("#externalRange").textContent = schedule.start + " → " + schedule.end;
    const injuryByTeam = new Map((injuryData.teams || []).map((team) => [String(team.team).toUpperCase(), team]));
    const games = schedule.games || [];
    const b2bCount = games.filter((game) => Number(game.home_rest) <= 0 || Number(game.away_rest) <= 0).length;
    const injuryTeams = new Set(games.flatMap((game) => [game.home, game.away]).filter((team) => {
      const item = injuryByTeam.get(String(team).toUpperCase());
      return item && Number(item.injury_count) > 0;
    }));
    $("#externalSummary").innerHTML = [
      summaryCard("Partidos", String(games.length), "en el período"),
      summaryCard("Partidos con B2B", String(b2bCount), "al menos un equipo sin día de descanso"),
      summaryCard("Equipos con bajas", String(injuryTeams.size), "según ESPN"),
      summaryCard("Fuente de lesiones", injuryData.source || "ESPN", injuryData.source_updated_at ? "actualizada " + injuryData.source_updated_at : "estado de la fuente"),
    ].join("");
    if (!games.length) {
      container.innerHTML = '<div class="empty">No hay partidos para el período o filtro seleccionado.</div>';
      return;
    }
    const restText = (value) => {
      const n = Number(value);
      if (!Number.isFinite(n)) return "Sin dato";
      if (n <= 0) return "Sin días de descanso (B2B)";
      return n + " días de descanso";
    };
    const injuryHtml = (teamCode) => {
      const item = injuryByTeam.get(String(teamCode).toUpperCase());
      if (!item) return '<span class="external-muted">Parte no disponible</span>';
      const players = (item.injuries || []).slice(0, 4).map((p) =>
        '<li><strong>' + esc(p.player_name) + '</strong><span>' + esc(p.status_label || p.status || "Estado sin confirmar") + '</span></li>'
      ).join("");
      return '<div class="external-injuries"><strong>' + Number(item.injury_count || 0) + ' reporte(s) · ' + Number(item.out_count || 0) + ' OUT</strong>' +
        (players ? '<ul>' + players + '</ul>' : '<span class="external-muted">Sin bajas reportadas</span>') + '</div>';
    };
    const externalByGame = new Map((externalContext.games || []).map((item) => [String(item.game_id), item]));
    container.innerHTML = games.map((game) => {
      const external = externalByGame.get(String(game.game_id)) || {};
      const homeRest = Number(game.home_rest);
      const awayRest = Number(game.away_rest);
      const homeB2B = Number.isFinite(homeRest) && homeRest <= 0;
      const awayB2B = Number.isFinite(awayRest) && awayRest <= 0;
      const homeInj = injuryByTeam.get(String(game.home).toUpperCase());
      const awayInj = injuryByTeam.get(String(game.away).toUpperCase());
      const homeOut = Number(homeInj?.out_count || 0);
      const awayOut = Number(awayInj?.out_count || 0);
      const homeImpact = game.availability?.home?.impact_points;
      const awayImpact = game.availability?.away?.impact_points;
      const context = [];
      if (homeB2B && !awayB2B) context.push("Visitante con ventaja de descanso");
      if (awayB2B && !homeB2B) context.push("Local con ventaja de descanso");
      if (homeOut > awayOut) context.push("Local con más bajas OUT reportadas");
      if (awayOut > homeOut) context.push("Visitante con más bajas OUT reportadas");
      if (!context.length) context.push("Sin ventaja contextual clara con los datos disponibles");
      const travelLine = (side) => {
        const item = external.travel?.[side];
        if (!item || item.distance_km == null) return '<small>Viaje: ' + esc(item?.reason || "Sin dato verificable") + '</small>';
        return '<small>Viaje desde ' + esc(item.from_arena || "recinto anterior") + ': <b>' + num(item.distance_km, 0) + ' km</b> hasta ' + esc(item.to_arena || game.home) + ' · partido previo ' + esc(item.previous_game_date || "—") + '</small>';
      };
      const splitLine = (side, label) => {
        const item = external.splits?.[side];
        if (!item || !item.games) return '<div class="external-split"><b>' + label + '</b><small>Muestra local/visita insuficiente</small></div>';
        return '<div class="external-split"><b>' + label + ' · ' + item.games + ' PJ</b><small>PF ' + num(item.points_for) + ' · PC ' + num(item.points_against) + ' · Margen ' + signed(item.margin) + ' · Total ' + num(item.game_total) + '</small></div>';
      };
      const scenarioLine = (side, label) => {
        const scenarios = external.absence_scenarios?.[side] || [];
        if (!scenarios.length) return '<p class="external-muted">' + label + ': sin muestra suficiente para estimar escenarios individuales (mínimo 3 partidos con y 3 sin el jugador).</p>';
        return '<div class="external-scenarios">' + scenarios.map((item) => {
          const base = Number(game.predicted_total);
          const plays = Number.isFinite(base) ? base + Number(item.total_shift_if_plays || 0) : null;
          const out = Number.isFinite(base) ? base + Number(item.total_shift_if_out || 0) : null;
          return '<div class="external-scenario"><b>' + esc(item.player_name) + ' · ' + esc(item.status) + '</b>' +
            '<small>Con jugador: ' + num(plays) + ' pts · Sin jugador: ' + num(out) + ' pts</small>' +
            '<small>Histórico: ' + item.games_with + ' con / ' + item.games_without + ' sin · Δ total ' + signed(item.delta_game_total) + ' pts · Δ PF equipo ' + signed(item.delta_team_points) + '</small></div>';
        }).join("") + '</div>';
      };
      return '<article class="external-game-card">' +
        '<header class="external-game-head"><div><span class="external-date">' + esc(shortDate(chileGameDate(game.date, game.time_et))) + ' · ' + esc(chileGameTime(game.date, game.time_et) || "Hora por confirmar") + ' Chile</span><h3>' + esc(game.away_name || game.away) + ' <span class="vs">@</span> ' + esc(game.home_name || game.home) + '</h3></div><span class="external-game-total">Modelo ' + num(game.predicted_total) + ' pts</span></header>' +
        '<div class="external-matchup-grid">' +
          '<section><h4>Visitante · ' + esc(game.away) + '</h4><p><b>Descanso:</b> ' + esc(restText(game.away_rest)) + (awayB2B ? ' <span class="external-flag">B2B</span>' : '') + '</p>' + travelLine("away") + '<b>Bajas</b>' + injuryHtml(game.away) + (awayImpact != null ? '<small>Ajuste experimental del modelo: ' + signed(awayImpact,1) + ' pts</small>' : '') + '</section>' +
          '<section><h4>Local · ' + esc(game.home) + '</h4><p><b>Descanso:</b> ' + esc(restText(game.home_rest)) + (homeB2B ? ' <span class="external-flag">B2B</span>' : '') + '</p>' + travelLine("home") + '<b>Bajas</b>' + injuryHtml(game.home) + (homeImpact != null ? '<small>Ajuste experimental del modelo: ' + signed(homeImpact,1) + ' pts</small>' : '') + '</section>' +
        '</div>' +
        '<div class="external-context"><b>Lectura contextual</b><p>' + esc(context.join(" · ")) + '</p><small>Localía: ' + esc(game.home_name || game.home) + ' juega en casa. Ventaja media de localía en la muestra: ' + (external.home_advantage?.home_margin == null ? "sin datos" : signed(external.home_advantage.home_margin) + ' pts para el local en ' + external.home_advantage.games + ' partidos') + '. No se aplica al pronóstico.</small></div>' +
        '<div class="external-split-grid">' + splitLine("away", (game.away_name || game.away) + " · visitante") + splitLine("home", (game.home_name || game.home) + " · local") + '</div>' +
        '<section class="external-sensitivity"><b>Sensibilidad por disponibilidad</b><p class="external-muted">Proyección hipotética individual basada en promedios históricos con/sin cada jugador. No es causal, no se suma entre jugadores y no modifica la predicción oficial.</p>' + scenarioLine("away", "Visitante") + scenarioLine("home", "Local") + '</section>' +
        '</article>';
    }).join("");
    const warnings = [schedule.warning, schedule.injuries_warning, injuryData.warning, externalContext.injuries_warning].filter(Boolean);
    $("#externalNote").textContent = "Descanso obtenido del calendario del modelo. Lesiones: ESPN" + (injuryData.source_updated_at ? " · actualización " + injuryData.source_updated_at : "") + ". Los recuentos de bajas no equivalen a impacto de minutos; OUT, cuestionables y dudas se muestran por separado." + (warnings.length ? " Avisos: " + warnings.join(" ") : "");
  } catch (error) {
    if (!silent) {
      container.innerHTML = '<div class="empty">No se pudo cargar el análisis externo.</div>';
      notice($("#externalNotice"), error.message, true);
    }
  } finally {
    setBusy(button, false);
  }
}

function activateTab(name) {
  state.active = name;
  $$(".tab").forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    if (active) {
      button.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  });
  $$(".tab-panel").forEach((panel) => {
    const active = panel.id === `tab-${name}`;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
  if (name === "upcoming") loadUpcoming(state.loaded.upcoming);
  if (name === "analysis") loadAnalysis(state.loaded.analysis);
  if (name === "external") loadExternalAnalysis(state.loaded.external);
  if (name === "results") loadResults(state.loaded.results);
  if (name === "model-comparison") loadModelComparison(state.loaded.comparison);
  if (name === "comparison") loadModelComparison(state.loaded.comparison);
  if (name === "players" && !state.loaded.players) loadPlayers();
  if (name === "props" && !state.loaded.props) loadProps();
  if (name === "teams") loadTeamImpactPlayers();
  if (name === "injuries") {
    window.dispatchEvent(new CustomEvent("injuries:activate"));
  }
  if (name === "betbuilder") {
    window.dispatchEvent(new CustomEvent("betbuilder:activate"));
  }
}

function bindEvents() {
  // Un control opcional ausente no debe impedir que se conecten los demás filtros.
  const bind = (selector, eventName, handler) => {
    const element = $(selector);
    if (!element) {
      console.warn("[NBA Totals] Control no encontrado:", selector);
      return;
    }
    element.addEventListener(eventName, handler);
  };
  let savedUpcomingView = "cards";
  try { savedUpcomingView = localStorage.getItem("nba-upcoming-view") || "cards"; } catch (_) {}
  setUpcomingView(savedUpcomingView, false);
  // La navegación de NBA Cards se registra primero para que un fallo
  // en otro control de la interfaz no deje inutilizable la sección Jugadores.
  const playersBody = $("#playersBody");
  if (playersBody && !playersBody.dataset.cardNavigationBound) {
    playersBody.dataset.cardNavigationBound = "true";
    playersBody.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      const row = target?.closest("tr[data-player-id]");
      if (!row || !playersBody.contains(row)) return;
      event.preventDefault();
      const playerId = Number(row.dataset.playerId);
      if (!Number.isInteger(playerId) || playerId <= 0) {
        showToast("No se encontró un ID válido para este jugador.");
        return;
      }
      openPlayerDetail(playerId, {
        opener: row.querySelector(".player-link") || row
      });
    });
  }
  const playersCards = $("#playersCards");
  if (playersCards && !playersCards.dataset.cardNavigationBound) {
    playersCards.dataset.cardNavigationBound = "true";
    playersCards.addEventListener("click", (event) => {
      const card = event.target instanceof Element ? event.target.closest(".nba-player-card[data-player-id]") : null;
      if (!card || !playersCards.contains(card)) return;
      const playerId = Number(card.dataset.playerId);
      if (!Number.isInteger(playerId) || playerId <= 0) return;
      openPlayerDetail(playerId, { opener: card });
    });
  }
  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
  });
  bind("#resultsDisplayToggle", "click", (event) => {
    const button = event.target.closest("[data-results-display]");
    if (!button) return;
    const mode = button.dataset.resultsDisplay;
    const cards = mode === "cards";
    $("#resultsCards").hidden = !cards;
    $("#resultsTableWrap").hidden = cards;
    $("#resultsDisplayToggle [data-results-display]").forEach((item) => {
      const active = item.dataset.resultsDisplay === mode;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-pressed", String(active));
    });
  });
  bind("#lineInput", "change", (event) => setLine(event.target.value));
  bind("#lineDown", "click", () => setLine(state.line - 0.5));
  bind("#lineUp", "click", () => setLine(state.line + 0.5));

  bind("#upcomingRefresh", "click", loadUpcoming);
  $$("[data-upcoming-view]").forEach((button) => button.addEventListener("click", () => setUpcomingView(button.dataset.upcomingView)));
  bind("#upcomingDays", "change", (event) => {
    state.upcoming.days = Number(event.target.value);
    loadUpcoming();
  });
  bind("#upcomingTeam", "change", (event) => {
    state.upcoming.team = event.target.value;
    loadUpcoming();
  });


  bind("#analysisRefresh", "click", () => loadAnalysis());


  bind("#analysisDays", "change", (event) => {
    state.analysis.days = Number(event.target.value);
    loadAnalysis();
  });
  bind("#analysisTeam", "change", (event) => {
    state.analysis.team = event.target.value;
    loadAnalysis();
  });

  bind("#teamImpactTeam", "change", loadTeamImpactPlayers);
  bind("#teamImpactPlayer", "change", loadTeamImpact);
  bind("#teamImpactWindow", "change", loadTeamImpact);
  bind("#teamImpactRefresh", "click", loadTeamImpact);
  bind("#teammateImpactRefresh", "click", loadTeammateAbsenceImpact);
  bind("#teamImpactPlayer", "change", loadTeammateAbsenceImpact);
  bind("#teamImpactWindow", "change", loadTeammateAbsenceImpact);

  bind("#resultsRefresh", "click", loadResults);


  $$(".results-nav-btn").forEach((button) => {
    button.addEventListener("click", () => activateResultsView(button.dataset.resultsView));
  });
  bind("#resultsSeason", "change", (event) => {
    state.results.season = event.target.value;
    loadResults();
  });
  bind("#resultsTeam", "change", (event) => {
    state.results.team = event.target.value;
    loadResults();
  });
  bind("#resultsLimit", "change", (event) => {
    state.results.limit = Number(event.target.value);
    loadResults();
  });
  bind("#resultsWindow", "change", (event) => {
    state.results.window = Number(event.target.value);
    loadResults();
  });

  bind("#playersRefresh", "click", loadPlayers);
  bind("#playersSeason", "change", (event) => {
    state.players.season = event.target.value;
    state.players.minGp = event.target.value === "2025-26" ? 10 : 1;
    $("#playersMinGp").value = String(state.players.minGp);
    loadPlayers();
  });
  bind("#playersMode", "change", (event) => {
    state.players.mode = event.target.value;
    loadPlayers();
  });
  bind("#playersSort", "change", (event) => {
    state.players.sort = event.target.value;
    loadPlayers();
  });
  bind("#playersTeam", "change", (event) => {
    state.players.team = event.target.value;
    loadPlayers();
  });
  bind("#playersMinGp", "change", (event) => {
    const value = Number(event.target.value);
    state.players.minGp = Math.min(82, Math.max(1, value || 1));
    event.target.value = String(state.players.minGp);
    loadPlayers();
  });
  bind("#playersLimit", "change", (event) => {
    state.players.limit = Number(event.target.value);
    loadPlayers();
  });
  let searchTimer;
  bind("#playersSearch", "input", (event) => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.players.q = event.target.value.trim();
      loadPlayers();
    }, 350);
  });

  bind("#propsRefresh", "click", () => loadProps());
  bind("#propsMarket", "change", renderPropsCandidates);
  bind("#propsCandidates", "click", (event) => {
    const button = event.target.closest("[data-props-player]");
    if (!button) return;
    const playerId = String(button.dataset.propsPlayer || "");
    if (!playerId) return;
    state.props.selectedPlayerId = playerId;
    state.props.selectedStat = button.dataset.propsStat || "pra";
    state.props.card = null;
    state.props.line = null;
    $("#propsPlayer").value = playerId;
    $("#propsStat").value = state.props.selectedStat;
    loadPropsPlayerCard(playerId);
    $("#propsAnalyzerTitle").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  bind("#positionVsRefresh", "click", loadPositionVsOpponent);
  bind("#positionVsSort", "change", renderPositionVsPlayers);
  bind("#positionVsTeam", "change", loadPositionVsOpponent);
  bind("#positionVsRole", "change", loadPositionVsOpponent);
  bind("#positionVsWindow", "change", loadPositionVsOpponent);
  bind("#propsPlayer", "change", (event) => {
    state.props.selectedPlayerId = event.target.value;
    state.props.card = null;
    state.props.line = null;
    loadPropsPlayerCard(state.props.selectedPlayerId);
  });
  bind("#propsStat", "change", (event) => {
    state.props.selectedStat = event.target.value;
    state.props.line = state.props.card ? pdDefaultLine(state.props.card, state.props.selectedStat) : 0.5;
    renderPropsAnalyzer();
  });
  bind("#propsLine", "change", (event) => {
    const value = Number(event.target.value);
    if (!Number.isFinite(value) || value < 0) return;
    state.props.line = Math.round(value * 2) / 2;
    renderPropsAnalyzer();
  });
  bind("#propsLineDown", "click", () => { state.props.line = Math.max(0, propsLineValue() - 0.5); renderPropsAnalyzer(); });
  bind("#propsLineUp", "click", () => { state.props.line = propsLineValue() + 0.5; renderPropsAnalyzer(); });
  bind("#propsDate", "change", (event) => {
    state.props.date = event.target.value;
    state.props.gameId = "";
    populatePropsGameFilters();
    renderPropsCandidates();
  });
  bind("#propsGame", "change", (event) => {
    state.props.gameId = event.target.value;
    renderPropsCandidates();
  });
  bind("#propsDays", "change", (event) => {
    const days = Number(event.currentTarget.value);
    if (!Number.isInteger(days) || days < 1 || days > 14) return;
    state.props.days = days;
    loadProps();
  });
  bind("#propsTeam", "change", (event) => {
    state.props.team = event.target.value;
    loadProps();
  });
  bind("#propsLimit", "change", (event) => {
    state.props.limit = Number(event.target.value);
    loadProps();
  });

  bind("#pdClose", "click", closePlayerDetail);
  bind("#playerDetail", "click", (event) => {
    if (event.target === $("#playerDetail")) closePlayerDetail();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#playerDetail").hidden) closePlayerDetail();
  });
  bind("#pdTabs", "click", (event) => {
    const button = event.target.closest("[data-stat]");
    if (!button || !state.detail.data) return;
    state.detail.stat = button.dataset.stat;
    state.detail.line = pdDefaultLine(state.detail.data, state.detail.stat);
    renderDetailTabs(state.detail.data);
    renderDetailDynamic();
  });
  bind("#pdLineDown", "click", () => adjustDetailLine(-0.5));
  bind("#pdLineUp", "click", () => adjustDetailLine(0.5));
  bind("#pdLineInput", "change", (event) => {
    const value = Number(event.target.value);
    if (!Number.isFinite(value) || value < 0) return;
    state.detail.line = Math.round(value * 2) / 2;
    renderDetailDynamic();
  });
  bind("#pdQuickViews", "click", (event) => {
    const button = event.target.closest("[data-range]");
    if (!button) return;
    state.detail.range =
      button.dataset.range === "all" ? "all" : Number(button.dataset.range);
    renderDetailDynamic();
  });
  bind("#pdVenueFilter", "click", (event) => {
    const button = event.target.closest("[data-venue]");
    if (!button) return;
    state.detail.venue = button.dataset.venue;
    renderDetailDynamic();
  });
  bind("#pdGameList", "click", (event) => {
    const button = event.target.closest("[data-game]");
    if (!button || !state.detail.data) return;
    if (button.dataset.game === "next") {
      openAnalysisForTeam(state.detail.data.player.team);
      return;
    }
    const game = (state.detail.data.games || []).find(
      (item) => String(item.game_id) === String(button.dataset.gameId)
    );
    if (!game) return;
    state.detail.line = Math.max(
      0.5,
      Math.round(pdStatValue(game, state.detail.stat) * 2) / 2
    );
    renderDetailDynamic();
  });
  bind("#pdTeammates", "click", (event) => {
    const button = event.target.closest("[data-player-id]");
    if (!button) return;
    openPlayerDetail(button.dataset.playerId, { opener: button });
  });
  bind("#pdAnalysis", "click", () => {
    if (state.detail.data?.player?.team) {
      openAnalysisForTeam(state.detail.data.player.team);
    }
  });
  $("#pdInjuriesLink")?.addEventListener("click", () => {
    const team = state.detail.data?.player?.team;
    closePlayerDetail();
    activateTab("injuries");
    if (team) {
      window.dispatchEvent(
        new CustomEvent("injuries:focus", { detail: { team } })
      );
    }
  });

  const propsBody = $("#propsBody");
  if (propsBody) {
    propsBody.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      const row = target?.closest("[data-player-id]");
      if (!row || !propsBody.contains(row)) return;
      openPlayerDetail(row.dataset.playerId, {
        opener: row.querySelector?.(".player-link") || row
      });
    });
  }

  bind("#predictForm", "submit", submitPrediction);

  // Refresco silencioso: la NBA actualiza el marcador y los finales aparecen solos.
  window.setInterval(() => {
    if (document.visibilityState !== "visible") return;
    if (state.active === "upcoming") loadUpcoming(true);
    if (state.active === "analysis") loadAnalysis(true);
    if (state.active === "external") loadExternalAnalysis(true);
    if (state.active === "results") loadResults(true);
    if (state.active === "props") loadProps(true);
  }, 60000);
}

async function init() {
  try {
    bindEvents();
    const meta = await api("/api/meta");
    state.meta = meta;
    state.line = Number(meta.default_line || 225.5);
    $("#lineInput").value = state.line.toFixed(1);
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
      state.props.days = 3;
      $("#propsDays").value = "3";
    }

    await loadUpcoming();
    const detailMatch = window.location.hash.match(/^#ficha\/(\d+)$/);
    if (detailMatch) openPlayerDetail(detailMatch[1]);
  } catch (error) {
    showToast(error.message);
    const tbody = $("#upcomingBody");
    if (tbody) emptyRow(tbody, 9, "No se pudo conectar con la API.");
  }
}

document.addEventListener("DOMContentLoaded", init);