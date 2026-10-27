"""Fuentes alternativas de ESPN para las actualizaciones de la web.

``stats.nba.com`` bloquea o deja colgadas las peticiones desde los runners de
GitHub Actions. La app ya usa ESPN para marcadores y cuotas, asi que este
modulo reutiliza esas mismas APIs publicas para mantener frescos los artefactos
de jugadores, props y calendario.

Las funciones de este modulo se usan solo desde los scripts de actualizacion;
la API de Vercel sigue leyendo los CSV publicados en ``artifacts``.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from ..config import PROJECT_ROOT

LOGGER = logging.getLogger(__name__)

ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_WEB = (
    "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"
)
EASTERN = ZoneInfo("America/New_York")
DEFAULT_ARTIFACTS = PROJECT_ROOT / "artifacts"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3

# ESPN usa abreviaturas propias en algunos equipos.
ESPN_TO_NBA = {
    "GS": "GSW",
    "NO": "NOP",
    "NY": "NYK",
    "SA": "SAS",
    "UTAH": "UTA",
    "WSH": "WAS",
}

PLAYER_STATS_COLUMNS = [
    "player_id",
    "player_name",
    "team_id",
    "team",
    "gp",
    "min",
    "pts",
    "reb",
    "ast",
    "pra",
    "fgm",
    "fga",
    "fg_pct",
    "fg3m",
    "fg3a",
    "fg3_pct",
    "ftm",
    "fta",
    "ft_pct",
    "oreb",
    "dreb",
    "stl",
    "blk",
    "tov",
    "pf",
    "plus_minus",
]

SCHEDULE_COLUMNS = [
    "game_id",
    "game_date",
    "game_time_et",
    "status",
    "status_text",
    "home",
    "away",
    "arena",
    "is_neutral",
    "home_score",
    "away_score",
    "total_points",
]

BOXSCORE_COLUMNS = [
    "season",
    "player_id",
    "player_name",
    "team",
    "game_id",
    "game_date",
    "matchup",
    "opponent",
    "is_home",
    "wl",
    "min",
    "pts",
    "reb",
    "ast",
    "stl",
    "blk",
    "fgm",
    "fga",
    "fg_pct",
    "fg3m",
    "fg3a",
    "fg3_pct",
    "ftm",
    "fta",
    "ft_pct",
    "tov",
    "oreb",
    "dreb",
    "pf",
    "plus_minus",
    "pra",
]

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def nba_team(abbreviation: str | None) -> str:
    """Normaliza una abreviatura de equipo de ESPN a la de la NBA."""
    value = str(abbreviation or "").upper().strip()
    return ESPN_TO_NBA.get(value, value)


def normalize_name(name: str | None) -> str:
    """Clave tolerante a acentos, puntuacion y sufijos (Jr., III, ...)."""
    value = unicodedata.normalize("NFKD", str(name or ""))
    value = value.encode("ascii", "ignore").decode("ascii")
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if token not in _SUFFIXES
    ]
    return " ".join(tokens)


def _number(value: Any, default: float = 0.0) -> float:
    if value is None or value == "" or value == "-":
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _ratio(value: Any, default: float = 0.0) -> float:
    number = _number(value, default)
    if number > 1:
        number /= 100
    return number


def _parse_made_attempt(value: Any) -> tuple[int, int]:
    if value is None:
        return 0, 0
    parts = str(value).strip().split("-")
    if len(parts) != 2:
        return 0, 0
    return int(_number(parts[0])), int(_number(parts[1]))


def _parse_minutes(value: Any) -> float:
    text = str(value or "").strip()
    if not text or text == "-":
        return 0.0
    if ":" in text:
        first, second = text.split(":", 1)
        return _number(first) + _number(second) / 60
    return _number(text)


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def season_year(season: str) -> int:
    """Convierte ``2026-27`` al ano que usa ESPN (``2027``)."""
    return int(str(season).split("-")[0]) + 1


def _get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> dict[str, Any]:
    """GET con reintentos y un User-Agent de navegador."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    }
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as error:  # noqa: BLE001 - reintentar fallos de red
            last_error = error
            LOGGER.warning(
                "ESPN fallo (%s/%s) en %s: %s",
                attempt + 1,
                retries,
                url,
                error,
            )
            if attempt + 1 < retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"No se pudo consultar ESPN ({url}): {last_error}")


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as error:  # noqa: BLE001 - un artefacto corrupto no debe romper
        LOGGER.warning("No se pudo leer %s: %s", path, error)
        return pd.DataFrame()


