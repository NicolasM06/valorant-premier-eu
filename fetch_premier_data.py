#!/usr/bin/env python3
"""
Récupère les équipes françaises de Valorant Premier (divisions Contender & Invite),
résout les rosters en pseudos, agrège TOUS les matchs de la saison de chaque équipe,
et calcule un classement individuel par joueur (K, D, A, ACS, K:D, HS%, ADR, + KAST%/
Clutch%/FK:FD/Rating quand les données brutes le permettent).

Écrit le résultat dans data/leaderboard.json pour que index.html l'affiche.

Variable d'environnement requise :
    HENRIKDEV_API_KEY  -> ta clé API (Basic ou Advanced), voir https://api.henrikdev.xyz/dashboard/

Usage:
    HENRIKDEV_API_KEY=xxxx python3 fetch_premier_data.py
    HENRIKDEV_API_KEY=xxxx python3 fetch_premier_data.py --debug
    HENRIKDEV_API_KEY=xxxx python3 fetch_premier_data.py --debug-match   # dump la structure brute d'1 match et s'arrête

IMPORTANT — HONNÊTETÉ SUR LES LIMITES :
Riot ne publie pas la formule exacte du "Rating" de VLR.gg. Le champ
"rating_estimate" calculé ici est une approximation communautaire basée sur
ACS/KAST/impact, PAS le vrai Rating VLR. De même, KAST%/Clutch%/FK:FD ne sont
calculables que si l'API renvoie des données round-par-round exploitables :
le code essaie plusieurs formes connues, et bascule sur "non disponible" si
la structure ne correspond à aucune d'entre elles. Lance --debug-match pour
vérifier ce qui est réellement exposé par l'API dans ton cas et ajuster
extract_round_level_stats() si besoin.
"""

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

API_BASE = "https://api.henrikdev.xyz"
REGION = "eu"
PLATFORM = "pc"

TARGET_DIVISION_NAMES = {"contender", "invite"}
FALLBACK_TOP_N_DIVISIONS = 2

OUTPUT_PATH = Path(__file__).parent / "data" / "leaderboard.json"
REQUEST_DELAY_SECONDS = 2.0
MAX_RETRIES = 3
REQUEST_TIMEOUT_SECONDS = 30

_puuid_name_cache = {}
_match_cache = {}


# --------------------------------------------------------------------------
# Appels API génériques
# --------------------------------------------------------------------------

def api_get(path: str, api_key: str):
    url = f"{API_BASE}{path}"
    req = Request(url, headers={"Authorization": api_key, "Accept": "application/json"})
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                time.sleep(REQUEST_DELAY_SECONDS)
                return data
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code == 429:
                wait = 15 * attempt
                print(f"  [429] pause {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"[HTTP {e.code}] {url}\n{body[:300]}", file=sys.stderr)
            time.sleep(REQUEST_DELAY_SECONDS)
            return None
        except (URLError, TimeoutError) as e:
            print(f"  [tentative {attempt}/{MAX_RETRIES}] {url} : {e}", file=sys.stderr)
            time.sleep(3 * attempt)
    print(f"[abandon] {url}", file=sys.stderr)
    return None


# --------------------------------------------------------------------------
# Découverte dynamique de la zone "France"
# --------------------------------------------------------------------------

def _flatten_strings(obj):
    """Retourne toutes les chaînes de caractères trouvées récursivement dans un objet JSON."""
    out = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_flatten_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_flatten_strings(v))
    return out


def discover_france_conference(api_key: str):
    """Interroge /premier/conferences pour trouver le code de zone actuel de la France,
    plutôt que de se fier à un nom codé en dur qui peut devenir obsolète (Riot a déjà
    renommé ses zones EMEA une fois)."""
    payload = api_get("/valorant/v1/premier/conferences", api_key)
    candidates = set()
    if payload and "data" in payload:
        for s in _flatten_strings(payload["data"]):
            if "FRANCE" in s.upper():
                candidates.add(s)
    if candidates:
        chosen = sorted(candidates)[0]
        print(f"✓ Zone France détectée dynamiquement : {chosen}")
        return chosen

    print("⚠️  Impossible de confirmer la zone France via /premier/conferences "
          "(endpoint non disponible ou format inattendu). Repli sur 'EU_FRANCE'.")
    return "EU_FRANCE"


