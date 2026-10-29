(function () {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const nf1 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 });
  const nf2 = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2 });

  const state = {
    loaded: false,
    loading: false,
    data: null,
    requestId: 0,
    filters: {
      team: "",
      status: "",
      query: "",
      onlyAffected: true,
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
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    return nf1.format(Number(value));
  }

  function signed(value, digits = 1) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    const number = Number(value);
    return `${number >= 0 ? "+" : ""}${num(number, digits)}`;
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

  function searchKey(value) {
    return String(value ?? "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function dateLabel(value) {
    if (!value) return "—";
    const date = new Date(`${value}T12:00:00`);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleDateString("es-ES", { day: "2-digit", month: "short" });
  }

  function toast(message) {
    if (typeof window.showToast === "function") {
      window.showToast(message);
      return;
    }
    const element = $("#toast");
    if (!element) return;
    element.textContent = message;
    element.hidden = false;
    window.clearTimeout(toast.timer);
    toast.timer = window.setTimeout(() => {
      element.hidden = true;
    }, 4200);
  }

  function setNotice(element, message, isError = false) {
    if (!element) return;
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

  function summaryCard(label, value, sub) {
    return `<div class="summary-card">
      <span class="k">${esc(label)}</span>
      <span class="v">${esc(value)}</span>
      <span class="s">${esc(sub)}</span>
    </div>`;
  }

  function statusClass(value) {
    const key = String(value || "").toLowerCase().replaceAll("_", "-");
    if (key === "out") return "out";
    if (key === "day") return "day";
    if (key === "doubtful" || key === "questionable") return "doubtful";
    if (key === "probable") return "probable";
    return "other";
  }

  function statusPriority(injury) {
    const order = { out: 0, doubtful: 1, questionable: 2, day: 3, probable: 4 };
    return order[injury.status_class] ?? 5;
  }

  function counts(injuries) {
    const result = { out: 0, day: 0, doubtful: 0, questionable: 0, probable: 0 };
    for (const injury of injuries) {
      if (injury.status_class === "out") result.out += 1;
      else if (injury.status_class === "day") result.day += 1;
      else if (injury.status_class === "probable") result.probable += 1;
      else if (injury.status_class === "doubtful") {
        if (String(injury.status || "").toLowerCase() === "questionable") {
          result.questionable += 1;
        } else {
          result.doubtful += 1;
        }
      }
    }
    return result;
  }

  function impactSummary(injuries) {
    if (!injuries.length) return "Sin bajas reportadas por ESPN.";
    const total = counts(injuries);
    const parts = [];
    if (total.out) parts.push(`${total.out} ${total.out === 1 ? "baja" : "bajas"}`);
    if (total.day) parts.push(`${total.day} día a día`);
    if (total.doubtful) parts.push(`${total.doubtful} duda${total.doubtful === 1 ? "" : "s"} alta`);
    if (total.questionable) parts.push(`${total.questionable} en duda`);
    if (total.probable) parts.push(`${total.probable} probable${total.probable === 1 ? "" : "s"}`);
    const principal = [...injuries].sort(
      (left, right) =>
        statusPriority(left) - statusPriority(right) ||
        (Number(right.stats?.pra) || 0) - (Number(left.stats?.pra) || 0),
    )[0];
    const detail =
      principal.stats?.pra !== null && principal.stats?.pra !== undefined
        ? `${principal.player_name} (${num(principal.stats.pra)} PRA)`
        : principal.player_name;
    parts.push(`Principal: ${detail}`);
    return parts.join(" · ");
  }

  function statusPill(injury) {
    return `<span class="injury-status is-${esc(statusClass(injury.status_class))}">${esc(injury.status_label)}</span>`;
  }

  function statChip(label, value) {
    return `<span class="injury-stat"><b>${num(value)}</b><small>${esc(label)}</small></span>`;
  }

  function headshot(injury) {
    if (injury.headshot_url) {
      return `<span class="injury-avatar">
        <span class="injury-initials" aria-hidden="true">${esc(initials(injury.player_name))}</span>
        <img class="injury-headshot" src="${esc(injury.headshot_url)}" alt="" loading="lazy"
          decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">
      </span>`;
    }
    return `<span class="injury-avatar"><span class="injury-initials" aria-hidden="true">${esc(initials(injury.player_name))}</span></span>`;
  }

  function renderInjury(injury) {
    const playerName = injury.player_id
      ? `<button class="injury-player-name" type="button" data-player-id="${esc(injury.player_id)}">${esc(injury.player_name)}</button>`
      : `<span class="injury-player-name">${esc(injury.player_name)}</span>`;
    const keyTag = injury.is_key_player ? '<span class="injury-key">Jugador clave</span>' : "";
    const comment = injury.short_comment
      ? `<details class="injury-comment">
          <summary>Nota ESPN</summary>
          <p>${esc(injury.short_comment)}</p>
        </details>`
      : "";
    return `<div class="injury-row">
      <div class="injury-player">
        ${headshot(injury)}
        <div class="injury-player-copy">
          <div class="injury-player-line">${playerName}${keyTag}</div>
          <span class="injury-player-meta">${esc(injury.position || "—")} · ${esc(injury.role_note || "Sin rol calculado")}</span>
        </div>
      </div>
      <div class="injury-state">
        ${statusPill(injury)}
        <small>${injury.date ? `Actualizado ${esc(dateLabel(injury.date))}` : "Sin fecha"}</small>
      </div>
      <div class="injury-stats">
        ${statChip("PTS", injury.stats?.pts)}
        ${statChip("REB", injury.stats?.reb)}
        ${statChip("AST", injury.stats?.ast)}
        ${statChip("PRA", injury.stats?.pra)}
      </div>
      ${comment}
    </div>`;
  }

  function renderTeam(team) {
    const logo = team.team_logo_url
      ? `<img src="${esc(team.team_logo_url)}" alt="" loading="lazy" decoding="async"
          referrerpolicy="no-referrer" onerror="this.parentElement.classList.add('is-fallback'); this.remove()">`
      : "";
    const logoClass = team.team_logo_url ? "" : " is-fallback";
    const countLabel =
      team.injury_count === 1 ? "1 en el parte" : `${team.injury_count} en el parte`;
    const availability = team.availability;
    const adjustment =
      availability && Number(availability.missing_share) > 0
        ? `<span class="injury-adjust" title="Impacto estimado en el margen, no en el total">Ajuste de margen ${signed(availability.impact_points)} pts</span>`
        : "";
    return `<article class="injury-team">
      <header class="injury-team-head">
        <div class="injury-team-brand">
          <span class="injury-team-logo${logoClass}">${logo}<span class="injury-team-fallback">${esc(team.team)}</span></span>
          <div>
            <h3>${esc(team.team_name)}</h3>
            <span class="injury-team-sub">${esc(team.team)} · ${team.injury_count === 0 ? "sin bajas reportadas" : `${team.out_count} out · ${team.day_to_day_count} día a día`}</span>
          </div>
        </div>
        <span class="injury-team-count${team.out_count ? " is-out" : ""}">${esc(countLabel)}</span>
      </header>
      <p class="injury-impact">${esc(team.impact_summary || impactSummary(team.injuries || []))} ${adjustment}</p>
      <div class="injury-rows">
        ${(team.injuries || []).map(renderInjury).join("") || '<div class="injury-empty-row">ESPN no reporta jugadores lesionados para este equipo.</div>'}
      </div>
    </article>`;
  }

  function filteredTeams() {
    const data = state.data;
    if (!data) return [];
    if (data.warning && !Number(data.count)) return [];
    const teamFilter = state.filters.team;
    const statusFilter = state.filters.status.toLowerCase();
    const query = searchKey(state.filters.query);
    const onlyAffected = state.filters.onlyAffected && !teamFilter;

    return (data.teams || [])
      .filter((team) => !teamFilter || team.team === teamFilter)
      .map((team) => {
        const injuries = (team.injuries || []).filter((injury) => {
          if (statusFilter) {
            const raw = String(injury.status || "").toLowerCase();
            const label = String(injury.status_label || "").toLowerCase();
            if (!raw.includes(statusFilter) && !label.includes(statusFilter)) return false;
          }
          if (query) {
            const haystack = searchKey(
              [
                team.team,
                team.team_name,
                injury.player_name,
                injury.status,
                injury.status_label,
                injury.short_comment,
              ]
                .filter(Boolean)
                .join(" "),
            );
            if (!haystack.includes(query)) return false;
          }
          return true;
        });
        const total = counts(injuries);
        return {
          ...team,
          injuries,
          injury_count: injuries.length,
          out_count: total.out,
          day_to_day_count: total.day,
          impact_summary: impactSummary(injuries),
        };
      })
      .filter((team) => {
        if (onlyAffected) return team.injury_count > 0;
        if (teamFilter) return true;
        return team.injury_count > 0 || !state.filters.query;
      });
  }

  function renderSummary() {
    const data = state.data;
    if (!data) return;
    const teams = data.teams || [];
    const outCount = teams.reduce((sum, team) => sum + Number(team.out_count || 0), 0);
    const dayCount = teams.reduce(
      (sum, team) => sum + Number(team.day_to_day_count || 0),
      0,
    );
    $("#injuriesSummary").innerHTML = [
      summaryCard("Lesiones", String(data.count ?? 0), "jugadores en el parte"),
      summaryCard("Equipos", String(data.team_count ?? 0), "con al menos una baja"),
      summaryCard("Bajas", String(outCount), `${dayCount} día a día`),
      summaryCard("Fuente", data.source || "ESPN", data.source_updated_at || "sin fecha"),
    ].join("");
    $("#injuriesSummary").setAttribute("aria-busy", "false");
    $("#injuriesRange").textContent = [data.season, data.source_updated_at]
      .filter(Boolean)
      .join(" · ");
    const badge = $("#injuriesTabCount");
    if (badge) {
      badge.textContent = String(data.count ?? 0);
      badge.hidden = !Number(data.count);
    }
  }

  function renderModelNote() {
    const data = state.data;
    if (!data) return;
    const model = data.adjustment;
    const detail = model
      ? `<p class="injury-impact-note">Coeficiente: ${nf2.format(Number(model.coefficient_points_per_share))} pts de margen por fracción completa del top-5 · ajuste ${esc(model.fit_season || "—")} (${num(model.fit_team_games, 0)} equipos-partido) → validación ${esc(model.holdout_season || "—")} (${num(model.holdout_team_games, 0)}).</p>`
      : "";
    $("#injuriesModelNote").innerHTML = `<div>
      <strong>Cómo afecta al equipo</strong>
      <p>${esc(data.model_note || "")}</p>
      <p class="injury-impact-note">${esc(data.impact_note || "")}</p>
      ${detail}
    </div>`;
  }

  function render() {
    const list = $("#injuriesList");
    if (!list) return;
    if (!state.data) return;
    renderSummary();
    renderModelNote();
    const teams = filteredTeams();
    if (!teams.length) {
      const warning = state.data.warning;
      list.innerHTML = warning
        ? `<div class="injury-empty">
            <strong>No hay datos de lesiones disponibles.</strong>
            <span>ESPN no respondió y no hay una copia anterior. La app sigue funcionando; puedes reintentar con Actualizar.</span>
          </div>`
        : `<div class="injury-empty">
            <strong>No hay lesiones que coincidan con el filtro.</strong>
            <span>Prueba a quitar el filtro de estado, cambiar de equipo o desmarcar “Solo equipos con bajas”.</span>
          </div>`;
      $("#injuriesNote").textContent = warning
        ? "Sin parte de ESPN no se puede describir el impacto de las bajas. El modelo base sigue funcionando sin ajuste."
        : "El parte se actualiza al consultar ESPN. El ganador y el hándicap usan las bajas confirmadas; el total no.";
      return;
    }
    list.innerHTML = teams.map(renderTeam).join("");
    const filteredCount = teams.reduce((sum, team) => sum + team.injury_count, 0);
    $("#injuriesNote").textContent = [
      `${filteredCount} lesiones en ${teams.length} equipos mostrados.`,
      state.data.stale
        ? "La fuente no se pudo actualizar; se muestra la última copia disponible."
        : "Fuente: ESPN. El ajuste de margen/ganador solo usa bajas confirmadas (Out) del top-5.",
      "El total y los props no se ajustan. Un jugador puede aparecer como baja y volver antes del partido: revisa la hora del parte.",
    ].join(" ");
  }

  async function request(url) {
    const response = await fetch(url, {
      headers: { Accept: "application/json" },
    });
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    if (!response.ok) {
      throw new Error(data.detail || `Error ${response.status}`);
    }
    return data;
  }

  async function load(force = false) {
    if (state.loading) return;
    if (state.loaded && !force) {
      render();
      return;
    }
    state.loading = true;
    const requestId = ++state.requestId;
    const button = $("#injuriesRefresh");
    if (button) button.disabled = true;
    setNotice($("#injuriesNotice"), "");
    $("#injuriesList").innerHTML =
      '<div class="injury-loading"><span class="spinner"></span>Cargando parte de ESPN…</div>';
    try {
      const data = await request("/api/injuries");
      if (requestId !== state.requestId) return;
      state.data = data;
      state.loaded = true;
      state.loading = false;
      if (data.warning) {
        setNotice($("#injuriesNotice"), data.warning, false);
      }
      render();
    } catch (error) {
      if (requestId !== state.requestId) return;
      state.loading = false;
      setNotice($("#injuriesNotice"), `No se pudieron cargar las lesiones: ${error.message}`, true);
      $("#injuriesSummary").innerHTML = "";
      $("#injuriesModelNote").innerHTML = "";
      $("#injuriesList").innerHTML = `<div class="injury-empty">
        <strong>No se pudo consultar el parte de lesiones.</strong>
        <span>La app sigue funcionando; puedes reintentar con Actualizar.</span>
      </div>`;
      $("#injuriesNote").textContent = "Comprueba la conexión y vuelve a intentarlo.";
      toast(error.message);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function focusTeam(team) {
    if (!team) return;
    state.filters.team = String(team).toUpperCase();
    const select = $("#injuriesTeam");
    if (select) {
      const option = Array.from(select.options).find((item) => item.value === state.filters.team);
      if (option) select.value = state.filters.team;
      else {
        const fallback = document.createElement("option");
        fallback.value = state.filters.team;
        fallback.textContent = state.filters.team;
        select.append(fallback);
        select.value = state.filters.team;
      }
    }
    render();
  }

  function bind() {
    $("#injuriesRefresh")?.addEventListener("click", () => load(true));
    $("#injuriesTeam")?.addEventListener("change", (event) => {
      state.filters.team = event.target.value;
      render();
    });
    $("#injuriesStatus")?.addEventListener("change", (event) => {
      state.filters.status = event.target.value;
      render();
    });
    $("#injuriesOnlyAffected")?.addEventListener("change", (event) => {
      state.filters.onlyAffected = event.target.checked;
      render();
    });
    let searchTimer;
    $("#injuriesSearch")?.addEventListener("input", (event) => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        state.filters.query = event.target.value.trim();
        render();
      }, 250);
    });
    $("#injuriesList")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-player-id]");
      if (!button || typeof window.openPlayerDetail !== "function") return;
      window.openPlayerDetail(button.dataset.playerId, { opener: button });
    });
    window.addEventListener("injuries:activate", () => load(false));
    window.addEventListener("injuries:focus", (event) => focusTeam(event.detail?.team));
  }

  bind();
})();
