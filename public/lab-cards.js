(() => {
  const $ = (id) => document.getElementById(id);
  const fmt = (value, digits = 1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
  const pct = (value) => value == null ? "—" : (Number(value) * 100).toFixed(1) + "%";
  const safe = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  document.addEventListener("nba:lab-result", (event) => {
    const data = event.detail || {}, base = data.baseline || {}, scenario = data.scenario || {}, change = data.changes || {};
    $("labGameCard").innerHTML = `
      <div class="lab-card-matchup">${safe(data.matchup?.away)} <span>@</span> ${safe(data.matchup?.home)}</div>
      <div class="lab-card-date">${safe(data.matchup?.date)} · ${safe(data.model_name || "Modelo actual")}</div>
      <div class="lab-card-score"><div><small>BASELINE ML</small><strong>${fmt(base.total)}</strong><span>${fmt(base.away_score)} – ${fmt(base.home_score)}</span></div><div class="is-highlight"><small>ESCENARIO</small><strong>${fmt(scenario.total)}</strong><span>${fmt(scenario.away_score)} – ${fmt(scenario.home_score)}</span></div></div>
      <div class="lab-card-stats"><span>Δ total <b>${Number(change.total) >= 0 ? "+" : ""}${fmt(change.total)}</b></span><span>P(OVER) <b>${pct(scenario.probability_over)}</b></span><span>Victoria local <b>${pct(scenario.home_win_probability)}</b></span></div>
      <p class="lab-card-foot">Escenario calculado con Ridge y ajustes configurados en NBA Lab.</p>`;
  });
  document.addEventListener("nba:live-result", (event) => {
    const data = event.detail || {}, score = data.score || {};
    $("labLiveCard").innerHTML = `
      <div class="lab-card-matchup">${safe(data.away_team)} <span>@</span> ${safe(data.home_team)}</div>
      <div class="lab-card-date"><span class="lab-live-dot"></span> EN VIVO · ${data.period ? "Q" + safe(data.period) : "Manual"} · ${safe(data.clock || "—")} · ${safe(data.source || "manual")}</div>
      <div class="lab-card-live-score"><strong>${fmt(score.away,0)} – ${fmt(score.home,0)}</strong><small>MARCADOR ACTUAL</small></div>
      <div class="lab-card-projection"><small>TOTAL FINAL PROYECTADO</small><strong>${fmt(data.projected_total)}</strong><span>Ritmo observado: ${fmt(data.pace_projection)} · Influencia live: ${pct(data.live_weight)}</span></div>
      <p class="lab-card-foot">Estimación híbrida con ritmo observado y total prepartido; no es una probabilidad calibrada de apuesta.</p>`;
  });
  $("labPlayerCardForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("labCardPlayer").value.trim(), team = $("labCardTeam").value;
    const pts = Number($("labCardPts").value), reb = Number($("labCardReb").value), ast = Number($("labCardAst").value), min = Number($("labCardMin").value);
    if (!name || [pts,reb,ast,min].some((v) => !Number.isFinite(v) || v < 0)) return;
    const pra = pts + reb + ast;
    $("labPlayerCard").innerHTML = `
      <div class="lab-player-head"><div><div class="lab-card-matchup">${safe(name)}</div><div class="lab-card-date">${safe(team)} · PROYECCIÓN INGRESADA</div></div><span class="lab-manual-tag">MANUAL</span></div>
      <div class="lab-player-main"><strong>${fmt(pts)}</strong><small>PTS</small><strong>${fmt(pra)}</strong><small>PRA</small></div>
      <div class="lab-card-stats"><span>REB <b>${fmt(reb)}</b></span><span>AST <b>${fmt(ast)}</b></span><span>MIN <b>${fmt(min)}</b></span></div>
      <p class="lab-card-foot">PTS + REB + AST = ${fmt(pra)} PRA. Valores introducidos manualmente; no provienen del modelo individual.</p>`;
  });
})();