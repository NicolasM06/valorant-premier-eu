#!/usr/bin/env python3
"""
Récupère le classement Valorant Premier (divisions Contender & Invite, région EU)
via l'API non-officielle HenrikDev, et écrit le résultat dans data/leaderboard.json
pour que le site statique (index.html) puisse l'afficher.

Variable d'environnement requise :
    HENRIKDEV_API_KEY  -> ta clé API (Basic ou Advanced), voir https://api.henrikdev.xyz/dashboard/

Usage:
    HENRIKDEV_API_KEY=xxxx python3 fetch_premier_data.py
"""

import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

API_BASE = "https://api.henrikdev.xyz"
REGION = "eu"

# Les 4 conférences européennes connues de Premier.
# (Si Riot en ajoute/renomme, ajuste cette liste — on peut aussi la
#  récupérer dynamiquement via /valorant/v1/premier/conferences)
EU_CONFERENCES = [
    "EU_CENTRAL_EAST",
    "EU_WEST",
    "EU_MIDDLE_EAST",
    "EU_TURKEY",
]

# Les divisions qu'on veut garder, du plus haut au plus bas.
# NB : la façon exacte dont l'API encode "Contender" / "Invite" au niveau
# de chaque équipe (nom textuel vs numéro de division) n'est pas garantie
# stable dans le temps. Le code ci-dessous essaie d'abord de lire un nom
# de division explicite, et retombe sur "les divisions numérotées les plus
# hautes" sinon. Vérifie le premier run avec `--debug` pour confirmer que
# le filtrage tombe juste, et ajuste TARGET_DIVISION_NAMES si besoin.
TARGET_DIVISION_NAMES = {"contender", "invite"}
FALLBACK_TOP_N_DIVISIONS = 2

OUTPUT_PATH = Path(__file__).parent / "data" / "leaderboard.json"
REQUEST_DELAY_SECONDS = 1.5  # reste sous la limite de 30-90 req/min


def api_get(path: str, api_key: str):
    url = f"{API_BASE}{path}"
    req = Request(url, headers={"Authorization": api_key, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"[erreur HTTP {e.code}] {url}\n{body}", file=sys.stderr)
        return None


def extract_division_label(team_entry: dict):
    """Essaie de trouver un nom de division lisible sur une entrée d'équipe."""
    for key in ("division_name", "division_label", "tier_name"):
        if key in team_entry and isinstance(team_entry[key], str):
            return team_entry[key]
    return None


def filter_top_divisions(teams: list):
    """Garde uniquement les équipes en Contender/Invite (ou équivalent top-2)."""
    if not teams:
        return []

    # Stratégie 1 : nom de division explicite dans la réponse
    named = [t for t in teams if (extract_division_label(t) or "").lower() in TARGET_DIVISION_NAMES]
    if named:
        return named

    # Stratégie 2 (repli) : on prend les N plus hautes divisions numériques
    numeric_divisions = sorted({t.get("division") for t in teams if isinstance(t.get("division"), int)}, reverse=True)
    if not numeric_divisions:
        # Rien d'exploitable pour filtrer : on renvoie tout (le site l'affichera brut)
        return teams
    top_divisions = set(numeric_divisions[:FALLBACK_TOP_N_DIVISIONS])
    return [t for t in teams if t.get("division") in top_divisions]


def fetch_conference_leaderboard(conference: str, api_key: str):
    print(f"→ Récupération du classement {conference}...")
    payload = api_get(f"/valorant/v1/premier/leaderboard/{REGION}/{conference}", api_key)
    time.sleep(REQUEST_DELAY_SECONDS)
    if not payload or "data" not in payload:
        print(f"  (aucune donnée pour {conference})")
        return []
    teams = payload["data"] if isinstance(payload["data"], list) else payload["data"].get("leaderboard", [])
    filtered = filter_top_divisions(teams)
    print(f"  {len(filtered)}/{len(teams)} équipes retenues (Contender/Invite)")
    for t in filtered:
        t["conference"] = conference
    return filtered


def fetch_team_history(team_id: str, api_key: str):
    payload = api_get(f"/valorant/v1/premier/{team_id}/history", api_key)
    time.sleep(REQUEST_DELAY_SECONDS)
    if not payload or "data" not in payload:
        return []
    return payload["data"].get("league_matches", [])


def main():
    api_key = os.environ.get("HENRIKDEV_API_KEY")
    if not api_key:
        print("ERREUR : définis la variable d'environnement HENRIKDEV_API_KEY", file=sys.stderr)
        sys.exit(1)

    with_history = "--with-history" in sys.argv  # optionnel : + d'appels API, + lent

    all_teams = []
    for conference in EU_CONFERENCES:
        all_teams.extend(fetch_conference_leaderboard(conference, api_key))

    if with_history:
        for team in all_teams:
            team_id = team.get("id") or team.get("team_id")
            if team_id:
                team["recent_matches"] = fetch_team_history(team_id, api_key)

    # Tri : conférence, puis division (desc), puis classement/points
    all_teams.sort(
        key=lambda t: (
            t.get("conference", ""),
            -(t.get("division") or 0),
            -(t.get("points") or t.get("ranking") or 0),
        )
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "region": REGION, "teams": all_teams},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n✓ {len(all_teams)} équipes écrites dans {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
