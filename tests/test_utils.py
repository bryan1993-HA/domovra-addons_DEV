"""
test_utils.py — Tests unitaires des utilitaires Domovra.

Couvre :
  - pluralize_fr  : singulier, pluriel, invariants, irréguliers, déjà-pluriel, règles génériques
  - fmt_qty       : conversion g→kg, ml→L, invariants, zero, float
  - _pretty_num   : formatage numérique (entier vs décimal)
"""
import pytest
from utils.jinja import pluralize_fr, fmt_qty, _pretty_num


# ─────────────────────────────────────────────
# pluralize_fr
# ─────────────────────────────────────────────

class TestPluralizeFr:

    # Cas singulier (qty == 1)
    def test_singular_1(self):
        assert pluralize_fr("pièce", 1) == "pièce"

    def test_singular_1_0(self):
        assert pluralize_fr("sachet", 1.0) == "sachet"

    # Pluriel régulier (ajoute 's')
    def test_plural_generic_adds_s(self):
        assert pluralize_fr("pièce", 2) == "pièces"

    def test_plural_qty_zero(self):
        assert pluralize_fr("bouteille", 0) == "bouteilles"

    def test_plural_float(self):
        assert pluralize_fr("sachet", 1.5) == "sachets"

    # Invariants (unités de mesure)
    def test_invariant_kg(self):
        assert pluralize_fr("kg", 5) == "kg"

    def test_invariant_g(self):
        assert pluralize_fr("g", 500) == "g"

    def test_invariant_L(self):
        assert pluralize_fr("L", 3) == "L"

    def test_invariant_ml(self):
        assert pluralize_fr("ml", 200) == "ml"

    def test_invariant_cl(self):
        assert pluralize_fr("cl", 25) == "cl"

    def test_invariant_percent(self):
        assert pluralize_fr("%", 10) == "%"

    # Irréguliers connus
    def test_irregular_piece(self):
        assert pluralize_fr("piece", 3) == "pieces"

    def test_irregular_boite(self):
        assert pluralize_fr("boîte", 2) == "boîtes"

    def test_irregular_oeuf(self):
        assert pluralize_fr("oeuf", 6) == "oeufs"

    def test_irregular_bocal(self):
        assert pluralize_fr("bocal", 2) == "bocaux"

    def test_irregular_pack(self):
        assert pluralize_fr("pack", 3) == "packs"

    # Déjà au pluriel → inchangé
    def test_already_plural_ends_with_s(self):
        assert pluralize_fr("pièces", 3) == "pièces"

    def test_already_plural_ends_with_x(self):
        assert pluralize_fr("bocaux", 3) == "bocaux"

    # Règles génériques : -al → -aux, -eau → -eaux
    def test_al_to_aux(self):
        assert pluralize_fr("journal", 2) == "journaux"

    def test_eau_to_eaux(self):
        assert pluralize_fr("gâteau", 4) == "gâteaux"

    # Unité vide
    def test_empty_unit(self):
        assert pluralize_fr("", 3) == ""


# ─────────────────────────────────────────────
# fmt_qty
# ─────────────────────────────────────────────

class TestFmtQty:

    def test_grams_below_1000_unchanged(self):
        r = fmt_qty(500, "g")
        assert r["v"] == "500"
        assert r["u"] == "g"

    def test_grams_above_1000_converts_to_kg(self):
        """Bug B5 historique : 1000 g était traité comme 1 g. Ce test vérifie la correction."""
        r = fmt_qty(1000, "g")
        assert r["v"] == "1"
        assert r["u"] == "kg"

    def test_grams_1500_to_kg(self):
        r = fmt_qty(1500, "g")
        assert r["v"] == "1.5"
        assert r["u"] == "kg"

    def test_ml_below_1000_unchanged(self):
        r = fmt_qty(500, "ml")
        assert r["v"] == "500"
        assert r["u"] == "ml"

    def test_ml_above_1000_converts_to_L(self):
        r = fmt_qty(1500, "ml")
        assert r["v"] == "1.5"
        assert r["u"] == "L"

    def test_ml_1000_converts_to_1L(self):
        r = fmt_qty(1000, "ml")
        assert r["v"] == "1"
        assert r["u"] == "L"

    def test_kg_unchanged(self):
        r = fmt_qty(2.5, "kg")
        assert r["v"] == "2.5"
        assert r["u"] == "kg"

    def test_L_unchanged(self):
        r = fmt_qty(1.5, "L")
        assert r["v"] == "1.5"
        assert r["u"] == "L"

    def test_lowercase_l_normalized(self):
        """'l' minuscule doit être normalisé en 'L'."""
        r = fmt_qty(2, "l")
        assert r["u"] == "L"

    def test_pcs_unchanged(self):
        r = fmt_qty(3, "pcs")
        assert r["v"] == "3"
        assert r["u"] == "pcs"

    def test_zero_qty(self):
        r = fmt_qty(0, "g")
        assert r["v"] == "0"

    def test_none_qty_treated_as_zero(self):
        r = fmt_qty(None, "kg")
        assert r["v"] == "0"


# ─────────────────────────────────────────────
# _pretty_num
# ─────────────────────────────────────────────

class TestPrettyNum:

    def test_integer_displayed_without_decimal(self):
        assert _pretty_num(3.0) == "3"

    def test_float_trailing_zeros_stripped(self):
        assert _pretty_num(1.50) == "1.5"

    def test_float_keeps_significant_decimals(self):
        assert _pretty_num(1.23) == "1.23"

    def test_zero(self):
        assert _pretty_num(0) == "0"

    def test_non_numeric_passthrough(self):
        assert _pretty_num("abc") == "abc"
