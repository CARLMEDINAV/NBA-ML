(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const FAV_KEY = "nbaTotals.playerAlertFavorites.v1";
  const SEEN_KEY = "nbaTotals.playerAlertSeen.v1";
  const FEED_KEY = "nbaTotals.playerAlertFeed.v1";
  const favorites = new Map();
  const seen = new Set();
  let feed = [];
  let pollTimer = null;
  let initialized = false;
  let searchTimer = null;
  const activeGames = new Set();

  try { for (const p of JSON.parse(localStorage.getItem(FAV_KEY) || "[]")) favorites.set(String(p.player_id), p); } catch {}
  try { for (const id of JSON.parse(sessionStorage.getItem(SEEN_KEY) || "[]")) seen.add(String(id)); } catch {}
  try { feed = JSON.parse(localStorage.getItem(FEED_KEY) || "[]").slice(0, 30); } catch {}

  function save() {
    localStorage.setItem(FAV_KEY, JSON.stringify([...favorites.values()]));
    localStorage.setItem(FEED_KEY, JSON.stringify(feed.slice(0, 30)));
    sessionStorage.setItem(SEEN_KEY, JSON.stringify([...seen].slice(-500)));
  }
  function escapeHtml(v) { return String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
  function renderFavorites() {
    $("#playerAlertsFavoriteCount").textContent = favorites.size;
    $("#playerAlertsFavorites").innerHTML = favorites.size ? [...favorites.values()].map(p => playerRow(p, true)).join("") : '<p class="muted">Aún no agregas jugadores favoritos.</p>';
    $("#playerAlertsCount").hidden = feed.length === 0;
    $("#playerAlertsCount").textContent = String(feed.length);
    $("#playerAlertsFeed").innerHTML = feed.length ? feed.slice(0,15).map(item => '<div class="player-alert-item"><img src="'+escapeHtml(item.headshot || '')+'" alt="" onerror="this.style.visibility=\'hidden\'"><div><strong>'+escapeHtml(item.player_name)+' · +'+escapeHtml(item.points)+' puntos</strong><small>'+escapeHtml(item.game)+' · '+escapeHtml(item.clock || 'En vivo')+' · Total: '+escapeHtml(item.total)+' pts</small></div><b>'+escapeHtml(item.total)+'</b></div>').join("") : '<p class="muted">Todavía no hay anotaciones detectadas.</p>';
  }
  function playerRow(p, remove) {
    return '<div class="player-alert-player"><div><strong>'+escapeHtml(p.player_name)+'</strong><small>'+escapeHtml(p.team || 'NBA')+'</small></div><button type="button" data-player-action="'+(remove?'remove':'add')+'" data-player-id="'+escapeHtml(p.player_id)+'">'+(remove?'Quitar':'＋ Favorito')+'</button></div>';
  }
  function openDialog() { $("#playerAlertsBackdrop").hidden = false; $("#playerAlertsSearch").focus(); renderFavorites(); }
  function closeDialog() { $("#playerAlertsBackdrop").hidden = true; }
  async function searchPlayers(q) {
    if (!q || q.trim().length < 2) { $("#playerAlertsResults").innerHTML = '<p class="muted">Escribe al menos 2 letras para buscar.</p>'; return; }
    try {
      const u = new URL("/api/players", location.origin);
      u.searchParams.set("season", "2026-27"); u.searchParams.set("mode", "pergame"); u.searchParams.set("limit", "12"); u.searchParams.set("min_gp", "1"); u.searchParams.set("q", q.trim());
      const r = await fetch(u, {headers:{Accept:"application/json"},cache:"no-store"});
      if (!r.ok) throw new Error("No se pudo buscar jugadores");
      const data = await r.json();
      $("#playerAlertsResults").innerHTML = (data.players || []).filter(p => !favorites.has(String(p.player_id))).map(p => playerRow(p, false)).join("") || '<p class="muted">No se encontraron jugadores nuevos.</p>';
    } catch(e) { $("#playerAlertsResults").textContent = e.message; }
  }
  async function getJson(path) {
    const r = await fetch(path,{headers:{Accept:"application/json"},cache:"no-store"});
    if(!r.ok) throw new Error("HTTP "+r.status);
    return r.json();
  }
  function scorerFor(play) {
    const participants = play.participants || [];
    const scorer = participants.find(p => /scor|shooter|made/i.test(String(p.role || ""))) || participants[0];
    return scorer || null;
  }
  async function pollGame(game) {
    const data = await getJson("/api/live/plays?event_id="+encodeURIComponent(game.event_id));
    const plays = data.plays || [];
    const cumulative = new Map();
    const favoritePlays = [];
    for (const play of plays) {
      const scorer = scorerFor(play);
      if (!scorer) continue;
      const pid = String(scorer.player_id);
      const points = Math.max(0, Number(play.score_value) || 0);
      if (!points) continue;
      cumulative.set(pid, (cumulative.get(pid) || 0) + points);
      const key = String(game.event_id)+":"+String(play.id || "");
      if (!play.id) continue;
      const isNew = !seen.has(key);
      seen.add(key);
      if (favorites.has(pid) && isNew) favoritePlays.push({key,play,scorer,pid,points});
    }
    for (const item of favoritePlays) {
      seen.add(item.key);
      const favorite = favorites.get(item.pid);
      const total = cumulative.get(item.pid) || item.points;
      const notice = {
        player_name: favorite.player_name || item.scorer.player_name,
        points: item.points,
        total,
        game: game.name,
        clock: item.play.clock || game.status,
        headshot: "https://cdn.nba.com/headshots/nba/latest/260x190/"+item.pid+".png",
        created_at: Date.now()
      };
      feed.unshift(notice); feed = feed.slice(0,30);
      renderFavorites();
      if ("Notification" in window && Notification.permission === "granted") {
        try { new Notification(notice.player_name+" anotó "+notice.points+" puntos", {body:"Lleva "+notice.total+" puntos · "+notice.game, icon:notice.headshot, tag:item.key}); } catch {}
      }
    }
    save();
  }
  async function poll() {
    if (document.hidden || !favorites.size) return;
    try {
      const data = await getJson("/api/live/games");
      const games = data.games || [];
      const current = new Set(games.map(g=>g.event_id));
      for (const id of [...activeGames]) if (!current.has(id)) activeGames.delete(id);
      for (const game of games) {
        activeGames.add(game.event_id);
        try { await pollGame(game); } catch {}
      }
    } catch {}
  }
  function start() {
    if (pollTimer) clearInterval(pollTimer);
    poll();
    pollTimer = setInterval(poll, 12000);
  }
  function bind() {
    $("#playerAlertsOpen")?.addEventListener("click",openDialog);
    $("#playerAlertsClose")?.addEventListener("click",closeDialog);
    $("#playerAlertsBackdrop")?.addEventListener("click",e=>{if(e.target.id==="playerAlertsBackdrop")closeDialog();});
    $("#playerAlertsSearch")?.addEventListener("input",e=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>searchPlayers(e.target.value),250);});
    $("#playerAlertsResults")?.addEventListener("click",e=>{
      const b=e.target.closest("button[data-player-action]"); if(!b)return;
      const row=b.closest(".player-alert-player");
      const player={player_id:b.dataset.playerId,player_name:row.querySelector("strong").textContent,team:row.querySelector("small").textContent};
      favorites.set(String(player.player_id),player); save(); renderFavorites(); searchPlayers($("#playerAlertsSearch").value); start();
    });
    $("#playerAlertsFavorites")?.addEventListener("click",e=>{
      const b=e.target.closest("button[data-player-action='remove']"); if(!b)return;
      favorites.delete(String(b.dataset.playerId)); save(); renderFavorites(); start();
    });
    $("#playerAlertsPermission")?.addEventListener("click",async()=>{
      if(!("Notification" in window)){alert("Este navegador no admite notificaciones.");return;}
      const permission=await Notification.requestPermission();
      $("#playerAlertsPermission").textContent=permission==="granted"?"Avisos activados":permission==="denied"?"Permiso bloqueado":"Activar avisos";
    });
    document.addEventListener("visibilitychange",()=>{if(!document.hidden)poll();});
    window.addEventListener("focus",poll);
    renderFavorites();
    if(favorites.size)start();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind, { once: true });
  else bind();
})();