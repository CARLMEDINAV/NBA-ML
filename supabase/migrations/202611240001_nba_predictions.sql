-- NBA TOTALS 2.0 — Prediction Tracker
create table if not exists public.nba_predictions (
    prediction_id text primary key,
    game_id text not null,
    game_date date not null,
    home_team text not null,
    away_team text not null,
    predicted_at timestamptz not null,
    model_name text not null,
    model_version text not null,
    predicted_total double precision not null check (predicted_total >= 0),
    market_total_line double precision,
    predicted_margin double precision,
    home_handicap_line double precision,
    predicted_home_win_probability double precision
        check (predicted_home_win_probability is null or
               (predicted_home_win_probability >= 0 and predicted_home_win_probability <= 1)),
    source text not null default 'pregame' check (source = 'pregame'),
    features jsonb not null default '{}'::jsonb,
    actual_home_score integer check (actual_home_score is null or actual_home_score >= 0),
    actual_away_score integer check (actual_away_score is null or actual_away_score >= 0),
    settled_at timestamptz,
    created_at timestamptz not null default now()
);
create index if not exists idx_nba_predictions_game_date on public.nba_predictions (game_date desc);
create index if not exists idx_nba_predictions_game_id on public.nba_predictions (game_id);
create index if not exists idx_nba_predictions_settled on public.nba_predictions (settled_at) where settled_at is not null;
alter table public.nba_predictions enable row level security;
