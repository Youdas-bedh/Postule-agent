"""Conservative location classification for the France-focused job pipeline."""
from __future__ import annotations

import re
import unicodedata


_FR_COUNTRIES = {"fr", "fra", "france", "france metropolitaine", "metropolitan france"}
_PLACE_NAMES = {
    # French regions and commonly returned French job locations.
    "auvergne rhone alpes", "bourgogne franche comte", "bretagne", "centre val de loire",
    "corse", "grand est", "hauts de france", "ile de france", "normandie", "nouvelle aquitaine",
    "occitanie", "pays de la loire", "provence alpes cote d azur",
    "aix en provence", "amiens", "angers", "annecy", "avignon", "bayonne", "besancon", "bordeaux",
    "boulogne billancourt", "brest", "caen", "cergy", "chambery", "clermont ferrand", "dijon",
    "grenoble", "la rochelle", "le havre", "le mans", "lille", "limoges", "lyon", "marseille",
    "metz", "montpellier", "montreuil", "mulhouse", "nancy", "nantes", "nice", "nimes", "orleans",
    "paris", "perpignan", "poitiers", "reims", "rennes", "rouen", "saint denis", "saint etienne",
    "strasbourg", "toulon", "toulouse", "tours", "versailles", "villeurbanne",
}


def _normalize(value: str | None) -> str:
    folded = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.casefold()).strip()


def is_france_job(country: str | None, location: str | None) -> bool:
    """Return True only for an explicit France country or a known French place.

    Unknown and generic remote locations are excluded so the job pipeline does
    not silently suggest roles abroad. An explicit non-France country wins over
    a coincidental city-name match.
    """
    normalized_country = _normalize(country)
    location_text = _normalize(location)
    if normalized_country:
        if normalized_country in _FR_COUNTRIES:
            return True
        if normalized_country not in {"remote", "hybrid", "onsite", "unknown", "multiple locations"}:
            return False
    if "france" in location_text or "france metropolitaine" in location_text:
        return True
    return any(re.search(rf"(?<![a-z0-9]){re.escape(place)}(?![a-z0-9])", location_text) for place in _PLACE_NAMES)
