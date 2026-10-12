#!/usr/bin/env python3
"""
trader_simule.py — Trader SIMULÉ sur les marchés de prédiction Polymarket.

Aucun pari réel : le script lit uniquement des API publiques (Gamma pour les
marchés, CLOB pour les carnets d'ordres), ne se connecte à aucun compte et ne
place aucun ordre. La cagnotte est fictive (50 $ au départ).

Ce qu'il fait toutes les 10 minutes
  1. Récupère jusqu'à 1 000 marchés actifs, écarte ceux qui finissent dans
     moins de 2 h ou ont moins de 5 000 $ de liquidité.
  2. Lit les règles de résolution en entier et identifie la source officielle
     (sinon : marché ignoré, définitivement).
  3. Cherche sur X ce qui se dit (si X_BEARER_TOKEN est défini), pondère par
     crédibilité du compte et fraîcheur, écarte promotion et comptes automatisés.
  4. Fait estimer une probabilité par Claude (sans lui montrer le prix du
     marché), avec recherche web pour vérifier les données publiques.
  5. Garde les marchés où l'écart probabilité / prix dépasse 8 points.
  6. Vérifie dans le carnet que toute la position se revendrait immédiatement
     avec 3 % de perte au maximum.
  7. Mise = Kelly ÷ 2, plafonnée à 6 % de la cagnotte ; 5 paris ouverts maximum.
  8. Note chaque entrée dans paris_script.txt AVANT de l'enregistrer.
  9. Revoit les paris ouverts : sortie si l'écart s'est refermé, si la
     probabilité a bougé de plus de 5 points, ou si le marché finit dans l'heure.
  Arrêt complet (liquidation fictive + bilan + alerte) si la cagnotte perd
  40 % depuis son plus haut, ou tombe à 0.

Installation
  pip install requests

Variables d'environnement (à définir toi-même, le script ne les demande pas)
  ANTHROPIC_API_KEY   obligatoire — estimation des probabilités
  X_BEARER_TOKEN      facultatif — sans lui, l'étape X est sautée et notée
  MODELE_CLAUDE       facultatif — défaut : claude-sonnet-5-5
  ANALYSES_MAX        facultatif — nouvelles analyses max par cycle (défaut : 20)
  DOSSIER_TRADER      facultatif — où écrire journal et état (défaut : dossier du script)

Usage
  python3 trader_simule.py            # boucle toutes les 10 minutes
  python3 trader_simule.py --une-fois # un seul cycle (pour cron / planificateur)
  python3 trader_simule.py --bilan    # écrit un bilan sans trader

Coût : chaque analyse = 2 appels à Claude (+ recherche web). Le script limite
les nouvelles analyses par cycle (analyses_max_par_cycle) et réutilise une
analyse pendant 6 h tant que le prix n'a pas bougé de plus de 3 points.
"""

import argparse
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
X_API = "https://api.twitter.com/2/tweets/search/recent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

CONFIG = {
    "cagnotte_initiale": 50.0,
    "intervalle_s": 600,          # 10 minutes
    "max_marches": 1000,
    "echeance_min_h": 2,
    "liquidite_min": 5000,
    "ecart_min": 0.08,            # 8 points
    "perte_revente_max": 0.03,    # 3 %
    "kelly_fraction": 0.5,
    "plafond_mise": 0.06,         # 6 % de la cagnotte
    "max_paris": 5,
    "mise_min": 1.0,              # en dessous, on ne joue pas
    "sortie_ecart_ferme": 0.02,   # écart résiduel sous lequel on considère l'écart refermé
    "sortie_proba_bouge": 0.05,   # 5 points
    "sortie_echeance_h": 1,
    "arret_drawdown": 0.40,       # 40 % depuis le plus haut
    "analyses_max_par_cycle": int(os.environ.get("ANALYSES_MAX") or 20),
    "cache_analyse_h": 6,
    "cache_ecart_prix": 0.03,
    "prix_min_considere": 0.04,   # les quasi-certitudes sont regardées en dernier
    "prix_max_considere": 0.96,
    "modele": os.environ.get("MODELE_CLAUDE") or "claude-sonnet-5-5",
    "recherche_web": True,
}