@dataclass(frozen=True)
class PlayerLookup:
    """Equivalencias nombre/equipo de ESPN con los IDs de la NBA."""

    by_name: dict[str, int]
    by_name_team: dict[tuple[str, str], int]
    names: dict[int, str]

    def resolve(self, name: str, team: str | None = None) -> int | None:
        key = normalize_name(name)
        if not key:
            return None
        team_key = nba_team(team)
        if team_key:
            found = self.by_name_team.get((key, team_key))
            if found is not None:
                return found
        return self.by_name.get(key)

    def canonical_name(self, player_id: int, fallback: str) -> str:
        return self.names.get(int(player_id), fallback)


def load_player_lookup(
    artifacts_dir: Path | str = DEFAULT_ARTIFACTS,
) -> PlayerLookup:
    """Construye el cruce nombre/equipo -> player_id con los artefactos web."""
    root = Path(artifacts_dir)
    paths = [
        root / "player_stats_2026_27_pergame.csv",
        root / "player_stats_2026_27_totals.csv",
        root / "player_stats_2025_26_pergame.csv",
        root / "player_state.csv",
        root / "player_bio.csv",
        root / "player_game_logs_recent.csv",
    ]
    by_name: dict[str, int] = {}
    by_name_team: dict[tuple[str, str], int] = {}
    names: dict[int, str] = {}

    for path in paths:
        frame = _read_csv(path)
        if frame.empty or not {"player_id", "player_name"}.issubset(frame.columns):
            continue
        team_column = "team" if "team" in frame.columns else None
        for row in frame[["player_id", "player_name"] + ([team_column] if team_column else [])].itertuples(index=False):
            player_id = pd.to_numeric(getattr(row, "player_id"), errors="coerce")
            if pd.isna(player_id):
                continue
            player_id = int(player_id)
            name = str(getattr(row, "player_name", "") or "")
            key = normalize_name(name)
            if not key:
                continue
            by_name.setdefault(key, player_id)
            names.setdefault(player_id, name)
            if team_column:
                team = nba_team(getattr(row, "team", None))
                if team:
                    by_name_team.setdefault((key, team), player_id)
    return PlayerLookup(by_name=by_name, by_name_team=by_name_team, names=names)


def load_team_ids(
    artifacts_dir: Path | str = DEFAULT_ARTIFACTS,
) -> dict[str, int]:
    """Equipo NBA -> team_id, usando team_state y las estadisticas publicadas."""
    root = Path(artifacts_dir)
    team_ids: dict[str, int] = {}

    state = _read_csv(root / "team_state.csv")
    if not state.empty and {"team_abbreviation", "team_id"}.issubset(state.columns):
        for row in state[["team_abbreviation", "team_id"]].itertuples(index=False):
            team = nba_team(getattr(row, "team_abbreviation", None))
            team_id = pd.to_numeric(getattr(row, "team_id"), errors="coerce")
            if team and not pd.isna(team_id):
                team_ids[team] = int(team_id)

    for filename in (
        "player_stats_2026_27_pergame.csv",
        "player_stats_2025_26_pergame.csv",
    ):
        stats = _read_csv(root / filename)
        if stats.empty or not {"team", "team_id"}.issubset(stats.columns):
            continue
        for row in stats[["team", "team_id"]].itertuples(index=False):
            team = nba_team(getattr(row, "team", None))
            team_id = pd.to_numeric(getattr(row, "team_id"), errors="coerce")
            if team and not pd.isna(team_id):
                team_ids.setdefault(team, int(team_id))
    return team_ids


