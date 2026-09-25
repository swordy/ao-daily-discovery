"""Tests des préférences apprises (src/preferences.py) — sans réseau."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from src import scorer
from src.boamp_api import load_config
from src.preferences import (
    Preferences,
    adjust_market,
    apply_preferences,
    load_preferences,
    normalize_tokens,
)


def _ecrire(tmp_path: Path, contenu: object | str) -> Path:
    chemin = tmp_path / "preferences.json"
    texte = contenu if isinstance(contenu, str) else json.dumps(contenu, ensure_ascii=False)
    chemin.write_text(texte, encoding="utf-8")
    return chemin


def _fichier(poids: dict) -> dict:
    return {
        "version": 1,
        "genere_le": "2026-09-26T08:00:00.000Z",
        "source": "cockpit-dashboard — cases Lu / Pas important",
        "marques": {"lu": 12, "pas_important": 7},
        "poids": poids,
    }


def _marche(objet: str = "", acheteur: str = "", ao_type: str = "Autre", score: float = 3.0) -> dict:
    return {"objet": objet, "nomacheteur": acheteur, "ao_type": ao_type, "score": score}


# ── Chargement ──

def test_fichier_absent(tmp_path: Path) -> None:
    assert load_preferences(tmp_path / "absent.json") is None


@pytest.mark.parametrize(
    "contenu",
    [
        "{ pas du json",
        "",
        "[1, 2, 3]",
        json.dumps({"version": 2, "poids": {}}),
        json.dumps({"poids": {"mots": {"tma": 0.5}}}),
        json.dumps({"version": 1, "poids": ["tma"]}),
        json.dumps({"version": True, "poids": {}}),
    ],
)
def test_json_invalide_ou_hors_contrat(tmp_path: Path, contenu: str) -> None:
    assert load_preferences(_ecrire(tmp_path, contenu)) is None


def test_fichier_valide(tmp_path: Path) -> None:
    prefs = load_preferences(_ecrire(tmp_path, _fichier({
        "mots": {"tma": 0.6, "infogerance": -0.75},
        "acheteurs": {"asnr fontenay": -0.5},
        "types": {"TMA / Maintenance applicative": 0.5},
    })))
    assert prefs is not None
    assert prefs.genere_le == "2026-09-26T08:00:00.000Z"
    assert prefs.marques == 19
    assert prefs.traits == 4


def test_poids_hors_bornes_ou_non_numeriques_ignores(tmp_path: Path) -> None:
    texte = json.dumps(_fichier({
        "mots": {
            "garde": 1, "garde2": -1, "trop": 1.5, "tropbas": -2, "texte": "0.5",
            "booleen": True, "nul": None, "liste": [0.5],
        },
        "acheteurs": "pas un objet",
    }))
    # NaN / Infinity : acceptés par json.load de Python, mais pas des poids finis
    texte = texte.replace('"nul": null', '"nul": null, "nan": NaN, "inf": Infinity')
    prefs = load_preferences(_ecrire(tmp_path, texte))
    assert prefs is not None
    assert prefs.mots == {"garde": 1.0, "garde2": -1.0}
    assert prefs.acheteurs == {}
    assert prefs.traits == 2


# ── Normalisation et traits ──

def test_normalisation() -> None:
    assert normalize_tokens("Infogérance — Système d'Information (lot n°2)") == [
        "infogerance", "systeme", "d", "information", "lot", "n", "2",
    ]


def test_mot_avec_accent() -> None:
    prefs = Preferences(mots={"infogerance": -0.8})
    m = _marche(objet="Infogérance IA", score=3.0)
    assert adjust_market(m, prefs)
    assert m["preference_ajustement"] == -0.6
    assert m["score"] == 2.5
    assert m["preference_raisons"] == ["mot « infogerance » −0.80"]


def test_mots_courts_ignores_et_jeton_distinct_compte_une_fois() -> None:
    prefs = Preferences(mots={"tma": 1.0, "maintenance": 0.4, "applicative": 0.8})
    m = _marche(objet="TMA : maintenance, maintenance applicative", score=3.0)
    assert adjust_market(m, prefs)
    # « tma » (3 lettres) ignoré ; « maintenance » compté une seule fois : (0.4 + 0.8) / 2
    assert m["preference_ajustement"] == round(0.6 * 0.75, 2)


def test_acheteur() -> None:
    prefs = Preferences(acheteurs={"asnr fontenay": -0.5})
    m = _marche(acheteur="ASNR  Fontenay", score=3.0)
    assert adjust_market(m, prefs)
    assert m["preference_raisons"] == ["acheteur « asnr fontenay » −0.50"]
    # Correspondance exacte : un acheteur voisin ne compte pas
    assert not adjust_market(_marche(acheteur="ASNR Fontenay-aux-Roses"), prefs)


def test_type() -> None:
    prefs = Preferences(types={"TMA / Maintenance applicative": 0.5})
    m = _marche(ao_type="TMA / Maintenance applicative", score=3.0)
    assert adjust_market(m, prefs)
    assert m["preference_raisons"] == ["type « TMA / Maintenance applicative » +0.50"]
    # Tel quel : pas de normalisation du type
    assert not adjust_market(_marche(ao_type="tma / maintenance applicative"), prefs)


# ── Ajustement ──

def test_moyenne_et_facteur() -> None:
    prefs = Preferences(
        mots={"infogerance": -0.75},
        acheteurs={"asnr fontenay": -0.5},
        types={"Autre": 0.1},
    )
    m = _marche(objet="Infogérance IA", acheteur="ASNR FONTENAY", ao_type="Autre", score=3.5)
    assert adjust_market(m, prefs)
    delta = (-0.75 - 0.5 + 0.1) / 3 * 0.75  # -0.2875
    assert m["score_brut"] == 3.5
    assert m["preference_ajustement"] == round(delta, 2)
    assert m["score"] == round((3.5 + delta) * 2) / 2 == 3.0
    # Raisons triées par |poids| décroissant
    assert m["preference_raisons"] == [
        "mot « infogerance » −0.75",
        "acheteur « asnr fontenay » −0.50",
        "type « Autre » +0.10",
    ]


def test_au_plus_trois_raisons() -> None:
    prefs = Preferences(mots={"alpha": 0.1, "bravo": -0.9, "charlie": 0.5, "delta": 0.3})
    m = _marche(objet="alpha bravo charlie delta")
    assert adjust_market(m, prefs)
    assert m["preference_raisons"] == [
        "mot « bravo » −0.90", "mot « charlie » +0.50", "mot « delta » +0.30",
    ]


@pytest.mark.parametrize(("score", "poids", "attendu"), [(5.0, 1.0, 5.0), (0.0, -1.0, 0.0), (4.5, 1.0, 5.0)])
def test_score_borne(score: float, poids: float, attendu: float) -> None:
    m = _marche(objet="infogerance", score=score)
    assert adjust_market(m, Preferences(mots={"infogerance": poids}))
    assert m["score"] == attendu
    assert m["preference_ajustement"] == round(poids * 0.75, 2)


def test_marche_sans_trait_inchange() -> None:
    prefs = Preferences(mots={"infogerance": -0.75})
    m = _marche(objet="Développement Java", acheteur="Ville de Lyon", score=4.0)
    avant = dict(m)
    assert apply_preferences([m], prefs) == 0
    assert m == avant
    assert "score_brut" not in m


def test_sans_preferences_aucun_effet() -> None:
    m = _marche(objet="infogerance", score=4.0)
    avant = dict(m)
    assert apply_preferences([m], None) == 0
    assert apply_preferences([m], Preferences()) == 0
    assert m == avant


def test_champs_inattendus_sans_exception() -> None:
    prefs = Preferences(mots={"infogerance": 0.5}, acheteurs={"x": 0.5}, types={"Autre": 0.5})
    assert not adjust_market({"objet": None, "nomacheteur": 42, "ao_type": None, "score": 3.0}, prefs)
    assert not adjust_market({"objet": "infogerance"}, prefs)  # pas de score


# ── Tri dans score_all_markets ──

def test_tri_apres_ajustement(monkeypatch: pytest.MonkeyPatch) -> None:
    def faux_score(market: dict, config: dict) -> dict:
        return {"score": market["_score"], "days_left": market["_jours"], "ao_type": "Autre"}

    monkeypatch.setattr(scorer, "score_market", faux_score)
    marches = [
        {"idweb": "A", "objet": "Infogérance serveurs", "_score": 4.0, "_jours": 10},
        {"idweb": "B", "objet": "Développement Java", "_score": 3.5, "_jours": 20},
        {"idweb": "C", "objet": "Développement Python", "_score": 3.5, "_jours": 5},
    ]
    sans, _ = scorer.score_all_markets([dict(m) for m in marches], {})
    assert [m["idweb"] for m in sans] == ["A", "C", "B"]

    prefs = Preferences(mots={"infogerance": -1.0, "java": 1.0})
    avec, _ = scorer.score_all_markets([dict(m) for m in marches], {}, prefs)
    # Arrondi au pair de Python sur les demi-valeurs :
    # A : 4.0 − 0.75 → 3.0 (round(6.5) = 6) ; B : 3.5 + 0.75 → 4.0 (round(8.5) = 8) ; C inchangé
    assert [(m["idweb"], m["score"]) for m in avec] == [("B", 4.0), ("C", 3.5), ("A", 3.0)]
    assert "score_brut" not in avec[1]


def test_score_all_markets_config_reelle() -> None:
    """Deux marchés fictifs minimaux, vraie config, sans réseau."""
    config = load_config()
    echeance = (date.today() + timedelta(days=20)).isoformat() + "T12:00:00+00:00"
    base = {"descripteur_libelle": ["Informatique (prestations de services)"], "datelimitereponse": echeance}
    marches = [
        {**base, "idweb": "A", "objet": "Infogérance du système d'information", "nomacheteur": "ASNR Fontenay"},
        {**base, "idweb": "B", "objet": "TMA des applications métier Java", "nomacheteur": "Ville de Lyon"},
    ]
    sans, filtres = scorer.score_all_markets([dict(m) for m in marches], config)
    assert filtres == 0 and len(sans) == 2
    assert all("score_brut" not in m for m in sans)

    prefs = Preferences(mots={"infogerance": -1.0}, acheteurs={"asnr fontenay": -1.0})
    avec, _ = scorer.score_all_markets([dict(m) for m in marches], config, prefs)
    a_sans = next(m for m in sans if m["idweb"] == "A")
    a_avec = next(m for m in avec if m["idweb"] == "A")
    assert a_avec["score_brut"] == a_sans["score"]
    assert a_avec["score"] == max(0.0, round((a_sans["score"] - 0.75) * 2) / 2)
    assert next(m for m in avec if m["idweb"] == "B")["score"] == next(m for m in sans if m["idweb"] == "B")["score"]