DOSSIER = Path(os.environ.get("DOSSIER_TRADER") or Path(__file__).resolve().parent)
JOURNAL = DOSSIER / "paris_script.txt"
ETAT = DOSSIER / "etat_script.json"

S = requests.Session()
S.headers["User-Agent"] = "trader-simule/1.0 (simulation, aucun ordre)"


# ───────────────────────── utilitaires ─────────────────────────

def maintenant():
    return dt.datetime.now(dt.timezone.utc)


def iso(d):
    return d.isoformat() if d else None


def parse_date(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def jsonish(v, defaut):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return defaut
    return defaut


def pct(x):
    return f"{x * 100:.1f} %"


def dollars(x):
    return f"{x:,.2f} $".replace(",", " ")


def journal(texte):
    horo = maintenant().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(f"\n[{horo}]\n{texte.rstrip()}\n")
    print(f"[{horo}] {texte.splitlines()[0]}")


def info(texte):
    print(f"  · {texte}")


def notifier(titre, message):
    """Alerte locale : console + notification système si disponible."""
    print(f"\n*** {titre} — {message} ***\n")
    try:
        if sys.platform == "darwin" and shutil.which("osascript"):
            subprocess.run(["osascript", "-e",
                            f'display notification "{message}" with title "{titre}"'],
                           timeout=10)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", titre, message], timeout=10)
    except Exception:
        pass
    (DOSSIER / "ALERTE_ARRET.txt").write_text(f"{titre}\n{message}\n", encoding="utf-8")


def get_json(url, params=None, headers=None, essais=3):
    for i in range(essais):
        try:
            r = S.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 429:
                time.sleep(10 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == essais - 1:
                raise
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"Trop de requêtes refusées : {url}")


# ───────────────────────── état ─────────────────────────

def etat_initial():
    c = CONFIG["cagnotte_initiale"]
    return {"cash": c, "plus_haut": c, "positions": [], "historique": [],
            "cache_regles": {}, "cache_analyses": {}, "arrete": False,
            "cree_le": iso(maintenant()), "cycles": 0}


def charger_etat():
    if ETAT.exists():
        return json.loads(ETAT.read_text(encoding="utf-8"))
    e = etat_initial()
    journal(f"DÉBUT DE LA SIMULATION — cagnotte fictive {dollars(e['cash'])}, "
            f"modèle {CONFIG['modele']}, X {'activé' if os.environ.get('X_BEARER_TOKEN') else 'non configuré'}")
    return e


def sauver_etat(e):
    tmp = ETAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ETAT)


# ───────────────────────── marchés ─────────────────────────

def recuperer_marches():
    marches, offset, page = [], 0, 100
    while len(marches) < CONFIG["max_marches"]:
        lot = get_json(f"{GAMMA}/markets", {
            "active": "true", "closed": "false", "limit": page, "offset": offset,
            "order": "liquidityNum", "ascending": "false"})
        if not lot:
            break
        marches += lot
        offset += len(lot)
        if len(lot) < page:
            break
    return marches[:CONFIG["max_marches"]]


def normaliser(m):
    issues = jsonish(m.get("outcomes"), [])
    prix = jsonish(m.get("outcomePrices"), [])
    jetons = jsonish(m.get("clobTokenIds"), [])
    if len(issues) != 2 or len(prix) != 2 or len(jetons) != 2:
        return None
    if m.get("enableOrderBook") is False:
        return None
    try:
        prix = [float(p) for p in prix]
    except (TypeError, ValueError):
        return None
    return {
        "id": str(m.get("id")),
        "question": m.get("question") or "",
        "regles": m.get("description") or "",
        "source_champ": m.get("resolutionSource") or "",
        "fin": parse_date(m.get("endDate")),
        "liquidite": float(m.get("liquidityNum") or m.get("liquidity") or 0),
        "issues": issues,
        "prix": prix,                 # prix affiché de chaque issue
        "jetons": jetons,
        "slug": m.get("slug") or "",
        "ferme": bool(m.get("closed")),
    }


def eligible(m, now):
    if not m or m["fin"] is None:
        return False
    if m["fin"] - now < dt.timedelta(hours=CONFIG["echeance_min_h"]):
        return False
    return m["liquidite"] >= CONFIG["liquidite_min"]