# --------------------------------------------------------------------------
# Classement par équipe + filtrage Contender/Invite
# --------------------------------------------------------------------------

def _find_division_value(obj, path=""):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_lower = k.lower()
            new_path = f"{path}.{k}" if path else k
            if "divis" in key_lower or "tier" in key_lower:
                found.append((new_path, v))
            if isinstance(v, (dict, list)):
                found.extend(_find_division_value(v, new_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:1]):
            found.extend(_find_division_value(item, f"{path}[{i}]"))
    return found


def extract_division_label(team_entry):
    for _, value in _find_division_value(team_entry):
        if isinstance(value, str):
            return value
    return None


def extract_division_number(team_entry):
    for _, value in _find_division_value(team_entry):
        if isinstance(value, int):
            return value
    return None


def filter_top_divisions(teams: list, debug: bool = False):
    if not teams:
        return []
    if debug:
        print("  [debug] clés de premier niveau d'une équipe :", list(teams[0].keys()))
        for path, value in _find_division_value(teams[0]):
            print(f"  [debug] {path} = {value!r}")

    named = [t for t in teams if (extract_division_label(t) or "").lower() in TARGET_DIVISION_NAMES]
    if named:
        return named

    numeric_divisions = sorted(
        {extract_division_number(t) for t in teams if extract_division_number(t) is not None},
        reverse=True,
    )
    if not numeric_divisions:
        print("  ⚠️  Aucun champ de division exploitable — aucune équipe retenue. "
              "Relance avec --debug pour inspecter la structure brute.")
        return []
    top_divisions = set(numeric_divisions[:FALLBACK_TOP_N_DIVISIONS])
    return [t for t in teams if extract_division_number(t) in top_divisions]


def fetch_conference_leaderboard(conference: str, api_key: str, debug: bool = False):
    print(f"→ Récupération du classement {conference}...")
    payload = api_get(f"/valorant/v1/premier/leaderboard/{REGION}/{conference}", api_key)
    if not payload or "data" not in payload:
        print(f"  ⚠️  Aucune donnée pour {conference}")
        return []
    teams = payload["data"] if isinstance(payload["data"], list) else payload["data"].get("leaderboard", [])
    if not teams:
        print(f"  (0 équipe renvoyée)")
        return []
    filtered = filter_top_divisions(teams, debug=debug)
    print(f"  {len(filtered)}/{len(teams)} équipes retenues (Contender/Invite)")
    for t in filtered:
        t["conference"] = conference
    return filtered


def fetch_team_history(team_id: str, api_key: str):
    payload = api_get(f"/valorant/v1/premier/{team_id}/history", api_key)
    if not payload or "data" not in payload:
        return []
    return payload["data"].get("league_matches", [])


def resolve_player_name(puuid: str, api_key: str):
    if puuid in _puuid_name_cache:
        return _puuid_name_cache[puuid]
    payload = api_get(f"/valorant/v2/by-puuid/account/{puuid}", api_key)
    if payload and "data" in payload:
        name, tag = payload["data"].get("name"), payload["data"].get("tag")
        result = f"{name}#{tag}" if name else None
    else:
        result = None
    _puuid_name_cache[puuid] = result
    return result


def resolve_roster(team: dict, api_key: str):
    roster = team.get("roster") or {}
    members = roster.get("members") or []
    team["roster_resolved"] = [
        {"puuid": p, "name": resolve_player_name(p, api_key) or "(pseudo non résolu)"}
        for p in members
    ]


# --------------------------------------------------------------------------
# Récupération + parsing des matchs individuels
# --------------------------------------------------------------------------