def _category_values(
    categories: list[dict[str, Any]],
    definitions: dict[str, list[str]],
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    result: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for category in categories:
        name = str(category.get("name", ""))
        names = definitions.get(name, [])
        values = dict(zip(names, category.get("values") or []))
        totals = dict(zip(names, category.get("totals") or []))
        result[name] = (values, totals)
    return result


def _stat(
    categories: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    category: str,
    key: str,
    *,
    totals: bool = False,
    default: float = 0.0,
) -> float:
    pair = categories.get(category)
    if not pair:
        return default
    values = pair[1] if totals else pair[0]
    return _number(values.get(key), default)


def fetch_player_stats(
    season: str,
    *,
    artifacts_dir: Path | str = DEFAULT_ARTIFACTS,
    season_type: int = 2,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Descarga las estadisticas de la temporada desde ESPN.

    Devuelve ``(pergame, totals)`` con las mismas columnas que los artefactos
    que consume la app. ESPN no publica oreb/dreb ni plus/minus en el resumen
    de temporada; esas columnas quedan vacias en lugar de inventar valores.
    """
    year = season_year(season)
    url = f"{ESPN_WEB}/statistics/byathlete"
    definitions: dict[str, list[str]] = {}
    athletes: list[dict[str, Any]] = []
    page = 1

    while True:
        payload = _get_json(
            url,
            params={
                "region": "us",
                "lang": "en",
                "contentorigin": "espn",
                "isqualified": "false",
                "page": page,
                "limit": 100,
                "sort": "offensive.avgPoints:desc",
                "season": year,
                "seasontype": season_type,
            },
            timeout=timeout,
            retries=retries,
        )
        if not definitions:
            definitions = {
                str(category.get("name", "")): list(category.get("names") or [])
                for category in payload.get("categories", [])
            }
        athletes.extend(payload.get("athletes", []))
        pagination = payload.get("pagination", {})
        pages = int(pagination.get("pages", 1) or 1)
        if page >= pages:
            break
        page += 1

    lookup = load_player_lookup(artifacts_dir)
    team_ids = load_team_ids(artifacts_dir)
    pergame_rows: list[dict[str, Any]] = []
    total_rows: list[dict[str, Any]] = []

    for item in athletes:
        athlete = item.get("athlete", {})
        player_name = str(athlete.get("displayName", "") or "").strip()
        team = nba_team(athlete.get("teamShortName"))
        player_id = lookup.resolve(player_name, team)
        if player_id is None:
            LOGGER.debug("Jugador de ESPN sin ID NBA: %s (%s)", player_name, team)
            continue

        categories = _category_values(item.get("categories", []), definitions)
        gp = int(
            _stat(categories, "general", "gamesPlayed", default=0)
        )
        if gp <= 0:
            continue

        canonical = lookup.canonical_name(player_id, player_name)
        common = {
            "player_id": int(player_id),
            "player_name": canonical,
            "team_id": int(team_ids.get(team, 0)),
            "team": team,
            "gp": gp,
        }
        pergame = dict(common)
        totals = dict(common)
        for target, source, keys in (
            (pergame, "pergame", {
                "min": ("general", "avgMinutes"),
                "reb": ("general", "avgRebounds"),
                "pf": ("general", "avgFouls"),
                "pts": ("offensive", "avgPoints"),
                "ast": ("offensive", "avgAssists"),
                "fgm": ("offensive", "avgFieldGoalsMade"),
                "fga": ("offensive", "avgFieldGoalsAttempted"),
                "fg3m": ("offensive", "avgThreePointFieldGoalsMade"),
                "fg3a": ("offensive", "avgThreePointFieldGoalsAttempted"),
                "ftm": ("offensive", "avgFreeThrowsMade"),
                "fta": ("offensive", "avgFreeThrowsAttempted"),
                "tov": ("offensive", "avgTurnovers"),
                "stl": ("defensive", "avgSteals"),
                "blk": ("defensive", "avgBlocks"),
            }),
            (totals, "totals", {
                "min": ("general", "minutes"),
                "reb": ("general", "rebounds"),
                "pf": ("general", "fouls"),
                "pts": ("offensive", "points"),
                "ast": ("offensive", "assists"),
                "fgm": ("offensive", "fieldGoalsMade"),
                "fga": ("offensive", "fieldGoalsAttempted"),
                "fg3m": ("offensive", "threePointFieldGoalsMade"),
                "fg3a": ("offensive", "threePointFieldGoalsAttempted"),
                "ftm": ("offensive", "freeThrowsMade"),
                "fta": ("offensive", "freeThrowsAttempted"),
                "tov": ("offensive", "turnovers"),
                "stl": ("defensive", "steals"),
                "blk": ("defensive", "blocks"),
            }),
        ):
            for column, (category, key) in keys.items():
                target[column] = _stat(
                    categories, category, key, totals=(source == "totals")
                )

        for target, is_total in ((pergame, False), (totals, True)):
            fgm, fga = target["fgm"], target["fga"]
            fg3m, fg3a = target["fg3m"], target["fg3a"]
            ftm, fta = target["ftm"], target["fta"]
            target["fg_pct"] = (
                _ratio(_stat(categories, "offensive", "fieldGoalPct", totals=is_total))
                if fga
                else 0.0
            )
            target["fg3_pct"] = (
                _ratio(
                    _stat(
                        categories,
                        "offensive",
                        "threePointFieldGoalPct",
                        totals=is_total,
                    )
                )
                if fg3a
                else 0.0
            )
            target["ft_pct"] = (
                _ratio(
                    _stat(categories, "offensive", "freeThrowPct", totals=is_total)
                )
                if fta
                else 0.0
            )
            if not target["fg_pct"] and fga:
                target["fg_pct"] = fgm / fga
            if not target["fg3_pct"] and fg3a:
                target["fg3_pct"] = fg3m / fg3a
            if not target["ft_pct"] and fta:
                target["ft_pct"] = ftm / fta
            target["pra"] = target["pts"] + target["reb"] + target["ast"]
            target["oreb"] = None
            target["dreb"] = None
            target["plus_minus"] = None

        pergame_rows.append(pergame)
        total_rows.append(totals)

    pergame_frame = pd.DataFrame(pergame_rows, columns=PLAYER_STATS_COLUMNS)
    totals_frame = pd.DataFrame(total_rows, columns=PLAYER_STATS_COLUMNS)
    for frame in (pergame_frame, totals_frame):
        if not frame.empty:
            frame["player_id"] = frame["player_id"].astype(int)
            frame["team_id"] = frame["team_id"].fillna(0).astype(int)
            frame["gp"] = frame["gp"].astype(int)
    LOGGER.info(
        "ESPN %s: %s jugadores con ID NBA (%s en ESPN)",
        season,
        len(pergame_frame),
        len(athletes),
    )
    return pergame_frame, totals_frame


def _event_row(event: dict[str, Any]) -> dict[str, Any] | None:
    event_id = str(event.get("id", "") or "")
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    home = next(
        (item for item in competitors if item.get("homeAway") == "home"), None
    )
    away = next(
        (item for item in competitors if item.get("homeAway") == "away"), None
    )
    if not event_id or home is None or away is None:
        return None

    home_team = nba_team((home.get("team") or {}).get("abbreviation"))
    away_team = nba_team((away.get("team") or {}).get("abbreviation"))
    if not home_team or not away_team:
        return None

    raw_date = event.get("date") or competition.get("date")
    if not raw_date:
        return None
    moment = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
    moment_et = moment.astimezone(EASTERN)

    status_type = (event.get("status") or {}).get("type", {})
    state = str(status_type.get("state", "pre"))
    completed = bool(status_type.get("completed", state == "post"))
    if state == "post" and completed:
        status = 3
    elif state == "in":
        status = 2
    else:
        status = 1
    status_text = str(
        status_type.get("shortDetail")
        or status_type.get("description")
        or status_type.get("detail")
        or "Programado"
    )

    home_score = pd.to_numeric(home.get("score"), errors="coerce")
    away_score = pd.to_numeric(away.get("score"), errors="coerce")
    if status != 3:
        home_score = pd.NA if pd.isna(home_score) else home_score
        away_score = pd.NA if pd.isna(away_score) else away_score
    total_points = (
        float(home_score) + float(away_score)
        if status == 3 and not pd.isna(home_score) and not pd.isna(away_score)
        else pd.NA
    )

    return {
        "event_id": event_id,
        "game_date": moment_et.date(),
        "game_time_et": moment_et.strftime("%H:%M"),
        "status": status,
        "status_text": status_text,
        "home": home_team,
        "away": away_team,
        "arena": str((competition.get("venue") or {}).get("fullName", "") or ""),
        "is_neutral": bool(competition.get("neutralSite", False)),
        "home_score": home_score,
        "away_score": away_score,
        "total_points": total_points,
    }


def fetch_scoreboard_days(
    days: Iterable[date],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> list[dict[str, Any]]:
    """Marcadores de ESPN para una lista de fechas, sin duplicados."""
    games: dict[str, dict[str, Any]] = {}
    for day in days:
        payload = _get_json(
            f"{ESPN_SITE}/scoreboard",
            params={"dates": day.strftime("%Y%m%d"), "limit": 100},
            timeout=timeout,
            retries=retries,
        )
        for event in payload.get("events", []):
            row = _event_row(event)
            if row is not None:
                games[row["event_id"]] = row
    return list(games.values())


def fetch_scoreboard(
    start: date,
    end: date,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> list[dict[str, Any]]:
    """Marcadores de ESPN entre dos fechas inclusivas."""
    if end < start:
        return []
    days = [
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
    ]
    return fetch_scoreboard_days(days, timeout=timeout, retries=retries)


def merge_schedule(
    existing: pd.DataFrame,
    games: list[dict[str, Any]],
) -> pd.DataFrame:
    """Incorpora marcadores de ESPN conservando los game_id de la NBA."""
    if existing is None or existing.empty:
        frame = pd.DataFrame(columns=SCHEDULE_COLUMNS)
    else:
        frame = existing.copy()
        if "game_date" in frame.columns:
            frame["game_date"] = pd.to_datetime(
                frame["game_date"], errors="coerce"
            ).dt.date
    for column in SCHEDULE_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    if not frame.empty:
        frame["game_id"] = frame["game_id"].astype(str)
        frame["status"] = pd.to_numeric(frame["status"], errors="coerce")

    for game in games:
        game_date = _as_date(game["game_date"])
        mask = (
            frame["game_date"].eq(game_date)
            & frame["home"].astype(str).eq(str(game["home"]))
            & frame["away"].astype(str).eq(str(game["away"]))
        )
        if mask.any():
            index = frame.index[mask][0]
            current_status = pd.to_numeric(
                pd.Series([frame.at[index, "status"]]), errors="coerce"
            ).iloc[0]
            # No volver a "programado" un partido que ya estaba finalizado.
            if pd.isna(current_status) or game["status"] >= int(current_status):
                frame.at[index, "status"] = game["status"]
                frame.at[index, "status_text"] = game["status_text"]
                if not pd.isna(game["home_score"]):
                    frame.at[index, "home_score"] = game["home_score"]
                if not pd.isna(game["away_score"]):
                    frame.at[index, "away_score"] = game["away_score"]
                if not pd.isna(game["total_points"]):
                    frame.at[index, "total_points"] = game["total_points"]
                frame.at[index, "game_time_et"] = game["game_time_et"]
                frame.at[index, "arena"] = game["arena"]
                frame.at[index, "is_neutral"] = game["is_neutral"]
            continue

        record = {
            "game_id": f"espn_{game['event_id']}",
            "game_date": game_date,
            "game_time_et": game["game_time_et"],
            "status": game["status"],
            "status_text": game["status_text"],
            "home": game["home"],
            "away": game["away"],
            "arena": game["arena"],
            "is_neutral": game["is_neutral"],
            "home_score": game["home_score"],
            "away_score": game["away_score"],
            "total_points": game["total_points"],
        }
        frame = pd.concat([frame, pd.DataFrame([record])], ignore_index=True)

    if frame.empty:
        return frame[SCHEDULE_COLUMNS]
    # No se deduplica por fecha/equipos: el calendario de la NBA contiene
    # marcadores TBD (NONE/NONE) para las rondas de la Copa que comparten dia.
    frame["game_date"] = pd.to_datetime(frame["game_date"]).dt.strftime(
        "%Y-%m-%d"
    )
    frame = frame.sort_values(
        ["game_date", "game_time_et", "game_id"], kind="stable"
    ).reset_index(drop=True)
    return frame[SCHEDULE_COLUMNS]


def update_schedule_from_espn(
    season: str,
    existing: pd.DataFrame | None = None,
    *,
    today: date | None = None,
    days_back: int = 14,
    days_forward: int = 10,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> pd.DataFrame:
    """Actualiza el calendario con resultados recientes y partidos proximos.

    La temporada completa ya esta en el artefacto; solo se consultan las fechas
    alrededor de hoy para no hacer cientos de peticiones en cada workflow.
    """
    current = today or datetime.now(EASTERN).date()
    start = current - timedelta(days=days_back)
    if existing is not None and not existing.empty and "game_date" in existing:
        dates = pd.to_datetime(existing["game_date"], errors="coerce")
        finals = dates[pd.to_numeric(existing.get("status"), errors="coerce").eq(3)]
        if not finals.empty:
            last_final = finals.max().date()
            # Si el artefacto lleva mucho tiempo parado, rellena como maximo
            # un mes hacia atras para que el workflow no se eternice.
            start = min(start, max(last_final - timedelta(days=1), current - timedelta(days=30)))
    end = current + timedelta(days=days_forward)
    games = fetch_scoreboard(
        start, end, timeout=timeout, retries=retries
    )
    LOGGER.info(
        "ESPN calendario: %s partidos entre %s y %s", len(games), start, end
    )
    return merge_schedule(
        existing if existing is not None else pd.DataFrame(),
        games,
    )


def _event_schedule_id(
    schedule: pd.DataFrame | None,
    event: dict[str, Any],
) -> str:
    if schedule is None or schedule.empty:
        return f"espn_{event['id']}"
    game_date = _as_date(event["_date"])
    mask = (
        pd.to_datetime(schedule["game_date"], errors="coerce").dt.date.eq(game_date)
        & schedule["home"].astype(str).eq(str(event["_home"]))
        & schedule["away"].astype(str).eq(str(event["_away"]))
    )
    if mask.any():
        return str(schedule.loc[mask, "game_id"].iloc[0])
    return f"espn_{event['id']}"


def _boxscore_rows(
    summary: dict[str, Any],
    event: dict[str, Any],
    *,
    season: str,
    game_id: str,
    lookup: PlayerLookup,
    team_ids: dict[str, int],
) -> list[dict[str, Any]]:
    game_date = _as_date(event["_date"])
    home = str(event["_home"])
    away = str(event["_away"])
    home_score = pd.to_numeric(event.get("_home_score"), errors="coerce")
    away_score = pd.to_numeric(event.get("_away_score"), errors="coerce")

    rows: list[dict[str, Any]] = []
    for team_block in summary.get("boxscore", {}).get("players", []):
        team = nba_team((team_block.get("team") or {}).get("abbreviation"))
        if not team:
            continue
        statistics = (team_block.get("statistics") or [{}])[0]
        keys = statistics.get("keys") or []
        for item in statistics.get("athletes", []):
            if item.get("didNotPlay"):
                continue
            athlete = item.get("athlete") or {}
            values = dict(zip(keys, item.get("stats") or []))
            minutes = _parse_minutes(values.get("minutes"))
            if minutes <= 0:
                continue
            name = str(athlete.get("displayName", "") or "").strip()
            player_id = lookup.resolve(name, team)
            if player_id is None:
                LOGGER.debug("Boxscore sin ID NBA: %s (%s)", name, team)
                continue

            fgm, fga = _parse_made_attempt(
                values.get("fieldGoalsMade-fieldGoalsAttempted")
            )
            fg3m, fg3a = _parse_made_attempt(
                values.get(
                    "threePointFieldGoalsMade-threePointFieldGoalsAttempted"
                )
            )
            ftm, fta = _parse_made_attempt(
                values.get("freeThrowsMade-freeThrowsAttempted")
            )
            pts = _number(values.get("points"))
            reb = _number(values.get("rebounds"))
            ast = _number(values.get("assists"))
            tov = _number(values.get("turnovers"))
            stl = _number(values.get("steals"))
            blk = _number(values.get("blocks"))
            is_home = team == home
            opponent = away if is_home else home
            if pd.isna(home_score) or pd.isna(away_score):
                wl = ""
            else:
                won = (
                    float(home_score) > float(away_score)
                    if is_home
                    else float(away_score) > float(home_score)
                )
                wl = "W" if won else "L"

            rows.append(
                {
                    "season": season,
                    "player_id": int(player_id),
                    "player_name": lookup.canonical_name(player_id, name),
                    "team": team,
                    "game_id": str(game_id),
                    "game_date": game_date,
                    "matchup": (
                        f"{team} vs. {opponent}"
                        if is_home
                        else f"{team} @ {opponent}"
                    ),
                    "opponent": opponent,
                    "is_home": is_home,
                    "wl": wl,
                    "min": minutes,
                    "pts": pts,
                    "reb": reb,
                    "ast": ast,
                    "stl": stl,
                    "blk": blk,
                    "fgm": fgm,
                    "fga": fga,
                    "fg_pct": fgm / fga if fga else 0.0,
                    "fg3m": fg3m,
                    "fg3a": fg3a,
                    "fg3_pct": fg3m / fg3a if fg3a else 0.0,
                    "ftm": ftm,
                    "fta": fta,
                    "ft_pct": ftm / fta if fta else 0.0,
                    "tov": tov,
                    "oreb": _number(values.get("offensiveRebounds")),
                    "dreb": _number(values.get("defensiveRebounds")),
                    "pf": _number(values.get("fouls")),
                    "plus_minus": _number(values.get("plusMinus")),
                    "pra": pts + reb + ast,
                }
            )
    return rows


def fetch_boxscore(
    event: dict[str, Any],
    *,
    season: str,
    game_id: str,
    artifacts_dir: Path | str = DEFAULT_ARTIFACTS,
    lookup: PlayerLookup | None = None,
    team_ids: dict[str, int] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> pd.DataFrame:
    """Convierte el resumen de un partido de ESPN en game logs por jugador."""
    summary = _get_json(
        f"{ESPN_SITE}/summary",
        params={"event": str(event["id"])},
        timeout=timeout,
        retries=retries,
    )
    rows = _boxscore_rows(
        summary,
        event,
        season=season,
        game_id=game_id,
        lookup=lookup or load_player_lookup(artifacts_dir),
        team_ids=team_ids or load_team_ids(artifacts_dir),
    )
    frame = pd.DataFrame(rows, columns=BOXSCORE_COLUMNS)
    if not frame.empty:
        for column in ("fg_pct", "fg3_pct", "ft_pct", "pra"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _existing_games(logs: pd.DataFrame) -> tuple[set[str], set[tuple[date, str, str]]]:
    if logs is None or logs.empty:
        return set(), set()
    game_ids = {
        str(value)
        for value in logs.get("game_id", pd.Series(dtype=str)).dropna().tolist()
    }
    keys: set[tuple[date, str, str]] = set()
    if {"game_date", "team", "opponent"}.issubset(logs.columns):
        for row in logs[["game_date", "team", "opponent"]].itertuples(index=False):
            try:
                game_date = _as_date(row.game_date)
            except Exception:  # noqa: BLE001
                continue
            teams = sorted(
                [str(row.team).upper().strip(), str(row.opponent).upper().strip()]
            )
            if all(teams):
                keys.add((game_date, teams[0], teams[1]))
    return game_ids, keys


def append_recent_games(
    history: pd.DataFrame,
    schedule: pd.DataFrame | None,
    season: str,
    *,
    today: date | None = None,
    days_back: int = 14,
    artifacts_dir: Path | str = DEFAULT_ARTIFACTS,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> pd.DataFrame:
    """Agrega a ``history`` los partidos finalizados que aun no estan dentro.

    Se consulta el marcador de ESPN y, solo para partidos nuevos, el resumen
    con el boxscore. Normalmente son unos pocos partidos por ejecucion.
    """
    current = today or datetime.now(EASTERN).date()
    current_logs = history[
        history.get("season", pd.Series(dtype=str)).astype(str).eq(str(season))
    ].copy()
    last_date: date | None = None
    if not current_logs.empty:
        parsed = pd.to_datetime(current_logs["game_date"], errors="coerce").dropna()
        if not parsed.empty:
            last_date = parsed.max().date()

    start = current - timedelta(days=days_back)
    if last_date is not None:
        # Revisa al menos el dia anterior; asi también recupera un partido
        # cuyo boxscore aun no estaba publicado en la ejecucion previa.
        start = max(start, last_date - timedelta(days=1))
        start = min(start, current)
    if last_date is not None and last_date > current:
        start = current - timedelta(days=days_back)

    games = fetch_scoreboard(
        start, current, timeout=timeout, retries=retries
    )
    lookup = load_player_lookup(artifacts_dir)
    team_ids = load_team_ids(artifacts_dir)
    game_ids, game_keys = _existing_games(current_logs)
    new_frames: list[pd.DataFrame] = []

    for game in games:
        if int(game["status"]) != 3:
            continue
        game_date = _as_date(game["game_date"])
        key = tuple(sorted([str(game["home"]), str(game["away"])]))
        if (game_date, key[0], key[1]) in game_keys:
            continue
        event = {
            "id": game["event_id"],
            "_date": game_date,
            "_home": game["home"],
            "_away": game["away"],
            "_home_score": game["home_score"],
            "_away_score": game["away_score"],
        }
        game_id = _event_schedule_id(schedule, event)
        if game_id in game_ids:
            continue
        try:
            frame = fetch_boxscore(
                event,
                season=season,
                game_id=game_id,
                artifacts_dir=artifacts_dir,
                lookup=lookup,
                team_ids=team_ids,
                timeout=timeout,
                retries=retries,
            )
        except Exception as error:  # noqa: BLE001 - un boxscore no bloquea el resto
            LOGGER.warning(
                "No se pudo descargar el boxscore %s: %s",
                game["event_id"],
                error,
            )
            continue
        if frame.empty:
            continue
        new_frames.append(frame)
        game_ids.add(game_id)
        game_keys.add((game_date, key[0], key[1]))

    if not new_frames:
        LOGGER.info("ESPN props: no hay partidos finalizados nuevos")
        return history

    added = pd.concat(new_frames, ignore_index=True)
    combined = pd.concat([history, added], ignore_index=True, sort=False)
    for column in BOXSCORE_COLUMNS:
        if column not in combined.columns:
            combined[column] = None
    combined = combined.drop_duplicates(
        subset=["season", "game_id", "player_id"], keep="last"
    )
    combined["game_date"] = pd.to_datetime(
        combined["game_date"], errors="coerce"
    )
    combined = combined.sort_values(
        ["player_id", "game_date", "game_id"], kind="stable"
    ).reset_index(drop=True)
    LOGGER.info(
        "ESPN props: %s partidos y %s filas agregadas",
        len(new_frames),
        len(added),
    )
    return combined
