"""Préférences apprises — cases « Lu » / « Pas important » cochées dans le cockpit.

Le cockpit pousse `data/preferences.json` (poids par mot, acheteur et type d'AO).
Ce module le lit et ajuste le score de chaque marché. Les règles sont les mêmes
que côté cockpit : toute divergence ferait mentir l'un des deux affichages.

Fichier absent ou invalide → aucun effet, jamais d'exception.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

VERSION_ATTENDUE = 1
FACTEUR = 0.75          # part de la moyenne des poids reportée sur le score
LONGUEUR_MIN_MOT = 3    # « TMA », « ERP » comptent ; « de », « la » non. Les mots vides, c'est le cockpit qui ne les exporte pas.
MAX_RAISONS = 3
SCORE_MIN, SCORE_MAX = 0.0, 5.0

_SEPARATEURS = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Preferences:
    """Poids validés, prêts à appliquer."""

    genere_le: str | None = None
    marques: int = 0
    mots: dict[str, float] = field(default_factory=dict)
    acheteurs: dict[str, float] = field(default_factory=dict)
    types: dict[str, float] = field(default_factory=dict)

    @property
    def traits(self) -> int:
        """Nombre de poids retenus, toutes familles confondues."""
        return len(self.mots) + len(self.acheteurs) + len(self.types)


# ── Normalisation ──

def normalize_tokens(texte: str) -> list[str]:
    """Minuscules, accents retirés (NFD sans marques combinantes), découpe sur [^a-z0-9]+."""
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFD", texte.lower()) if not unicodedata.combining(c)
    )
    return [j for j in _SEPARATEURS.split(sans_accents) if j]


def _nombre(valeur: object) -> float | None:
    """Nombre JSON fini (les booléens ne comptent pas), sinon None."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return None
    valeur = float(valeur)
    return valeur if math.isfinite(valeur) else None


def _poids_valides(brut: object) -> dict[str, float]:
    """Garde les poids numériques finis dans [-1, 1] ; tout le reste est ignoré."""
    if not isinstance(brut, dict):
        return {}
    retenus: dict[str, float] = {}
    for cle, valeur in brut.items():
        poids = _nombre(valeur)
        if isinstance(cle, str) and poids is not None and -1.0 <= poids <= 1.0:
            retenus[cle] = poids
    return retenus


# ── Chargement ──

def load_preferences(chemin: str | Path) -> Preferences | None:
    """Lit le fichier du cockpit. None s'il est absent, illisible ou hors contrat."""
    try:
        with open(chemin, encoding="utf-8") as f:
            brut = json.load(f)
    except (OSError, ValueError, RecursionError):
        return None

    if not isinstance(brut, dict) or _nombre(brut.get("version")) != VERSION_ATTENDUE:
        return None
    poids = brut.get("poids")
    if not isinstance(poids, dict):
        return None

    marques = brut.get("marques")
    total_marques = 0
    if isinstance(marques, dict):
        for cle in ("lu", "pas_important"):
            n = _nombre(marques.get(cle))
            if n is not None and n > 0:
                total_marques += int(n)

    genere_le = brut.get("genere_le")
    return Preferences(
        genere_le=genere_le if isinstance(genere_le, str) else None,
        marques=total_marques,
        mots=_poids_valides(poids.get("mots")),
        acheteurs=_poids_valides(poids.get("acheteurs")),
        types=_poids_valides(poids.get("types")),
    )


# ── Application ──

def _texte(valeur: object) -> str:
    return valeur if isinstance(valeur, str) else ""


def _traits_retrouves(market: dict, prefs: Preferences) -> list[tuple[str, str, float]]:
    """Traits du marché présents dans les préférences : (famille, clé, poids)."""
    traits: list[tuple[str, str, float]] = []

    # Mots de l'objet : chaque jeton distinct compte une fois
    vus: set[str] = set()
    for jeton in normalize_tokens(_texte(market.get("objet"))):
        if len(jeton) >= LONGUEUR_MIN_MOT and jeton not in vus and jeton in prefs.mots:
            vus.add(jeton)
            traits.append(("mot", jeton, prefs.mots[jeton]))

    # Acheteur : nom normalisé, jetons rejoints par une espace, correspondance exacte
    acheteur = " ".join(normalize_tokens(_texte(market.get("nomacheteur"))))
    if acheteur and acheteur in prefs.acheteurs:
        traits.append(("acheteur", acheteur, prefs.acheteurs[acheteur]))

    # Type d'AO : tel quel, correspondance exacte
    ao_type = _texte(market.get("ao_type"))
    if ao_type and ao_type in prefs.types:
        traits.append(("type", ao_type, prefs.types[ao_type]))

    return traits


def _raison(famille: str, cle: str, poids: float) -> str:
    """Ex. : mot « tma » +0.60, acheteur « asnr fontenay » −0.50."""
    signe = "−" if poids < 0 else "+"
    return f"{famille} « {cle} » {signe}{abs(poids):.2f}"


def part_des_poids(poids: list[float]) -> float:
    """Entre -1 et +1 : les goûts corroborés (somme / (nombre + 1)), plus le rejet le plus net, entier.

    « Pas important » est un geste explicite : un seul trait nettement écarté suffit à faire descendre.
    « Lu » est le geste courant : un goût doit être corroboré par d'autres traits pour faire monter.
    Même règle côté cockpit (`partDesPoids`, src/domain/veille/Preferences.ts).
    """
    aimes = [w for w in poids if w > 0]
    ecartes = [w for w in poids if w < 0]
    part = (sum(aimes) / (len(aimes) + 1) if aimes else 0.0) + (min(ecartes) if ecartes else 0.0)
    return max(-1.0, min(1.0, part))


def adjust_market(market: dict, prefs: Preferences) -> bool:
    """Ajuste le score d'un marché sur place. Renvoie True si un trait a été retrouvé."""
    score = _nombre(market.get("score"))
    if score is None:
        return False
    traits = _traits_retrouves(market, prefs)
    if not traits:
        return False

    delta = part_des_poids([p for _, _, p in traits]) * FACTEUR
    # Même arrondi que le score final du scorer. Attention : round() de Python arrondit
    # les demi-valeurs au pair (6.5 → 6), là où Math.round de JS monte (6.5 → 7).
    ajuste = round((score + delta) * 2) / 2
    market["score_brut"] = market["score"]
    market["score"] = min(SCORE_MAX, max(SCORE_MIN, ajuste))
    market["preference_ajustement"] = round(delta, 2)
    forts = sorted(traits, key=lambda t: -abs(t[2]))[:MAX_RAISONS]
    market["preference_raisons"] = [_raison(*t) for t in forts]
    return True


def apply_preferences(markets: list[dict], prefs: Preferences | None) -> int:
    """Ajuste chaque marché sur place ; renvoie le nombre de marchés ajustés."""
    if prefs is None or prefs.traits == 0:
        return 0
    return sum(1 for m in markets if adjust_market(m, prefs))