def fetch_match(match_id: str, api_key: str, debug_match: bool = False):
    if match_id in _match_cache:
        return _match_cache[match_id]
    payload = api_get(f"/valorant/v4/match/{REGION}/{match_id}", api_key)
    data = payload["data"] if payload and "data" in payload else None
    if debug_match:
        print(f"\n=== DIAGNOSTIC MATCH {match_id} ===")
        if not data:
            print("Aucune donnée reçue.")
        else:
            print(f"Clés de premier niveau : {list(data.keys())}")
            players = extract_players(data)
            if players:
                print(f"\nClés d'un joueur : {list(players[0].keys())}")
                stats_obj = players[0].get("stats", players[0])
                print(f"Clés de 'stats' (ou du joueur si pas de sous-objet) : {list(stats_obj.keys()) if isinstance(stats_obj, dict) else stats_obj}")
            rounds = data.get("rounds")
            if isinstance(rounds, list) and rounds:
                print(f"\n'rounds' est présent : {len(rounds)} rounds.")
                print(f"Clés d'un round : {list(rounds[0].keys())}")
                for key, value in rounds[0].items():
                    if isinstance(value, list) and value and isinstance(value[0], dict):
                        print(f"  → 'rounds[0].{key}' est une liste d'objets, clés du premier élément : {list(value[0].keys())}")
                        # Un niveau de plus si on trouve un sous-objet 'stats' imbriqué
                        nested_stats = value[0].get("stats")
                        if isinstance(nested_stats, dict):
                            print(f"    → 'rounds[0].{key}[0].stats' clés : {list(nested_stats.keys())}")
                        player_field = value[0].get("player")
                        print(f"    → 'rounds[0].{key}[0].player' = {player_field!r} (type: {type(player_field).__name__})")
                    elif isinstance(value, dict):
                        print(f"  → 'rounds[0].{key}' est un objet, clés : {list(value.keys())}")
            else:
                print("\n⚠️  Pas de champ 'rounds' exploitable au niveau racine du match — "
                      "les données round-par-round (KAST/Clutch/FK:FD) ne semblent pas "
                      "exposées par cet endpoint pour ce match.")

            kills = data.get("kills")
            if isinstance(kills, list) and kills:
                print(f"\n'kills' (liste globale) est présent : {len(kills)} kills.")
                print(f"Clés d'un kill : {list(kills[0].keys())}")
                print(f"Exemple complet du premier kill : {json.dumps(kills[0], indent=2, ensure_ascii=False)}")
            else:
                print("\n⚠️  Pas de champ 'kills' global exploitable.")
        print("=== FIN DIAGNOSTIC ===\n")
    _match_cache[match_id] = data
    return data


def extract_players(match_data: dict):
    """Isole la liste des joueurs quelle que soit la forme exacte de la réponse."""
    if not match_data:
        return []
    players = match_data.get("players")
    if isinstance(players, dict):
        players = players.get("all_players", [])
    return players or []


TRADE_WINDOW_MS = 3000  # fenêtre communément utilisée par les trackers pour créditer un "trade"


