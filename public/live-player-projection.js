(() => {
  "use strict";
  const root = document.querySelector("#livePlayerProjection");
  if (!root) return;
  const statusNode = document.querySelector("#livePlayerStatus");
  const body = document.querySelector("#livePlayerProjectionBody");
  let playerId = null;
  let timer = null;
  let request = 0;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const n = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(1) : "—";
  const stat = (label, value) => '<div class="live-player-stat"><small>'+label+'</small><strong>'+n(value)+'</strong></div>';
  function empty(message, warning=false) {
    statusNode.textContent = warning ? "SIN DATOS" : "SIN PARTIDO EN VIVO";
    statusNode.className = "live-player-status" + (warning ? " is-warning" : "");
    body.innerHTML = '<p class="muted">'+esc(message)+'</p>';
  }
  function render(data) {
    if (!data.available) { empty(data.message || data.warning || "No hay estadísticas live disponibles.", !!data.warning); return; }
    const g = data.game || {}, c = data.current || {}, m = data.minutes || {}, s = data.scenarios || {};
    statusNode.textContent = "EN VIVO · "+(g.status || ("Q"+g.period+" · "+g.clock));
    statusNode.className = "live-player-status is-live";
    body.innerHTML =
      '<div class="live-player-game"><div>'+esc(g.away)+' <small>VISITA</small></div><strong>'+esc(g.away_score)+' – '+esc(g.home_score)+'</strong><div>'+esc(g.home)+' <small>LOCAL</small></div></div>'+
      '<div class="live-player-current">'+stat("PTS",c.pts)+stat("REB",c.reb)+stat("AST",c.ast)+stat("MIN",c.min)+'</div>'+
      '<div class="live-player-meta"><span>3PM '+n(c.fg3m)+'</span><span>STL '+n(c.stl)+'</span><span>BLK '+n(c.blk)+'</span><span>TOV '+n(c.tov)+'</span><span>Tiempo de juego restante '+n(m.game_remaining)+' min</span></div>'+
      '<p class="eyebrow" style="margin:14px 0 6px">PROYECCIÓN FINAL POR ESCENARIO</p>'+
      '<div class="live-player-scenarios">'+
      '<div class="live-player-scenario"><small>CONSERVADOR</small><b>+'+n(m.remaining_low)+' MIN</b><strong>'+n(s.conservative?.pts)+' PTS</strong><small>'+n(s.conservative?.reb)+' REB · '+n(s.conservative?.ast)+' AST</small><small>'+n(s.conservative?.pra)+' PRA</small></div>'+
      '<div class="live-player-scenario is-expected"><small>ESPERADO</small><b>+'+n(m.remaining_expected)+' MIN</b><strong>'+n(s.expected?.pts)+' PTS</strong><small>'+n(s.expected?.reb)+' REB · '+n(s.expected?.ast)+' AST</small><small>'+n(s.expected?.pra)+' PRA</small></div>'+
      '<div class="live-player-scenario"><small>OPTIMISTA</small><b>+'+n(m.remaining_high)+' MIN</b><strong>'+n(s.optimistic?.pts)+' PTS</strong><small>'+n(s.optimistic?.reb)+' REB · '+n(s.optimistic?.ast)+' AST</small><small>'+n(s.optimistic?.pra)+' PRA</small></div></div>'+
      '<div class="live-player-meta"><span>Minutos objetivo: '+n(m.target)+'</span><span>Cancha/banco: '+esc(m.court_status)+'</span></div>'+
      '<p class="live-player-note">'+esc(m.rotation_note)+' '+esc(data.warning || "")+' Actualización: '+esc(new Date(data.updated_at).toLocaleTimeString("es-CL"))+'. '+esc(data.warning || "")+'</p>';
  }
  async function refresh() {
    if (!playerId || document.querySelector("#playerDetail")?.hidden) return;
    const id = ++request;
    try {
      const response = await fetch("/api/live/player-stats?player_id="+encodeURIComponent(playerId), {headers:{Accept:"application/json"},cache:"no-store"});
      if (!response.ok) throw new Error("HTTP "+response.status);
      const data = await response.json();
      if (id !== request || document.querySelector("#playerDetail")?.hidden) return;
      render(data);
    } catch (error) { if (id === request) empty("No se pudo actualizar la proyección live: "+error.message, true); }
  }
  window.addEventListener("nba:player-card-open", event => {
    playerId = Number(event.detail?.playerId) || null;
    request++;
    if (timer) clearInterval(timer);
    empty("Consultando boxscore y rotación estimada…");
    refresh();
    timer = setInterval(refresh, 15000);
  });
  document.querySelector("#pdClose")?.addEventListener("click", () => { request++; playerId=null; if(timer) clearInterval(timer); timer=null; });
  document.querySelector("#playerDetail")?.addEventListener("click", event => {
    if (event.target.id === "playerDetail") { request++; playerId=null; if(timer) clearInterval(timer); timer=null; }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
})();