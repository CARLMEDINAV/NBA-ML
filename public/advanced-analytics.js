(() => {
  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? '').replace(/[&<>\"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[char]));
  const n = (value, digits = 1) => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(digits).replace('.', ',');
  const pct = (value) => value == null ? '—' : Math.round(Number(value) * 100) + '%';
  const api = async (path, params = {}) => { const query = new URLSearchParams(params); const response = await fetch(path + '?' + query.toString(), {headers:{'Accept':'application/json'}}); const data = await response.json(); if (!response.ok) throw new Error(data.detail || 'No se pudo consultar la API.'); return data; };
  const selectedPlayer = () => $('#propsPlayer')?.value || '';
  async function loadDistribution() {
    const playerId = selectedPlayer(), target = $('#pointDistResult'); if (!target) return;
    if (!playerId) { target.textContent = 'Selecciona un jugador en el analizador de Props.'; return; }
    target.textContent = 'Calculando distribución histórica…';
    try {
      const data = await api('/api/player-prop-distribution', {player_id:playerId,line:Number($('#pointDistLine')?.value || 20.5),window:20});
      if (!data.available) { target.textContent = data.reason || 'No hay datos suficientes.'; return; }
      const rows = (data.distribution || []).map((item) => '<div class="advanced-bar-row"><span>' + n(item.line) + '</span><div class="advanced-bar-track"><i style="width:' + Math.round(item.over*100) + '%"></i></div><b>' + pct(item.over) + '</b></div>').join('');
      target.innerHTML = '<div class="advanced-grid"><div class="advanced-stat"><span>Media últimos ' + data.sample + '</span><strong>' + n(data.mean) + '</strong><small>puntos</small></div><div class="advanced-stat"><span>Mediana</span><strong>' + n(data.median) + '</strong><small>puntos</small></div><div class="advanced-stat"><span>Over ' + n(data.line) + '</span><strong>' + pct(data.over) + '</strong><small>' + data.sample + ' partidos</small></div><div class="advanced-stat"><span>Under ' + n(data.line) + '</span><strong>' + pct(data.under) + '</strong><small>frecuencia empírica</small></div></div><div class="advanced-bars">' + rows + '</div><p class="advanced-foot">' + esc(data.note) + ' Intervalo orientativo 80% del Over: ' + pct(data.over_interval_80?.[0]) + '–' + pct(data.over_interval_80?.[1]) + '.</p>';
    } catch (error) { target.textContent = error.message; }
  }
  async function loadMatchup() {
    const playerId = selectedPlayer(), target = $('#matchupResult'); if (!target) return;
    if (!playerId) { target.textContent = 'Selecciona un jugador en el analizador de Props.'; return; }
    target.textContent = 'Analizando rival y defensores…';
    try {
      const data = await api('/api/player-matchup', {player_id:playerId});
      if (!data.available) { target.textContent = data.reason || 'No hay información suficiente del matchup.'; return; }
      const h = data.h2h || {};
      const defenders = (data.direct_defenders || []).map((item) => '<tr><td>' + esc(item.defender) + '</td><td>' + item.games + '</td><td>' + n(item.minutes) + '</td><td>' + n(item.player_pts_per_game) + '</td><td>' + n(item.assists_per_game) + '</td><td>' + pct(item.fg_pct) + '</td></tr>').join('');
      target.innerHTML = '<div class="advanced-grid"><div class="advanced-stat"><span>Rival</span><strong>' + esc(data.opponent) + '</strong><small>' + esc(data.team) + ' · próximo rival</small></div><div class="advanced-stat"><span>H2H</span><strong>' + (h.games || 0) + '</strong><small>partidos registrados</small></div><div class="advanced-stat"><span>PTS vs rival</span><strong>' + n(h.pts) + '</strong><small>por partido</small></div><div class="advanced-stat"><span>REB / AST</span><strong>' + n(h.reb) + ' / ' + n(h.ast) + '</strong><small>por partido</small></div></div>' + (defenders ? '<div class="table-wrap"><table class="advanced-table"><thead><tr><th>Defensor</th><th>PJ</th><th>MIN matchup</th><th>PTS jugador</th><th>AST</th><th>FG%</th></tr></thead><tbody>' + defenders + '</tbody></table></div>' : '<p class="advanced-foot">No hay datos de defensores directos para este rival en el archivo de tracking.</p>') + '<p class="advanced-foot">' + esc(data.sample_warning || '') + ' ' + esc(data.note || '') + '</p>';
    } catch (error) { target.textContent = error.message; }
  }
  async function loadQuarterProjection() {
    const value = $('#quarterGame')?.value || '', target = $('#quarterResult'); if (!target) return;
    if (!value) { target.textContent = 'Selecciona un partido.'; return; }
    const parts = value.split('|'), home = parts[0], away = parts[1]; target.textContent = 'Consultando parciales recientes de ESPN…';
    try {
      const data = await api('/api/quarter-projection', {home:home,away:away,days:14});
      if (!data.available) { target.textContent = data.reason || 'No hay parciales suficientes.'; return; }
      const rows = (data.quarters || []).map((q) => '<tr><td><b>' + esc(q.quarter) + '</b></td><td>' + n(q.away) + '</td><td>' + n(q.home) + '</td><td><b>' + n(q.total) + '</b></td></tr>').join('');
      target.innerHTML = '<div class="advanced-grid"><div class="advanced-stat"><span>' + esc(data.away) + '</span><strong>' + n(data.away_total) + '</strong><small>puntos proyectados</small></div><div class="advanced-stat"><span>' + esc(data.home) + '</span><strong>' + n(data.home_total) + '</strong><small>puntos proyectados</small></div><div class="advanced-stat"><span>Total</span><strong>' + n(data.game_total) + '</strong><small>cuatro cuartos</small></div><div class="advanced-stat"><span>Muestra</span><strong>' + data.away_games + ' / ' + data.home_games + '</strong><small>partidos ESPN · visitante/local</small></div></div><div class="table-wrap"><table class="advanced-table"><thead><tr><th>Periodo</th><th>' + esc(data.away) + '</th><th>' + esc(data.home) + '</th><th>Total</th></tr></thead><tbody>' + rows + '</tbody></table></div><p class="advanced-foot">' + esc(data.source || '') + '. ' + esc(data.note || '') + '</p>';
    } catch (error) { target.textContent = error.message; }
  }
  async function loadQuarterGames() {
    const select = $('#quarterGame'); if (!select) return; select.innerHTML = '<option value="">Cargando partidos…</option>';
    try {
      const data = await api('/api/player-props', {days:5,limit:8}), games = data.games || [];
      select.innerHTML = '<option value="">Selecciona partido</option>';
      games.forEach((game) => { const option = document.createElement('option'); option.value = game.home + '|' + game.away; option.textContent = game.away + ' @ ' + game.home + ' · ' + game.date; select.append(option); });
      if (games.length) { select.selectedIndex = 1; await loadQuarterProjection(); } else $('#quarterResult').textContent = 'No hay partidos próximos en el rango seleccionado.';
    } catch (error) { $('#quarterResult').textContent = error.message; }
  }
  function init() {
    $('#pointDistRefresh')?.addEventListener('click', loadDistribution); $('#matchupRefresh')?.addEventListener('click', loadMatchup); $('#quarterRefresh')?.addEventListener('click', loadQuarterGames); $('#quarterGame')?.addEventListener('change', loadQuarterProjection); $('#pointDistLine')?.addEventListener('change', loadDistribution);
    $('#propsPlayer')?.addEventListener('change', () => { loadDistribution(); loadMatchup(); });
    $('#tab-btn-props')?.addEventListener('click', () => window.setTimeout(() => { if ($('#propsPlayer')?.value) { loadDistribution(); loadMatchup(); } loadQuarterGames(); }, 1200));
    const playerSelect = $('#propsPlayer'); if (playerSelect && window.MutationObserver) { const observer = new MutationObserver(() => { if (playerSelect.value) { loadDistribution(); loadMatchup(); } }); observer.observe(playerSelect, {childList:true}); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();