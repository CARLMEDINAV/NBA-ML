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

  const searchInput = $("labCardPlayerSearch");
  const searchButton = $("labCardSearchBtn");
  const searchResults = $("labCardSearchResults");
  const playerOutput = $("labPlayerCard");
  const unwrap = (payload) => payload?.data && typeof payload.data === "object" ? payload.data : payload;
  const avg = (rows, key) => {
    const values = rows.map((row) => Number(row[key])).filter(Number.isFinite);
    return values.length ? values.reduce((a,b)=>a+b,0)/values.length : null;
  };
  const stat = (value) => value == null || !Number.isFinite(Number(value)) ? "—" : fmt(value);
  const pctLabel = (value) => value == null ? "—" : (Number(value)*100).toFixed(1)+"%";
  const renderWindow = (games, count) => {
    const sample = games.slice(0,count);
    return {gp:sample.length, min:avg(sample,"min"), pts:avg(sample,"pts"), reb:avg(sample,"reb"), ast:avg(sample,"ast"), pra:avg(sample,"pra")};
  };
  const renderPlayerCard = (data) => {
    const player = data.player || {}, averages = player.averages || {}, projection = data.upcoming?.projection || {};
    const games = Array.isArray(data.games) ? data.games : [];
    const last5 = renderWindow(games,5), last10 = renderWindow(games,10);
    const upcoming = data.upcoming;
    const projected = ["pts","reb","ast","pra"].some(key => projection[key] != null);
    const headshot = player.headshot_url ? `<img class="lab-player-avatar" src="${safe(player.headshot_url)}" alt="" loading="lazy">` : "";
    const matchup = upcoming ? `${safe(upcoming.away)} @ ${safe(upcoming.home)} · ${safe(upcoming.date)}` : "Sin próximo partido disponible en el calendario";
    const rows = games.slice(0,5).map(game => `<tr><td>${safe(game.date)}</td><td>${safe(game.opponent)} ${game.is_home ? "(L)" : "(V)"}</td><td>${stat(game.min)}</td><td>${stat(game.pts)}</td><td>${stat(game.reb)}</td><td>${stat(game.ast)}</td><td>${stat(game.pra)}</td></tr>`).join("");
    playerOutput.innerHTML = `
      <div class="lab-player-profile">
        ${headshot}
        <div><div class="lab-card-matchup">${safe(player.player_name)}</div><div class="lab-card-date">${safe(player.team_name || player.team)} · ${safe(player.season || data.season)} · ${safe(player.gp)} PJ</div><div class="lab-card-date">Actualizado: ${safe(data.player_state_date || "—")}</div></div>
      </div>
      <div class="lab-card-projection"><small>PRÓXIMO PARTIDO</small><strong>${matchup}</strong><span>${projected ? "Proyección calculada por Player Props (Ridge)" : "No hay proyección disponible para el próximo partido"}</span></div>
      <div class="lab-card-kicker">PROMEDIOS DE TEMPORADA</div>
      <div class="lab-player-metric-grid">${[["PTS",averages.pts],["REB",averages.reb],["AST",averages.ast],["PRA",averages.pra],["MIN",averages.min]].map(([label,value])=>`<div><strong>${stat(value)}</strong><small>${label}</small></div>`).join("")}</div>
      <div class="lab-card-kicker">TENDENCIA RECIENTE</div>
      <div class="lab-player-windows">
        ${[["ÚLTIMOS 5",last5],["ÚLTIMOS 10",last10]].map(([label,window])=>`<div class="lab-player-window"><b>${label}</b><div>PTS <strong>${stat(window.pts)}</strong> · REB <strong>${stat(window.reb)}</strong> · AST <strong>${stat(window.ast)}</strong> · PRA <strong>${stat(window.pra)}</strong></div><small>${window.gp} partidos · ${stat(window.min)} MIN</small></div>`).join("")}
      </div>
      <div class="lab-card-kicker">PROYECCIÓN DEL MODELO</div>
      <div class="lab-player-metric-grid projection-grid">${[["PTS",projection.pts],["REB",projection.reb],["AST",projection.ast],["PRA",projection.pra],["MIN",projection.min]].map(([label,value])=>`<div><strong>${stat(value)}</strong><small>${label}</small></div>`).join("")}</div>
      <div class="lab-card-kicker">COMPARAR CON LÍNEA DE MERCADO</div>
      <div class="lab-player-lines">
        ${[["PTS",projection.pts],["REB",projection.reb],["AST",projection.ast],["PRA",projection.pra]].map(([label,value])=>`<label>${label}<input type="number" min="0" step="0.5" placeholder="Línea" data-player-line="${label.toLowerCase()}" ${value == null ? "disabled" : ""}></label>`).join("")}
      </div>
      <div id="labPlayerLineResults" class="lab-player-line-results"><p class="lab-card-foot">Ingresa una línea para comparar con la proyección. La diferencia no representa una probabilidad de acierto.</p></div>
      <div class="lab-card-kicker">ÚLTIMOS PARTIDOS</div>
      <div class="lab-player-log-wrap"><table class="lab-player-log"><thead><tr><th>Fecha</th><th>Rival</th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>PRA</th></tr></thead><tbody>${rows || '<tr><td colspan="7">Sin historial disponible</td></tr>'}</tbody></table></div>
      <p class="lab-card-foot">${safe(data.note || "Proyección estadística; puede cambiar con lesiones, minutos y alineaciones.")}</p>`;
    playerOutput.querySelectorAll("[data-player-line]").forEach(input => input.addEventListener("input", () => {
      const lines = [...playerOutput.querySelectorAll("[data-player-line]")].filter(item=>item.value.trim()!=="");
      $("labPlayerLineResults").innerHTML = lines.length ? lines.map(item=>{
        const key=item.dataset.playerLine, value=Number(projection[key]), line=Number(item.value), diff=value-line;
        return `<span>${key.toUpperCase()}: proyección <b>${stat(value)}</b> · línea <b>${fmt(line)}</b> · diferencia <b>${diff>=0?"+":""}${fmt(diff)}</b> (${diff>0?"por encima":diff<0?"por debajo":"igual"})</span>`;
      }).join("") : '<p class="lab-card-foot">Ingresa una línea para comparar con la proyección.</p>';
    }));
  };
  const loadPlayer = async (playerId) => {
    playerOutput.innerHTML = '<p class="muted">Recuperando estadísticas, historial y proyección del modelo…</p>';
    try {
      const response = await fetch(`/api/player-card?player_id=${encodeURIComponent(playerId)}`,{headers:{Accept:"application/json"}});
      const payload = unwrap(await response.json());
      if(!response.ok) throw new Error(payload.detail || payload.message || "No se pudo recuperar la ficha");
      renderPlayerCard(payload);
    } catch(error) {
      playerOutput.innerHTML = `<p class="muted">No se pudo cargar la Player Card: ${safe(error.message)}. Intenta actualizar o selecciona otro jugador.</p>`;
    }
  };
  const searchPlayers = async () => {
    const query = searchInput?.value.trim();
    if(!query || query.length<2) {
      searchResults.innerHTML='<p class="lab-card-foot">Escribe al menos dos letras para buscar.</p>';
      return;
    }
    searchButton.disabled=true; searchButton.textContent="Buscando…";
    searchResults.innerHTML='<p class="lab-card-foot">Buscando en la base de jugadores…</p>';
    try {
      const response=await fetch(`/api/players?season=2026-27&mode=pergame&sort=pra&limit=20&min_gp=1&q=${encodeURIComponent(query)}`,{headers:{Accept:"application/json"}});
      const payload=unwrap(await response.json());
      if(!response.ok) throw new Error(payload.detail || "Error al buscar jugadores");
      const players=payload.players || [];
      searchResults.innerHTML=players.length ? players.map(player=>`<button type="button" class="lab-card-search-result" data-player-id="${safe(player.player_id)}"><span>${safe(player.player_name)}</span><small>${safe(player.team)} · ${safe(player.gp)} PJ · ${stat(player.pts)} PTS · ${stat(player.pra)} PRA</small></button>`).join("") : '<p class="lab-card-foot">No se encontraron jugadores para esa búsqueda.</p>';
      searchResults.querySelectorAll("[data-player-id]").forEach(button=>button.addEventListener("click",()=>{
        searchInput.value=button.querySelector("span").textContent;
        searchResults.innerHTML="";
        loadPlayer(button.dataset.playerId);
      }));
    } catch(error) {
      searchResults.innerHTML=`<p class="lab-card-foot">${safe(error.message)}</p>`;
    } finally {searchButton.disabled=false;searchButton.textContent="Buscar";}
  };
  searchButton?.addEventListener("click",searchPlayers);
  searchInput?.addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();searchPlayers();}});
})();