# ───────────────────────── carnet d'ordres ─────────────────────────

def carnet(jeton):
    b = get_json(f"{CLOB}/book", {"token_id": jeton})
    bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
    return bids, asks


def simuler_achat(asks, montant):
    """Parcourt les offres de vente. Renvoie (parts, coût) ou None si profondeur insuffisante."""
    reste, parts, cout = montant, 0.0, 0.0
    for prix, taille in asks:
        if prix <= 0:
            continue
        dispo = prix * taille
        pris = min(reste, dispo)
        parts += pris / prix
        cout += pris
        reste -= pris
        if reste <= 1e-9:
            return parts, cout
    return None


def simuler_vente(bids, parts):
    """Parcourt les offres d'achat. Renvoie le produit de la vente ou None si profondeur insuffisante."""
    reste, produit = parts, 0.0
    for prix, taille in bids:
        pris = min(reste, taille)
        produit += pris * prix
        reste -= pris
        if reste <= 1e-9:
            return produit
    return None


def milieu(bids, asks):
    if bids and asks:
        return (bids[0][0] + asks[0][0]) / 2
    return None


# ───────────────────────── X ─────────────────────────

RE_PROMO = re.compile(
    r"(#ad\b|#sponsored|#sponsoris|#partenariat|\bsponsored\b|use (my )?code|promo code|code promo|"
    r"giveaway|airdrop|referral|parrainage|link in bio|lien en bio|join (my|our) (discord|telegram)|"
    r"copy ?trad|free signals?|signaux gratuits)", re.I)
RE_BOT = re.compile(r"(\bbot\b|automated|automatique|auto-?post|feed of|flux automatique)", re.I)


def chercher_x(requete):
    """Renvoie (texte formaté pour Claude, nombre retenu, nombre écarté) ou None si X non configuré."""
    jeton = os.environ.get("X_BEARER_TOKEN")
    if not jeton or not requete:
        return None
    params = {
        "query": f"({requete}) -is:retweet -is:nullcast",   # nullcast = posts sponsorisés
        "max_results": 100,
        "tweet.fields": "created_at,public_metrics,author_id,lang",
        "expansions": "author_id",
        "user.fields": "created_at,description,public_metrics,verified,verified_type,username",
    }
    try:
        d = get_json(X_API, params, headers={"Authorization": f"Bearer {jeton}"})
    except Exception as e:
        info(f"X indisponible : {e}")
        return None
    auteurs = {u["id"]: u for u in d.get("includes", {}).get("users", [])}
    now = maintenant()
    retenus, ecartes = [], 0
    for t in d.get("data", []):
        u = auteurs.get(t.get("author_id"), {})
        texte = t.get("text", "")
        m = u.get("public_metrics", {})
        abonnes = m.get("followers_count", 0)
        cree = parse_date(u.get("created_at")) or now
        anciennete_j = max((now - cree).days, 1)
        posts_par_jour = m.get("tweet_count", 0) / anciennete_j
        if (RE_PROMO.search(texte) or RE_BOT.search(u.get("description", "") or "")
                or anciennete_j < 30 or abonnes < 20 or posts_par_jour > 150):
            ecartes += 1
            continue
        age_h = max((now - (parse_date(t.get("created_at")) or now)).total_seconds() / 3600, 0)
        fraicheur = 0.5 ** (age_h / 12)                       # demi-vie 12 h
        credibilite = min(1.0, math.log10(abonnes + 1) / 6)   # ~1 à 1 M d'abonnés
        if u.get("verified_type") in ("business", "government"):
            credibilite = min(1.0, credibilite + 0.3)
        elif u.get("verified"):
            credibilite = min(1.0, credibilite + 0.1)
        retenus.append((credibilite * fraicheur, age_h, abonnes, u.get("username", "?"), texte))
    retenus.sort(reverse=True)
    lignes = [f"[poids {p:.2f} | @{n}, {a:,} abonnés, il y a {h:.0f} h] {txt.replace(chr(10), ' ')}"
              for p, h, a, n, txt in retenus[:25]]
    return "\n".join(lignes) or "(aucun post pertinent retenu)", len(retenus), ecartes


# ───────────────────────── Claude ─────────────────────────

