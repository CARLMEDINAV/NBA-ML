(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const state = { timer: null, eventId: "", games: [], loading: false };
  const API = "/api/live";

  function safeNumber(value, fallback = 0) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }
  function gaussian() {
    const u = Math.max(Math.random(), 1e-9);
    const v = Math.max(Math.random(), 1e-9);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }
  function remainingMinutes(period, clock) {
    const match = String(clock || "12:00").match(/^(\d{1,2}):(\d{2})$/);
    const seconds = match ? Number(match[1]) * 60 + Number(match[2]) : 720;
    if (period <= 4) return Math.max(0, (4 - period) * 12 + seconds / 60);
    return Math.max(0, (period - 4 - 1) * 5 + seconds / 60);
  }
  function probabilityOfFutureRun(currentMargin, minutesLeft, elapsedMinutes) {
    if (minutesLeft <= 0) return currentMargin >= 20 ? 1 : 0;
    // Lightweight live estimate, not a trained/calibrated betting model.
    // Score-margin drift is shrunk toward the current game pace.
    const driftPerMinute = (currentMargin / Math.max(12, elapsedMinutes + minutesLeft)) * 0.72;
    const steps = Math.max(1, Math.ceil(minutesLeft));
    const dt = minutesLeft / steps;
    const sigma = 3.65 * Math.sqrt(dt);
    const runs = 2400;
    let hit = 0;
    for (let run = 0; run < runs; run += 1) {
      let margin = currentMargin;
      let maximum = margin;
      for (let step = 0; step < steps; step += 1) {
        margin += driftPerMinute * dt + gaussian() * sigma;
        maximum = Math.max(maximum, margin);
        if (maximum >= 20) { hit += 1; break; }
      }
    }
    return hit / runs;
  }
  function pct(value) { return Math.round(value * 100); }
  function teamLabel(team) { return String(team || "—").toUpperCase(); }

  async function getJson(url) {
    const response = await fetch(url, { cache: "no-store", headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
  }
  function scoringMax(plays, homeNow, awayNow) {
    let homeMax = 0, awayMax = 0, homePeak = 0, awayPeak = 0;
    let hasScoreSequence = false;
    (plays || []).forEach((play) => {
      const home = Number(play.home_score), away = Number(play.away_score);
      if (!Number.isFinite(home) || !Number.isFinite(away)) return;
      hasScoreSequence = true;
      const margin = home - away;
      homeMax = Math.max(homeMax, margin);
      awayMax = Math.max(awayMax, -margin);
      if (margin >= homePeak) homePeak = margin;
      if (-margin >= awayPeak) awayPeak = -margin;
    });
    const current = safeNumber(homeNow) - safeNumber(awayNow);
    homeMax = Math.max(homeMax, current, 0);
    awayMax = Math.max(awayMax, -current, 0);
    return { homeMax, awayMax, hasScoreSequence };
  }
  function renderCard(game, plays) {
    const host = $("#blowoutTrackerResult");
    if (!host) return;
    const home = teamLabel(game.home_team), away = teamLabel(game.away_team);
    const homeScore = safeNumber(game.home_score), awayScore = safeNumber(game.away_score);
    const margin = homeScore - awayScore;
    const status = String(game.status || "");
    const periodMatch = status.match(/(?:Q|OT)(\d*)/i);
    const period = status.match(/OT/i) ? 5 + safeNumber(periodMatch && periodMatch[1]) : safeNumber(periodMatch && periodMatch[1], 2);
    const clockMatch = status.match(/(\d{1,2}:\d{2})/);
    const clock = clockMatch ? clockMatch[1] : "12:00";
    const elapsed = Math.max(1, (period <= 4 ? (period - 1) * 12 : 48 + (period - 5) * 5) + (12 - (safeNumber(clock.split(":")[0], 12) + safeNumber(clock.split(":")[1]) / 60)));
    const left = remainingMinutes(period, clock);
    const max = scoringMax(plays, homeScore, awayScore);
    const homeReached = max.homeMax >= 20;
    const awayReached = max.awayMax >= 20;
    const homeProb = homeReached ? 1 : probabilityOfFutureRun(margin, left, elapsed);
    const awayProb = awayReached ? 1 : probabilityOfFutureRun(-margin, left, elapsed);
    const homePeak = Math.round(max.homeMax), awayPeak = Math.round(max.awayMax);
    const lead = margin >= 0 ? home : away;
    const leadMargin = Math.abs(margin);
    const active = left > 0;
    const info = !active ? "Partido finalizado" : `${status} · ${Math.ceil(left)} min restantes aprox.`;
    host.innerHTML = `
      <div class="blowout-head"><div><span class="blowout-kicker">NBA TOTALS · LIVE EVENT TRACKER</span><h3>${away} @ ${home}</h3><p>${info}</p></div><span class="blowout-live-dot">${active ? "LIVE" : "FINAL"}</span></div>
      <div class="blowout-score"><div><small>${away}</small><strong>${awayScore}</strong></div><span>—</span><div><small>${home}</small><strong>${homeScore}</strong></div></div>
      <div class="blowout-current">${leadMargin ? `${lead} lidera por ${leadMargin}` : "Partido empatado"} <span>· máxima ventaja registrada: ${Math.max(homePeak, awayPeak)} pts</span></div>
      <div class="blowout-prob-grid">
        <article class="${homeReached ? "is-confirmed" : ""}"><small>${home} · ventaja +20</small><strong>${homeReached ? "CONFIRMADO" : pct(homeProb) + "%"}</strong><span>${homeReached ? `Ya llegó a +${homePeak}` : "Probabilidad estimada de alcanzarla"}</span></article>
        <article class="${awayReached ? "is-confirmed" : ""}"><small>${away} · ventaja +20</small><strong>${awayReached ? "CONFIRMADO" : pct(awayProb) + "%"}</strong><span>${awayReached ? `Ya llegó a +${awayPeak}` : "Probabilidad estimada de alcanzarla"}</span></article>
      </div>
      <div class="blowout-foot"><span>Máxima ventaja: ${home} +${homePeak} · ${away} +${awayPeak}</span><span>Play-by-play: ${max.hasScoreSequence ? "disponible" : "marcador actual"}</span></div>
      <p class="blowout-disclaimer">Estimación experimental Monte Carlo basada en el marcador y el tiempo restante. No es una probabilidad calibrada ni una recomendación de apuesta. El evento +20 queda confirmado si aparece en el play-by-play.</p>`;
  }
  async function refresh() {
    if (state.loading) return;
    state.loading = true;
    const host = $("#blowoutTrackerResult");
    const select = $("#blowoutGameSelect");
    try {
      const data = await getJson(`${API}/games`);
      state.games = data.games || [];
      const previous = select.value || state.eventId;
      select.innerHTML = state.games.length
        ? state.games.map((g) => `<option value="${g.event_id}">${teamLabel(g.away_team)} @ ${teamLabel(g.home_team)} · ${g.away_score}-${g.home_score} · ${g.status}</option>`).join("")
        : '<option value="">No hay partidos en vivo</option>';
      if (state.games.some((g) => g.event_id === previous)) select.value = previous;
      state.eventId = select.value;
      const game = state.games.find((g) => g.event_id === state.eventId);
      if (!game) {
        host.innerHTML = '<p class="muted">No hay partidos en vivo. El tracker se activará cuando comience un encuentro.</p>';
        return;
      }
      const playData = await getJson(`${API}/plays?event_id=${encodeURIComponent(game.event_id)}`);
      renderCard(game, playData.plays || []);
      if (playData.warning) $("#blowoutTrackerNotice").textContent = playData.warning;
      else $("#blowoutTrackerNotice").textContent = "";
    } catch (error) {
      host.innerHTML = '<p class="muted">No se pudo actualizar el tracker. Intenta nuevamente.</p>';
      $("#blowoutTrackerNotice").textContent = error.message;
    } finally {
      state.loading = false;
    }
  }
  document.addEventListener("DOMContentLoaded", () => {
    if (!$("#blowoutTrackerResult")) return;
    $("#blowoutTrackerRefresh").addEventListener("click", refresh);
    $("#blowoutGameSelect").addEventListener("change", refresh);
    state.timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !$("#tab-lab-live").hidden) refresh();
    }, 15000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && !$("#tab-lab-live").hidden) refresh();
    });
    refresh();
  });
  window.addEventListener("beforeunload", () => window.clearInterval(state.timer));
})();