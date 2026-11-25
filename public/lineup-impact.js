(() => {
  const $ = (s) => document.querySelector(s);
  const teams = ["ATL","BOS","BKN","CHA","CHI","CLE","DAL","DEN","DET","GSW","HOU","IND","LAC","LAL","MEM","MIA","MIL","MIN","NOP","NYK","OKC","ORL","PHI","PHX","POR","SAC","SAS","TOR","UTA","WAS"];
  const state = { home: [], away: [] };
  const teamName = (side) => side === "home" ? $("#liHome").value : $("#liAway").value;
  const fmt = (n) => Number(n || 0).toFixed(1);
  const safe = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const statusLabel = (status) => ({OUT:"OUT",QUESTIONABLE:"QUESTIONABLE",DOUBTFUL:"DOUBTFUL",PROBABLE:"PROBABLE",ACTIVE:"SIN BAJA REPORTADA",UNKNOWN:"SIN REPORTE","DAY-TO-DAY":"DÍA A DÍA"}[status] || status);
  const statusClass = (status) => status === "OUT" ? "out" : ["QUESTIONABLE","DOUBTFUL","DAY-TO-DAY"].includes(status) ? "doubtful" : status === "PROBABLE" ? "probable" : status === "ACTIVE" ? "active" : "unknown";
  const statusOptions = ["ACTIVE","PROBABLE","QUESTIONABLE","DOUBTFUL","DAY-TO-DAY","OUT","UNKNOWN"];
  const nameKey = (value) => String(value || "").normalize("NFKD").replace(/[\u0300-\u036f]/g,"").toLowerCase().replace(/[^a-z0-9]+/g," ").trim();

  async function getPlayers(team) {
    const params = new URLSearchParams({season:"2026-27",mode:"pergame",team,limit:"30",sort:"pra",min_gp:"2"});
    const response = await fetch("/api/players?" + params);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "No se pudo cargar el roster");
    return data.players || [];
  }
  async function getInjuries(team) {
    const response = await fetch("/api/injuries?" + new URLSearchParams({team}));
    if (!response.ok) throw new Error("No se pudo consultar el parte de ESPN");
    const data = await response.json();
    const row = (data.teams || []).find(item => String(item.team).toUpperCase() === team);
    return { injuries: row?.injuries || [], warning: data.warning || null, updated: data.source_updated_at || null };
  }
  function normalize(p, index, injuryMap) {
    const id = String(p.player_id ?? p.id ?? index);
    const name = p.player_name || p.name || "Jugador";
    const injury = injuryMap.byId.get(id) || injuryMap.byName.get(nameKey(name));
    const status = injury ? String(injury.status || "UNKNOWN").toUpperCase() : (injuryMap.unknownFallback ? "UNKNOWN" : "ACTIVE");
    return {
      player_id:id,name,minutes:Number(p.min ?? p.minutes ?? 0),pts:Number(p.pts ?? 0),
      reb:Number(p.reb ?? 0),ast:Number(p.ast ?? 0),usage:Number(p.usage ?? p.usg_pct ?? 0),
      position:String(injury?.position || p.position || ""),headshot:p.player_id ? "https://cdn.nba.com/headshots/nba/latest/1040x760/"+p.player_id+".png" : "",
      starter:index<5,out:status==="OUT",availabilityStatus:status,
      availabilitySource:injury ? "ESPN" : "Sin reporte ESPN"
    };
  }
  function renderCourt(side) {
    const host = $(side === "home" ? "#liHomeCourt" : "#liAwayCourt");
    const starters = state[side].filter(p => p.starter);
    const slots = ["li-slot-pg","li-slot-sg","li-slot-sf","li-slot-pf","li-slot-c"];
    host.innerHTML = starters.map((p,i) => {
      const initials = safe(p.name.split(/\s+/).map(x=>x[0]).slice(0,2).join("").toUpperCase());
      return '<article class="li-court-player '+slots[i]+'"><div class="li-court-photo-wrap">' +
        (p.headshot ? '<img class="li-court-photo" src="'+safe(p.headshot)+'" alt="Foto de '+safe(p.name)+'" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'grid\'">' : '') +
        '<span class="li-photo-fallback">'+initials+'</span>' + (p.out || p.availabilityStatus==="OUT" ? '<span class="li-out-stamp">OUT</span>' : '') +
        '</div><b>'+safe(p.name)+'</b><small>'+["PG","SG","SF","PF","C"][i]+'</small></article>';
    }).join("");
    if (!starters.length) host.innerHTML = '<p class="li-court-empty">Selecciona cinco titulares para visualizar el quinteto.</p>';
  }
  function renderTeam(side) {
    const host = $(side === "home" ? "#liHomePlayers" : "#liAwayPlayers");
    renderCourt(side);
    host.innerHTML = state[side].map((p,i) => {
      const options = statusOptions.map(status => '<option value="'+status+'" '+(p.availabilityStatus===status?"selected":"")+'>'+statusLabel(status)+'</option>').join("");
      return `
      <div class="li-player">
        <label class="li-starter"><input type="checkbox" data-kind="starter" data-side="${side}" data-index="${i}" ${p.starter?"checked":""}> <span>Titular</span></label>
        <span class="li-player-main"><b>${safe(p.name)}</b><small>${fmt(p.minutes)} MIN · ${fmt(p.pts)} PTS · ${fmt(p.reb)} REB · ${fmt(p.ast)} AST</small>
          <small class="li-status li-status-${statusClass(p.availabilityStatus)}">${statusLabel(p.availabilityStatus)} · ${safe(p.availabilitySource)}</small>
        </span>
        <label class="li-status-select" title="Estado de disponibilidad"><select data-kind="status" data-side="${side}" data-index="${i}" aria-label="Disponibilidad de ${safe(p.name)}">${options}</select></label>
        <label class="li-out"><input type="checkbox" data-kind="out" data-side="${side}" data-index="${i}" ${p.out?"checked":""} ${p.starter?"":"disabled"} ${p.availabilityStatus==="OUT"?"disabled":""}> <span>Simular fuera</span></label>
      </div>`;
    }).join("");
    host.querySelectorAll("input,select").forEach(input => input.addEventListener("change", () => {
      const item = state[side][Number(input.dataset.index)];
      if (input.dataset.kind === "starter") {
        const count = state[side].filter(x=>x.starter).length;
        if (input.checked && count >= 5) { input.checked=false; return; }
        item.starter=input.checked;
        if (!item.starter && item.availabilityStatus!=="OUT") item.out=false;
      } else if (input.dataset.kind === "status") {
        item.availabilityStatus=input.value;
        item.availabilitySource="Ajuste manual";
        item.out=input.value==="OUT";
      } else item.out=input.checked;
      renderTeam(side);
    }));
  }
  async function loadTeam(side) {
    const button = $(side === "home" ? "#liLoadHome" : "#liLoadAway");
    button.disabled=true; button.textContent="Cargando…";
    try {
      const team=teamName(side);
      const [rows, injuryData]=await Promise.all([getPlayers(team),getInjuries(team).catch(error=>({injuries:[],warning:error.message,updated:null}))]);
      const byId=new Map(),byName=new Map();
      injuryData.injuries.forEach(injury=>{
        if(injury.player_id!=null)byId.set(String(injury.player_id),injury);
        byName.set(nameKey(injury.player_name),injury);
      });
      state[side]=rows.slice(0,15).map((p,i)=>normalize(p,i,{byId,byName,unknownFallback:Boolean(injuryData.warning)}));
      renderTeam(side);
      const notice=$("#liNotice");
      notice.hidden=false;
      notice.textContent=injuryData.warning
        ? injuryData.warning+" Sin un parte válido, los jugadores quedan como SIN REPORTE y no se asignan automáticamente como reemplazos."
        : "Disponibilidad consultada en ESPN"+(injuryData.updated?" · actualizado "+injuryData.updated:"")+". OUT se excluye automáticamente. QUESTIONABLE, DOUBTFUL y DÍA A DÍA no se asignan como reemplazos sin confirmación manual.";
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
    const teamPayload=(side)=>({team:teamName(side),starters:state[side].filter(p=>p.starter).map(payloadPlayer),
      bench:state[side].filter(p=>!p.starter).map(payloadPlayer),
      removed_ids:state[side].filter(p=>p.starter&&(p.out||p.availabilityStatus==="OUT")).map(p=>p.player_id)});
    const payloadPlayer=(p)=>({player_id:p.player_id,name:p.name,minutes:p.minutes,pts:p.pts,reb:p.reb,ast:p.ast,usage:p.usage,position:p.position,availability_status:p.availabilityStatus});
    const payload={home:teamPayload("home"),away:teamPayload("away"),
      baseline:{home_score:baseline.projected_home_score,away_score:baseline.projected_away_score,total:baseline.predicted_total}};
    const response=await fetch("/api/lineup-impact/simulate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await response.json(); if(!response.ok) throw new Error(data.detail || "Error al simular");
    renderResult(data);
  }
  function renderResult(d) {
    const renderTeamImpact=(team)=>`
      <article><h4>${safe(team.team)} · impacto</h4><b>${team.points_delta>=0?"+":""}${fmt(team.points_delta)} pts</b>
        <p>${safe(team.removed.join(", ")||"Sin bajas seleccionadas")}</p>
        <small>Reemplazos: ${safe(team.replacements.map(x=>x.in+" ("+x.minutes_covered+" min)").join(", ")||"—")}</small>
        <small>${team.excluded_bench?.length ? "Suplentes OUT excluidos: "+safe(team.excluded_bench.join(", ")) : "Sin suplentes OUT en el cálculo"}</small>
      </article>`;
    $("#liResult").innerHTML=`
      <div class="li-result-head"><div><p class="eyebrow">COMPARACIÓN DE ESCENARIOS</p><h3>${safe(teamName("away"))} @ ${safe(teamName("home"))}</h3></div><span class="li-delta">${d.changes.total>=0?"+":""}${fmt(d.changes.total)} PTS</span></div>
      <div class="li-score-grid">
        <article><small>BASELINE RIDGE</small><strong>${fmt(d.baseline.total)}</strong><span>${fmt(d.baseline.away_score)} – ${fmt(d.baseline.home_score)}</span></article>
        <article class="is-scenario"><small>CON AUSENCIAS</small><strong>${fmt(d.scenario.total)}</strong><span>${fmt(d.scenario.away_score)} – ${fmt(d.scenario.home_score)}</span></article>
      </div>
      <div class="li-team-impact">${renderTeamImpact(d.teams.home)}${renderTeamImpact(d.teams.away)}</div>
      <p class="li-method">${safe(d.methodology)}</p>`;
  }
  function init() {
    const options=teams.map(t=>'<option value="'+t+'">'+t+'</option>').join("");
    $("#liHome").innerHTML=options;$("#liAway").innerHTML=options;
    $("#liHome").value="BOS";$("#liAway").value="NYK";
    const now=new Date();$("#liDate").value=new Date(now.getTime()-now.getTimezoneOffset()*60000).toISOString().slice(0,10);
    $("#liHome").addEventListener("change",()=>loadTeam("home"));
    $("#liAway").addEventListener("change",()=>loadTeam("away"));
    $("#liLoadHome").addEventListener("click",()=>loadTeam("home"));
    $("#liLoadAway").addEventListener("click",()=>loadTeam("away"));
    $("#liRun").addEventListener("click",()=>simulate().catch(e=>{$("#liNotice").textContent=e.message;$("#liNotice").hidden=false;}));
    loadTeam("home");loadTeam("away");
  }
  document.addEventListener("DOMContentLoaded",()=>{if($("#lineupImpactView"))init();});
})();