def compute_match_round_stats(match_data: dict):
    """Calcule KAST / First Kill / First Death / Clutch pour TOUS les joueurs
    d'un match, en une seule passe, à partir de la structure confirmée :
    - match_data['rounds'][i]['stats'][j'] = {'player': {...}, ...}
    - match_data['kills'] = liste globale avec 'round', 'killer', 'victim',
      'assistants', 'time_in_round_in_ms'
    - match_data['rounds'][i]['winning_team']
    Retourne (dict puuid -> {kast, first_kills, first_deaths, clutches_won}, total_rounds)
    """
    rounds = match_data.get("rounds") or []
    kills = match_data.get("kills") or []
    total_rounds = len(rounds)

    kills_by_round = defaultdict(list)
    for k in kills:
        kills_by_round[k.get("round")].append(k)

    result = defaultdict(lambda: {"kast": 0, "first_kills": 0, "first_deaths": 0, "clutches_won": 0})

    def puuid_of(obj):
        if isinstance(obj, dict):
            return obj.get("puuid")
        return obj  # au cas où ce serait déjà une simple chaîne

    for idx, rnd in enumerate(rounds):
        round_kills = sorted(kills_by_round.get(idx, []), key=lambda k: k.get("time_in_round_in_ms", 0))

        participants = {}  # puuid -> team
        for s in (rnd.get("stats") or []):
            p = s.get("player") or {}
            pu = p.get("puuid")
            if pu:
                participants[pu] = p.get("team")

        alive = {pu: True for pu in participants}
        team_alive = defaultdict(int)
        for pu, team in participants.items():
            team_alive[team] += 1

        killed_set, assisted_set, got_kill_set = set(), set(), set()
        death_events = []  # (time_ms, victim, killer)
        clutch_candidate = None  # (puuid_seul, nb_adversaires_restants)

        for k in round_kills:
            killer = puuid_of(k.get("killer"))
            victim = puuid_of(k.get("victim"))
            t = k.get("time_in_round_in_ms", 0)

            if killer:
                got_kill_set.add(killer)
            if victim:
                killed_set.add(victim)
            for a in (k.get("assistants") or []):
                au = puuid_of(a)
                if au:
                    assisted_set.add(au)

            death_events.append((t, victim, killer))

            if victim in alive and alive[victim]:
                alive[victim] = False
                vteam = participants.get(victim)
                if vteam is not None:
                    team_alive[vteam] -= 1

            teams_present = [tm for tm in team_alive if tm is not None]
            if len(teams_present) == 2 and clutch_candidate is None:
                t1, t2 = teams_present
                c1, c2 = team_alive[t1], team_alive[t2]
                if c1 == 1 and c2 >= 1:
                    lone = next((pu for pu, al in alive.items() if al and participants.get(pu) == t1), None)
                    if lone:
                        clutch_candidate = (lone, c2)
                elif c2 == 1 and c1 >= 1:
                    lone = next((pu for pu, al in alive.items() if al and participants.get(pu) == t2), None)
                    if lone:
                        clutch_candidate = (lone, c1)

        # Détection des trades : une mort est "vengée" si un coéquipier tue
        # l'auteur du kill (le tueur d'origine se fait tuer à son tour) dans
        # la fenêtre de temps suivante, ce même round.
        traded_set = set()
        for i, (t, victim, killer) in enumerate(death_events):
            if not victim or not killer:
                continue
            vteam = participants.get(victim)
            for t2, victim2, killer2 in death_events[i + 1:]:
                if t2 - t > TRADE_WINDOW_MS:
                    break
                if victim2 == killer and participants.get(killer2) == vteam:
                    traded_set.add(victim)
                    break

        if round_kills:
            fk = puuid_of(round_kills[0].get("killer"))
            fd = puuid_of(round_kills[0].get("victim"))
            if fk:
                result[fk]["first_kills"] += 1
            if fd:
                result[fd]["first_deaths"] += 1

        for pu in participants:
            credited = (pu in got_kill_set) or (pu in assisted_set) or (pu not in killed_set) or (pu in traded_set)
            if credited:
                result[pu]["kast"] += 1

        if clutch_candidate:
            lone_puuid, _deficit = clutch_candidate
            lone_team = participants.get(lone_puuid)
            if rnd.get("winning_team") == lone_team:
                result[lone_puuid]["clutches_won"] += 1

    return result, total_rounds


def extract_damage_made(s: dict):
    """Le champ dégâts peut être un nombre brut ('damage_made') ou un objet
    imbriqué ('damage': {'made': X, 'received': Y}) selon la forme exacte
    renvoyée par l'API pour ce match."""
    dm = s.get("damage_made")
    if isinstance(dm, (int, float)):
        return dm
    dmg = s.get("damage")
    if isinstance(dmg, dict):
        return dmg.get("made") or dmg.get("dealt") or dmg.get("done") or 0
    if isinstance(dmg, (int, float)):
        return dmg
    return 0


