(() => {
  const $ = (s) => document.querySelector(s);
  const teams = ["ATL","BOS","BKN","CHA","CHI","CLE","DAL","DEN","DET","GSW","HOU","IND","LAC","LAL","MEM","MIA","MIL","MIN","NOP","NYK","OKC","ORL","PHI","PHX","POR","SAC","SAS","TOR","UTA","WAS"];
  const state = { home: [], away: [] };
  const teamName = (side) => side === "home" ? $("#liHome").value : $("#liAway").value;
  const fmt = (n) => Number(n || 0).toFixed(1);
  const safe = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  async function getPlayers(team) {
    const params = new URLSearchParams({season:"2026-27",mode:"pergame",team,limit:"30",sort:"pra",min_gp:"2"});
    const response = await fetch("/api/players?" + params);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "No se pudo cargar el roster");
    return data.players || [];
  }
  function normalize(p, index) {
    return {player_id:String(p.player_id ?? p.id ?? index),name:p.player_name || p.name || "Jugador",
      minutes:Number(p.min ?? p.minutes ?? 0),pts:Number(p.pts ?? 0),reb:Number(p.reb ?? 0),
      ast:Number(p.ast ?? 0),usage:Number(p.usage ?? p.usg_pct ?? 0),starter:index<5,out:false};
  }
  function renderTeam(side) {
    const host = $(side === "home" ? "#liHomePlayers" : "#liAwayPlayers");
    host.innerHTML = state[side].map((p,i) => `
      <div class="li-player">
        <label class="li-starter"><input type="checkbox" data-kind="starter" data-side="${side}" data-index="${i}" ${p.starter?"checked":""}> <span>Titular</span></label>
        <span class="li-player-main"><b>${safe(p.name)}</b><small>${fmt(p.minutes)} MIN · ${fmt(p.pts)} PTS · ${fmt(p.reb)} REB · ${fmt(p.ast)} AST</small></span>
        <label class="li-out"><input type="checkbox" data-kind="out" data-side="${side}" data-index="${i}" ${p.out?"checked":""} ${p.starter?"":"disabled"}> <span>Fuera</span></label>
      </div>`).join("");
    host.querySelectorAll("input").forEach(input => input.addEventListener("change", () => {
      const item = state[side][Number(input.dataset.index)];
      if (input.dataset.kind === "starter") {
        const count = state[side].filter(x=>x.starter).length;
        if (input.checked && count >= 5) { input.checked=false; return; }
        item.starter=input.checked; item.out=false;
      } else item.out=input.checked;
      renderTeam(side);
    }));
  }
  async function loadTeam(side) {
    const button = $(side === "home" ? "#liLoadHome" : "#liLoadAway");
    button.disabled=true; button.textContent="Cargando…";
    try {
      const rows=await getPlayers(teamName(side));
      state[side]=rows.slice(0,15).map(normalize);
      renderTeam(side);
    } catch(e) { $("#liNotice").textContent=e.message; $("#liNotice").hidden=false; }
    finally {button.disabled=false;button.textContent="Cargar roster";}
  }
  async function simulate() {
    const notice=$("#liNotice"); notice.hidden=true;
    for (const side of ["home","away"]) {
      if (state[side].filter(p=>p.starter).length!==5) throw new Error("Selecciona exactamente cinco titulares por equipo.");
    }
    const date=$("#liDate").value;
    const baseResponse=await fetch("/api/predict?"+new URLSearchParams({home:teamName("home"),away:teamName("away"),date}));
    const baseline=await baseResponse.json();
    if(!baseResponse.ok) throw new Error(baseline.detail || "No se pudo obtener la predicción base");
    const teamPayload=(side)=>({team:teamName(side),starters:state[side].filter(p=>p.starter),
      bench:state[side].filter(p=>!p.starter),removed_ids:state[side].filter(p=>p.starter&&p.out).map(p=>p.player_id)});
    const payload={home:teamPayload("home"),away:teamPayload("away"),
      baseline:{home_score:baseline.projected_home_score,away_score:baseline.projected_away_score,total:baseline.predicted_total}};
    const response=await fetch("/api/lineup-impact/simulate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await response.json(); if(!response.ok) throw new Error(data.detail || "Error al simular");
    renderResult(data);
  }
  function renderResult(d) {
    $("#liResult").innerHTML=`
      <div class="li-result-head"><div><p class="eyebrow">COMPARACIÓN DE ESCENARIOS</p><h3>${safe(teamName("away"))} @ ${safe(teamName("home"))}</h3></div><span class="li-delta">${d.changes.total>=0?"+":""}${fmt(d.changes.total)} PTS</span></div>
      <div class="li-score-grid">
        <article><small>BASELINE RIDGE</small><strong>${fmt(d.baseline.total)}</strong><span>${fmt(d.baseline.away_score)} – ${fmt(d.baseline.home_score)}</span></article>
        <article class="is-scenario"><small>CON AUSENCIAS</small><strong>${fmt(d.scenario.total)}</strong><span>${fmt(d.scenario.away_score)} – ${fmt(d.scenario.home_score)}</span></article>
      </div>
      <div class="li-team-impact">
        <article><h4>${safe(d.teams.home.team)} · impacto</h4><b>${d.teams.home.points_delta>=0?"+":""}${fmt(d.teams.home.points_delta)} pts</b><p>${safe(d.teams.home.removed.join(", ")||"Sin bajas seleccionadas")}</p><small>Reemplazos: ${safe(d.teams.home.replacements.map(x=>x.in).join(", ")||"—")}</small></article>
        <article><h4>${safe(d.teams.away.team)} · impacto</h4><b>${d.teams.away.points_delta>=0?"+":""}${fmt(d.teams.away.points_delta)} pts</b><p>${safe(d.teams.away.removed.join(", ")||"Sin bajas seleccionadas")}</p><small>Reemplazos: ${safe(d.teams.away.replacements.map(x=>x.in).join(", ")||"—")}</small></article>
      </div><p class="li-method">${safe(d.methodology)}</p>`;
  }
  function init() {
    const options=teams.map(t=>`<option value="${t}">${t}</option>`).join("");
    $("#liHome").innerHTML=options;$("#liAway").innerHTML=options;
    $("#liHome").value="BOS";$("#liAway").value="NYK";
    const now=new Date();$("#liDate").value=new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,10);
    $("#liLoadHome").addEventListener("click",()=>loadTeam("home"));
    $("#liLoadAway").addEventListener("click",()=>loadTeam("away"));
    $("#liRun").addEventListener("click",()=>simulate().catch(e=>{ $("#liNotice").textContent=e.message;$("#liNotice").hidden=false; }));
    loadTeam("home");loadTeam("away");
  }
  document.addEventListener("DOMContentLoaded",()=>{if($("#lineupImpactView"))init();});
})();