def claude(systeme, message, recherche=False, max_tokens=1500):
    cle = os.environ.get("ANTHROPIC_API_KEY")
    corps = {"model": CONFIG["modele"], "max_tokens": max_tokens, "system": systeme,
             "messages": [{"role": "user", "content": message}]}
    if recherche:
        corps["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    r = S.post(ANTHROPIC_URL, json=corps, timeout=240, headers={
        "x-api-key": cle, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    if r.status_code == 400 and recherche:
        # Outil de recherche indisponible pour ce compte ou ce modèle : on continue sans.
        CONFIG["recherche_web"] = False
        return claude(systeme, message, False, max_tokens)
    r.raise_for_status()
    return "\n".join(b["text"] for b in r.json().get("content", []) if b.get("type") == "text")


def extraire_json(texte):
    blocs = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", texte, re.S)
    candidat = blocs[-1] if blocs else texte[texte.find("{"): texte.rfind("}") + 1]
    return json.loads(candidat)


def examiner_regles(m):
    systeme = ("Tu lis des règles de résolution de marchés de prédiction. "
               "Réponds uniquement par un objet JSON, sans texte autour.")
    message = f"""Question : {m['question']}
Issues : {m['issues']}
Champ « resolutionSource » : {m['source_champ'] or '(vide)'}

Règles de résolution complètes :
\"\"\"
{m['regles']}
\"\"\"

Renvoie :
{{"source_officielle": "<organisme, publication ou donnée précise qui tranche, telle que nommée par les règles>" ou null,
  "requete_x": "<requête de recherche X courte, mots-clés dans la langue de l'événement>",
  "resume_regles": "<une phrase>"}}

source_officielle vaut null si les règles ne désignent aucune source identifiable
(par exemple seulement « un consensus de sources crédibles » sans en nommer aucune)."""
    return extraire_json(claude(systeme, message, max_tokens=600))


def estimer_probabilite(m, source, posts_x):
    systeme = ("Tu es un analyste prudent de marchés de prédiction. Tu estimes la probabilité "
               "que la PREMIÈRE issue l'emporte selon les règles exactes. Le prix du marché ne "
               "t'est volontairement pas donné : ne cherche pas à le deviner, raisonne sur les faits. "
               "Termine par un objet JSON.")
    bloc_x = posts_x if posts_x is not None else "(recherche X non disponible pour ce cycle)"
    message = f"""Date actuelle : {maintenant().strftime('%Y-%m-%d %H:%M UTC')}
Fin du marché : {m['fin'].strftime('%Y-%m-%d %H:%M UTC')}
Question : {m['question']}
Issues : 1re = {m['issues'][0]} ; 2e = {m['issues'][1]}
Source officielle : {source}

Règles complètes :
\"\"\"
{m['regles']}
\"\"\"

Posts X récents (pondérés crédibilité × fraîcheur, promotion et comptes automatisés exclus) :
{bloc_x}

{"Vérifie en priorité les données publiques de la source officielle avec la recherche web." if CONFIG['recherche_web'] else ""}
Termine par :
{{"probabilite": <entre 0 et 1, pour la 1re issue>, "raison": "<2 à 3 phrases>", "sources": ["<url ou nom>"]}}"""
    d = extraire_json(claude(systeme, message, recherche=CONFIG["recherche_web"], max_tokens=2000))
    p = float(d["probabilite"])
    if not 0 <= p <= 1:
        raise ValueError(f"probabilité hors bornes : {p}")
    return p, d.get("raison", ""), d.get("sources", [])


def analyser(m, etat, forcer=False):
    """Renvoie (p_issue0, raison) ou None si le marché est à écarter. Utilise le cache si possible."""
    regles = etat["cache_regles"].get(m["id"])
    if regles is None:
        r = examiner_regles(m)
        regles = {"source": r.get("source_officielle"), "requete_x": r.get("requete_x", "")}
        etat["cache_regles"][m["id"]] = regles
    if not regles["source"]:
        return None
    cache = etat["cache_analyses"].get(m["id"])
    if cache and not forcer:
        age_h = (maintenant() - parse_date(cache["quand"])).total_seconds() / 3600
        if age_h < CONFIG["cache_analyse_h"] and abs(cache["prix"] - m["prix"][0]) < CONFIG["cache_ecart_prix"]:
            return cache["p"], cache["raison"], False
    x = chercher_x(regles["requete_x"])
    posts = x[0] if x else None
    p, raison, sources = estimer_probabilite(m, regles["source"], posts)
    if x:
        raison += f" [X : {x[1]} posts retenus, {x[2]} écartés]"
    if sources:
        raison += f" [sources : {', '.join(map(str, sources[:3]))}]"
    etat["cache_analyses"][m["id"]] = {"p": p, "raison": raison, "prix": m["prix"][0],
                                       "quand": iso(maintenant())}
    return p, raison, True


# ───────────────────────── portefeuille ─────────────────────────

def valeur_position(pos):
    """Valeur de liquidation immédiate (vente au carnet), ou au milieu à défaut."""
    try:
        bids, asks = carnet(pos["jeton"])
    except Exception:
        return pos["mise"], None, None
    v = simuler_vente(bids, pos["parts"])
    if v is None:
        mid = milieu(bids, asks)
        v = pos["parts"] * mid if mid is not None else pos["mise"]
    return v, bids, asks


def valeur_totale(etat):
    return etat["cash"] + sum(valeur_position(p)[0] for p in etat["positions"])


def fermer(etat, pos, produit, motif):
    etat["cash"] += produit
    gain = produit - pos["mise"]
    etat["positions"].remove(pos)
    etat["historique"].append({**pos, "ferme_le": iso(maintenant()), "produit": produit,
                               "gain": gain, "motif": motif})
    journal(f"SORTIE (simulée) — {pos['question']}\n"
            f"Position : {pos['libelle']} | Mise {dollars(pos['mise'])} → {dollars(produit)} "
            f"({'+' if gain >= 0 else ''}{dollars(gain)})\n"
            f"Motif : {motif}\n"
            f"Cagnotte (liquidités) : {dollars(etat['cash'])}")


def revoir_positions(etat):
    now = maintenant()
    for pos in list(etat["positions"]):
        try:
            brut = get_json(f"{GAMMA}/markets/{pos['id']}")
            m = normaliser(brut)
        except Exception as e:
            info(f"revue impossible pour {pos['question'][:60]} : {e}")
            continue
        if m is None:
            continue
        i = pos["issue"]
        if m["ferme"]:
            final = m["prix"][i]
            fermer(etat, pos, pos["parts"] * final, f"marché résolu, issue payée {final:.2f} $ par part")
            continue
        produit, bids, asks = valeur_position(pos)
        if m["fin"] and m["fin"] - now < dt.timedelta(hours=CONFIG["sortie_echeance_h"]):
            if bids and simuler_vente(bids, pos["parts"]) is not None:
                fermer(etat, pos, produit, "le marché se termine dans moins d'une heure")
            else:
                info(f"sortie souhaitée mais carnet trop mince : {pos['question'][:60]}")
            continue
        try:
            a = analyser(m, etat, forcer=True)
        except Exception as e:
            info(f"réanalyse impossible ({e}), position conservée")
            continue
        if a is None:
            continue
        p0, raison, _ = a
        p_issue = p0 if i == 0 else 1 - p0
        mid = milieu(bids or [], asks or [])
        motif = None
        if abs(p_issue - pos["proba_entree"]) > CONFIG["sortie_proba_bouge"]:
            motif = (f"ma probabilité a bougé : {pct(pos['proba_entree'])} → {pct(p_issue)}. {raison}")
        elif mid is not None and p_issue - mid < CONFIG["sortie_ecart_ferme"]:
            motif = f"écart refermé : ma probabilité {pct(p_issue)}, milieu du carnet {pct(mid)}"
        if motif:
            if bids and simuler_vente(bids, pos["parts"]) is not None:
                fermer(etat, pos, produit, motif)
            else:
                info(f"sortie souhaitée mais carnet trop mince, nouvel essai au prochain cycle")


def chercher_entrees(etat, marches):
    places = CONFIG["max_paris"] - len(etat["positions"])
    if places <= 0:
        info("5 paris ouverts : pas de nouvelle entrée")
        return
    detenus = {p["id"] for p in etat["positions"]}
    exclus = {k for k, v in etat["cache_regles"].items() if not v["source"]}
    lo, hi = CONFIG["prix_min_considere"], CONFIG["prix_max_considere"]
    candidats = [m for m in marches if m["id"] not in detenus and m["id"] not in exclus]
    candidats.sort(key=lambda m: (not (lo <= m["prix"][0] <= hi), -m["liquidite"]))
    capital = valeur_totale(etat)
    budget = CONFIG["analyses_max_par_cycle"]
    vus = 0
    for m in candidats:
        if places <= 0:
            break
        if budget <= 0 and m["id"] not in etat["cache_analyses"]:
            continue
        try:
            a = analyser(m, etat)
        except Exception as e:
            info(f"analyse échouée ({m['question'][:50]}) : {e}")
            budget -= 1
            continue
        if a is None:
            continue
        p0, raison, neuve = a
        if neuve:
            budget -= 1
        vus += 1
        prix0 = m["prix"][0]
        if p0 - prix0 > CONFIG["ecart_min"]:
            i, p_issue = 0, p0
        elif prix0 - p0 > CONFIG["ecart_min"]:
            i, p_issue = 1, 1 - p0
        else:
            continue
        prix_issue = m["prix"][i]
        try:
            bids, asks = carnet(m["jetons"][i])
        except Exception as e:
            info(f"carnet illisible ({e})")
            continue
        if not asks:
            continue
        meilleur = asks[0][0]
        if meilleur >= 1 or p_issue <= meilleur:
            continue
        kelly = (p_issue - meilleur) / (1 - meilleur)
        fraction = min(kelly * CONFIG["kelly_fraction"], CONFIG["plafond_mise"])
        mise = min(fraction * capital, etat["cash"])
        if mise < CONFIG["mise_min"]:
            info(f"mise trop faible ({dollars(mise)}) : {m['question'][:60]}")
            continue
        achat = simuler_achat(asks, mise)
        if achat is None:
            info(f"profondeur insuffisante à l'achat : {m['question'][:60]}")
            continue
        parts, cout = achat
        prix_moyen = cout / parts
        if p_issue - prix_moyen <= 0:
            continue
        revente = simuler_vente(bids, parts)
        if revente is None:
            info(f"revente impossible en totalité : {m['question'][:60]}")
            continue
        perte = 1 - revente / cout
        if perte > CONFIG["perte_revente_max"]:
            info(f"revente immédiate à -{pct(perte)} (> 3 %) : {m['question'][:60]}")
            continue

        libelle = str(m["issues"][i])
        condition = (f"écart < {CONFIG['sortie_ecart_ferme'] * 100:.0f} pts entre ma probabilité et le milieu du "
                     f"carnet, OU ma probabilité sort de [{pct(p_issue - 0.05)} ; {pct(p_issue + 0.05)}], "
                     f"OU fin du marché dans moins d'1 h ({(m['fin'] - dt.timedelta(hours=1)).strftime('%Y-%m-%d %H:%M UTC')})")
        # 8. Le journal est écrit AVANT l'enregistrement de la position.
        journal(f"ENTRÉE (simulée) — {m['question']}\n"
                f"Lien : https://polymarket.com/market/{m['slug']}\n"
                f"Position : {libelle}\n"
                f"Ma probabilité : {pct(p_issue)} | Prix affiché : {pct(prix_issue)} | "
                f"Achat moyen simulé : {pct(prix_moyen)}\n"
                f"Mise fictive : {dollars(cout)} (Kelly {pct(kelly)} → ÷2 plafonné : {pct(fraction)} "
                f"de {dollars(capital)})\n"
                f"Revente immédiate estimée : -{pct(perte)}\n"
                f"Condition de sortie : {condition}\n"
                f"Raison : {raison}")
        etat["cash"] -= cout
        etat["positions"].append({
            "id": m["id"], "question": m["question"], "slug": m["slug"], "issue": i,
            "libelle": libelle, "jeton": m["jetons"][i], "parts": parts, "mise": cout,
            "prix_entree": prix_moyen, "proba_entree": p_issue, "fin": iso(m["fin"]),
            "ouvert_le": iso(maintenant())})
        sauver_etat(etat)
        places -= 1
    info(f"{vus} marchés évalués ce cycle, {len(etat['positions'])} paris ouverts")


# ───────────────────────── arrêt et bilan ─────────────────────────

def bilan(etat, titre="BILAN"):
    total = valeur_totale(etat)
    depart = CONFIG["cagnotte_initiale"]
    h = etat["historique"]
    gagnants = [x for x in h if x["gain"] > 0]
    lignes = [f"{titre}",
              f"Valeur : {dollars(total)} (départ {dollars(depart)}, "
              f"{'+' if total >= depart else ''}{pct(total / depart - 1)}) | plus haut {dollars(etat['plus_haut'])}",
              f"Paris clos : {len(h)} dont {len(gagnants)} gagnants | ouverts : {len(etat['positions'])} | "
              f"cycles : {etat['cycles']}"]
    for x in h[-20:]:
        lignes.append(f"  {'+' if x['gain'] >= 0 else ''}{dollars(x['gain'])} — {x['libelle']} — "
                      f"{x['question'][:70]} ({x['motif'][:60]})")
    for p in etat["positions"]:
        lignes.append(f"  ouvert : {p['libelle']} — {p['question'][:70]} (mise {dollars(p['mise'])})")
    journal("\n".join(lignes))


def tout_arreter(etat, raison):
    for pos in list(etat["positions"]):
        produit, bids, _ = valeur_position(pos)
        fermer(etat, pos, produit, f"arrêt général : {raison}")
    etat["arrete"] = True
    sauver_etat(etat)
    bilan(etat, f"ARRÊT DE LA SIMULATION — {raison}")
    notifier("Trader simulé arrêté", raison)


def verifier_arret(etat):
    total = valeur_totale(etat)
    etat["plus_haut"] = max(etat["plus_haut"], total)
    baisse = 1 - total / etat["plus_haut"] if etat["plus_haut"] > 0 else 1
    info(f"valeur {dollars(total)} | plus haut {dollars(etat['plus_haut'])} | baisse {pct(baisse)}")
    if total <= 0.01 or (etat["cash"] < CONFIG["mise_min"] and not etat["positions"]):
        tout_arreter(etat, "cagnotte fictive épuisée")
        return True
    if baisse >= CONFIG["arret_drawdown"]:
        tout_arreter(etat, f"perte de {pct(baisse)} depuis le plus haut ({dollars(etat['plus_haut'])})")
        return True
    return False


# ───────────────────────── boucle ─────────────────────────

def cycle(etat):
    if etat["arrete"]:
        print("Simulation arrêtée. Supprime etat_script.json pour recommencer.")
        return False
    etat["cycles"] += 1
    debut = maintenant()
    print(f"\n── Cycle {etat['cycles']} — {debut.astimezone().strftime('%H:%M:%S')} ──")
    brut = recuperer_marches()
    marches = [m for m in (normaliser(x) for x in brut) if eligible(m, debut)]
    info(f"{len(brut)} marchés récupérés, {len(marches)} retenus (échéance ≥ 2 h, liquidité ≥ 5 000 $)")
    revoir_positions(etat)
    sauver_etat(etat)
    if verifier_arret(etat):
        return False
    chercher_entrees(etat, marches)
    sauver_etat(etat)
    return not verifier_arret(etat)


def main():
    ap = argparse.ArgumentParser(description="Trader simulé Polymarket (aucun pari réel).")
    ap.add_argument("--une-fois", action="store_true", help="un seul cycle puis sortie")
    ap.add_argument("--bilan", action="store_true", help="écrire un bilan et sortir")
    args = ap.parse_args()

    DOSSIER.mkdir(parents=True, exist_ok=True)
    etat = charger_etat()
    if args.bilan:
        bilan(etat)
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY n'est pas défini : impossible d'estimer les probabilités.")
    sauver_etat(etat)
    while True:
        t0 = time.time()
        try:
            continuer = cycle(etat)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"  ! cycle interrompu : {e}")
            continuer = True
        sauver_etat(etat)
        if args.une_fois or not continuer:
            break
        time.sleep(max(0, CONFIG["intervalle_s"] - (time.time() - t0)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompu. L'état est conservé dans etat_script.json.")