def rounds_played_in_match(match_data: dict) -> int:
    teams = match_data.get("teams") or {}
    if isinstance(teams, dict):
        for side in teams.values():
            if isinstance(side, dict) and "rounds_won" in side:
                return (side.get("rounds_won") or 0) + (side.get("rounds_lost") or 0)
    rounds = match_data.get("rounds")
    if isinstance(rounds, list):
        return len(rounds)
    return 0


def aggregate_team_season(team: dict, api_key: str, player_agg: dict, debug_match: bool = False):
    """Parcourt tous les matchs de la saison d'une équipe et cumule les stats
    individuelles dans player_agg (dict partagé, clé = puuid)."""
    team_id = team.get("id") or team.get("team_id")
    if not team_id:
        return
    matches = fetch_team_history(team_id, api_key)
    team["matches_played"] = len(matches)
    roster_puuids = {m["puuid"] for m in team.get("roster_resolved", [])}

    for m in matches:
        match_id = m.get("id")
        if not match_id:
            continue
        data = fetch_match(match_id, api_key, debug_match=debug_match)
        if not data:
            continue
        rounds_total = rounds_played_in_match(data)
        round_stats_by_puuid, _ = compute_match_round_stats(data)

        for p in extract_players(data):
            try:
                puuid = p.get("puuid")
                if roster_puuids and puuid not in roster_puuids:
                    continue  # on ne compte que les joueurs de CETTE équipe pour éviter les doublons adverses
                name = f"{p.get('name')}#{p.get('tag')}" if p.get("name") else puuid
                s = p.get("stats", p)
                agent = (p.get("agent") or {}).get("name") if isinstance(p.get("agent"), dict) else p.get("agent")

                entry = player_agg[puuid]
                entry["name"] = name
                entry["team"] = f"{team.get('name')} #{team.get('tag')}"
                entry["division"] = extract_division_number(team)
                entry.setdefault("agents", defaultdict(int))
                if agent:
                    entry["agents"][agent] += 1
                entry["matches"] += 1
                entry["kills"] += s.get("kills") or 0
                entry["deaths"] += s.get("deaths") or 0
                entry["assists"] += s.get("assists") or 0
                entry["score"] += s.get("score") or 0
                entry["headshots"] += s.get("headshots") or 0
                entry["bodyshots"] += s.get("bodyshots") or 0
                entry["legshots"] += s.get("legshots") or 0
                entry["damage"] += extract_damage_made(s)
                entry["rounds"] += rounds_total

                rl = round_stats_by_puuid.get(puuid)
                if rl:
                    entry["kast_rounds"] += rl["kast"]
                    entry["kast_total_rounds"] += rounds_total
                    entry["first_kills"] += rl["first_kills"]
                    entry["first_deaths"] += rl["first_deaths"]
                    entry["clutches_won"] += rl["clutches_won"]
                    entry["round_level_available"] = True
            except Exception as e:
                # Une anomalie sur UN joueur/UN match ne doit jamais faire perdre
                # tout le run (potentiellement 30-90 min de travail). On logue et
                # on continue avec le joueur suivant.
                print(f"  ⚠️  joueur ignoré (match {match_id}, puuid {p.get('puuid')}) : {e}", file=sys.stderr)
                continue


def finalize_player_stats(player_agg: dict):
    """Calcule les moyennes/ratios finaux à partir des totaux cumulés."""
    players = []
    for puuid, e in player_agg.items():
        rounds = e["rounds"] or 0
        acs = round(e["score"] / rounds, 1) if rounds else None
        adr = round(e["damage"] / rounds, 1) if rounds and e["damage"] else None
        kd = round(e["kills"] / e["deaths"], 2) if e["deaths"] else e["kills"]
        total_shots = e["headshots"] + e["bodyshots"] + e["legshots"]
        hs_pct = round(100 * e["headshots"] / total_shots, 1) if total_shots else None

        kast_pct = None
        fk_fd = None
        clutch_display = None
        if e.get("round_level_available") and e["kast_total_rounds"]:
            kast_pct = round(100 * e["kast_rounds"] / e["kast_total_rounds"], 1)
            fk_fd = f"{e['first_kills']}/{e['first_deaths']}"
            clutch_display = e["clutches_won"]

        # Rating "maison", clairement une ESTIMATION, pas le vrai Rating VLR
        # (formule non publiée par VLR.gg). Pondération inspirée d'approximations
        # communautaires : impact ACS normalisé + KDA + KAST si dispo.
        rating_estimate = None
        if acs is not None:
            kda_component = (e["kills"] + 0.5 * e["assists"] - e["deaths"]) / max(e["matches"], 1)
            kast_component = (kast_pct / 100) if kast_pct is not None else 0.72  # valeur neutre par défaut
            rating_estimate = round(0.005 * acs + 0.4 * kast_component + 0.05 * kda_component, 2)

        agents_sorted = sorted(e.get("agents", {}).items(), key=lambda x: -x[1])[:3]

        players.append({
            "puuid": puuid,
            "name": e["name"],
            "team": e["team"],
            "division": e.get("division"),
            "matches": e["matches"],
            "agents": [a for a, _ in agents_sorted],
            "kills": e["kills"],
            "deaths": e["deaths"],
            "assists": e["assists"],
            "kd": kd,
            "acs": acs,
            "adr": adr,
            "hs_pct": hs_pct,
            "kast_pct": kast_pct,
            "fk_fd": fk_fd,
            "clutches_won": clutch_display,
            "rating_estimate": rating_estimate,
        })

    players.sort(key=lambda p: (p["rating_estimate"] or 0), reverse=True)
    return players


def new_player_entry():
    return {
        "name": None, "team": None, "division": None, "matches": 0,
        "kills": 0, "deaths": 0, "assists": 0, "score": 0,
        "headshots": 0, "bodyshots": 0, "legshots": 0, "damage": 0, "rounds": 0,
        "kast_rounds": 0, "kast_total_rounds": 0, "first_kills": 0, "first_deaths": 0,
        "clutches_won": 0, "round_level_available": False,
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    api_key = os.environ.get("HENRIKDEV_API_KEY")
    if not api_key:
        print("ERREUR : définis HENRIKDEV_API_KEY", file=sys.stderr)
        sys.exit(1)

    debug = "--debug" in sys.argv
    debug_match = "--debug-match" in sys.argv

    france_conference = discover_france_conference(api_key)
    teams = fetch_conference_leaderboard(france_conference, api_key, debug=debug)

    if debug_match:
        print("\n[--debug-match] Dump du 1er match trouvé pour la 1ère équipe, puis arrêt.")
        if teams:
            history = fetch_team_history(teams[0].get("id") or teams[0].get("team_id"), api_key)
            if history:
                fetch_match(history[0]["id"], api_key, debug_match=True)
        return

    player_agg = defaultdict(new_player_entry)

    print(f"\n{len(teams)} équipes françaises Contender/Invite. Résolution rosters + agrégation saison...")
    for i, team in enumerate(teams, 1):
        print(f"  [{i}/{len(teams)}] {team.get('name', '?')}")
        try:
            resolve_roster(team, api_key)
            aggregate_team_season(team, api_key, player_agg)
        except Exception as e:
            print(f"  ⚠️  équipe ignorée après erreur ({team.get('name', '?')}) : {e}", file=sys.stderr)
            continue

    players = finalize_player_stats(player_agg)

    teams.sort(key=lambda t: -(extract_division_number(t) or 0))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "region": REGION,
                "conference": france_conference,
                "teams": teams,
                "players": players,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n✓ {len(teams)} équipes et {len(players)} joueurs écrits dans {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
