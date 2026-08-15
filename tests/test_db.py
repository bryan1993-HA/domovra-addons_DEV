"""
test_db.py — Tests unitaires de la couche données (db.py).

Couvre :
  - Locations  : add, list, duplicate idempotent, update, delete, delete cascade
  - Products   : add, list, duplicate idempotent, update, delete cascade, low_stock
  - Lots       : add, list, consume_lot (partiel + total + lot inexistant), update_lot,
                 delete_lot, status DLC (red/yellow/green/unknown/no_expiry)
  - Insights   : get_product_info (total_qty, FIFO, lots_count)
"""
import datetime
import pytest
import db


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _loc(name="Frigo", **kw):
    return db.add_location(name, **kw)

def _prod(name="Lait", unit="L", shelf=30, **kw):
    return db.add_product(name, unit=unit, shelf=shelf, **kw)

def _lot(product_id, location_id, qty=2.0, best_before=None, frozen_on=None):
    return db.add_lot(product_id, location_id, qty, frozen_on, best_before)

def _future(days):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()

def _past(days):
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


# ─────────────────────────────────────────────
# Locations
# ─────────────────────────────────────────────

class TestLocations:

    def test_add_and_list(self, tmp_db):
        loc_id = _loc("Frigo")
        assert loc_id > 0
        locs = db.list_locations()
        names = [l["name"] for l in locs]
        assert "Frigo" in names

    def test_add_duplicate_returns_same_id(self, tmp_db):
        id1 = _loc("Cave")
        id2 = _loc("Cave")
        assert id1 == id2
        assert len([l for l in db.list_locations() if l["name"] == "Cave"]) == 1

    def test_add_freezer_flag(self, tmp_db):
        _loc("Congélateur", is_freezer=1)
        locs = {l["name"]: l for l in db.list_locations()}
        assert locs["Congélateur"]["is_freezer"] == 1

    def test_update_location(self, tmp_db):
        loc_id = _loc("Ancien nom")
        db.update_location(loc_id, "Nouveau nom", is_freezer=1)
        locs = {l["name"]: l for l in db.list_locations()}
        assert "Nouveau nom" in locs
        assert locs["Nouveau nom"]["is_freezer"] == 1

    def test_delete_location_cascades_lots(self, tmp_db):
        loc_id = _loc("À supprimer")
        prod_id = _prod("Beurre")
        _lot(prod_id, loc_id)
        # Avant suppression, le lot existe
        assert any(l["location_id"] == loc_id for l in db.list_lots())
        db.delete_location(loc_id)
        # Après suppression, plus de lot ni d'emplacement
        locs = [l["name"] for l in db.list_locations()]
        assert "À supprimer" not in locs
        remaining = [l for l in db.list_lots() if l["location_id"] == loc_id]
        assert remaining == []


# ─────────────────────────────────────────────
# Products
# ─────────────────────────────────────────────

class TestProducts:

    def test_add_and_list(self, tmp_db):
        prod_id = _prod("Yaourt")
        assert prod_id > 0
        names = [p["name"] for p in db.list_products()]
        assert "Yaourt" in names

    def test_add_duplicate_returns_same_id(self, tmp_db):
        id1 = _prod("Fromage")
        id2 = _prod("Fromage")
        assert id1 == id2
        assert len([p for p in db.list_products() if p["name"] == "Fromage"]) == 1

    def test_add_with_barcode(self, tmp_db):
        prod_id = db.add_product("Nutella", unit="g", shelf=365, barcode="3017620425035")
        prods = {p["name"]: p for p in db.list_products()}
        assert prods["Nutella"]["barcode"] == "3017620425035"

    def test_add_barcode_duplicate_returns_existing_id(self, tmp_db):
        id1 = db.add_product("Produit A", barcode="1234567890")
        id2 = db.add_product("Produit B", barcode="1234567890")
        # Le deuxième retourne l'id du premier (contrainte UNIQUE sur barcode)
        assert id1 == id2

    def test_update_product(self, tmp_db):
        prod_id = _prod("Huile", unit="L", shelf=180)
        db.update_product(prod_id, "Huile d'olive", "L", 365, min_qty=2.0)
        prods = {p["name"]: p for p in db.list_products()}
        assert "Huile d'olive" in prods
        assert prods["Huile d'olive"]["min_qty"] == 2.0

    def test_delete_product_cascades_lots(self, tmp_db):
        loc_id = _loc()
        prod_id = _prod("Produit à supprimer")
        _lot(prod_id, loc_id)
        assert any(l["product_id"] == prod_id for l in db.list_lots())
        db.delete_product(prod_id)
        assert all(l["product_id"] != prod_id for l in db.list_lots())
        assert all(p["id"] != prod_id for p in db.list_products())

    def test_min_qty_float_or_none(self, tmp_db):
        id1 = db.add_product("P1", min_qty=1.5)
        id2 = db.add_product("P2", min_qty=None)
        prods = {p["id"]: p for p in db.list_products()}
        assert prods[id1]["min_qty"] == 1.5
        assert prods[id2]["min_qty"] is None

    def test_low_stock(self, tmp_db):
        loc_id = _loc()
        # Produit en dessous du seuil
        prod_low = db.add_product("Stock bas", min_qty=5.0)
        _lot(prod_low, loc_id, qty=2.0)
        # Produit au-dessus du seuil
        prod_ok = db.add_product("Stock ok", min_qty=1.0)
        _lot(prod_ok, loc_id, qty=3.0)
        # Produit sans min_qty → ne remonte pas
        prod_none = db.add_product("Sans seuil", min_qty=None)
        _lot(prod_none, loc_id, qty=0.5)

        low = db.list_low_stock_products()
        low_ids = [p["id"] for p in low]
        assert prod_low in low_ids
        assert prod_ok not in low_ids
        assert prod_none not in low_ids


# ─────────────────────────────────────────────
# Lots
# ─────────────────────────────────────────────

class TestLots:

    def test_add_lot_appears_in_list(self, tmp_db):
        loc_id = _loc()
        prod_id = _prod()
        lot_id = _lot(prod_id, loc_id, qty=3.0)
        assert lot_id > 0
        lots = db.list_lots()
        ids = [l["id"] for l in lots]
        assert lot_id in ids

    def test_add_lot_with_best_before(self, tmp_db):
        loc_id = _loc()
        prod_id = _prod()
        bb = _future(30)
        lot_id = _lot(prod_id, loc_id, best_before=bb)
        lots = {l["id"]: l for l in db.list_lots()}
        assert lots[lot_id]["best_before"] == bb

    def test_consume_lot_partial(self, tmp_db):
        loc_id = _loc()
        prod_id = _prod()
        lot_id = _lot(prod_id, loc_id, qty=5.0)
        db.consume_lot(lot_id, 2.0)
        lots = {l["id"]: l for l in db.list_lots()}
        assert lots[lot_id]["qty"] == pytest.approx(3.0)

    def test_consume_lot_full_closes_it(self, tmp_db):
        loc_id = _loc()
        prod_id = _prod()
        lot_id = _lot(prod_id, loc_id, qty=2.0)
        db.consume_lot(lot_id, 2.0)
        # Le lot est soft-deleted (status='empty'), n'apparaît plus dans list_lots
        ids = [l["id"] for l in db.list_lots()]
        assert lot_id not in ids

    def test_consume_lot_overconsume_closes_it(self, tmp_db):
        """Consommer plus que le stock disponible clôture le lot."""
        loc_id = _loc()
        prod_id = _prod()
        lot_id = _lot(prod_id, loc_id, qty=1.0)
        db.consume_lot(lot_id, 999.0)
        ids = [l["id"] for l in db.list_lots()]
        assert lot_id not in ids

    def test_consume_lot_unknown_does_not_crash(self, tmp_db):
        """Un lot_id inexistant → warning loggé mais pas d'exception."""
        db.consume_lot(99999, 1.0)  # ne doit pas lever d'exception

    def test_update_lot_qty_and_location(self, tmp_db):
        loc1 = _loc("Frigo")
        loc2 = _loc("Cave")
        prod_id = _prod()
        lot_id = _lot(prod_id, loc1, qty=4.0)
        db.update_lot(lot_id, 6.0, loc2, None, None)
        lots = {l["id"]: l for l in db.list_lots()}
        assert lots[lot_id]["qty"] == pytest.approx(6.0)
        assert lots[lot_id]["location_id"] == loc2

    def test_update_lot_unknown_does_not_crash(self, tmp_db):
        """update_lot sur un id inexistant → warning loggé, pas d'exception."""
        db.update_lot(99999, 1.0, 1, None, None)

    def test_delete_lot(self, tmp_db):
        loc_id = _loc()
        prod_id = _prod()
        lot_id = _lot(prod_id, loc_id, qty=1.0)
        rows = db.delete_lot(lot_id)
        assert rows == 1
        ids = [l["id"] for l in db.list_lots()]
        assert lot_id not in ids

    def test_multiple_lots_same_product(self, tmp_db):
        loc_id = _loc()
        prod_id = _prod()
        id1 = _lot(prod_id, loc_id, qty=2.0)
        id2 = _lot(prod_id, loc_id, qty=3.0)
        lots = {l["id"]: l for l in db.list_lots()}
        assert id1 in lots
        assert id2 in lots


# ─────────────────────────────────────────────
# status_for — calcul DLC
# ─────────────────────────────────────────────

class TestStatusFor:

    def test_no_best_before_returns_unknown(self):
        assert db.status_for(None, 30, 14) == "unknown"

    def test_empty_string_returns_unknown(self):
        assert db.status_for("", 30, 14) == "unknown"

    def test_invalid_date_returns_unknown(self):
        assert db.status_for("not-a-date", 30, 14) == "unknown"

    def test_expired_returns_red(self):
        past = _past(1)
        assert db.status_for(past, 30, 14) == "red"

    def test_within_critical_returns_red(self):
        soon = _future(10)  # < 14 jours
        assert db.status_for(soon, 30, 14) == "red"

    def test_within_warning_returns_yellow(self):
        mid = _future(20)  # entre 14 et 30 jours
        assert db.status_for(mid, 30, 14) == "yellow"

    def test_beyond_warning_returns_green(self):
        far = _future(60)
        assert db.status_for(far, 30, 14) == "green"

    def test_exactly_on_critical_boundary(self):
        """Le jour J où days == crit_days → red."""
        boundary = _future(14)
        assert db.status_for(boundary, 30, 14) == "red"

    def test_exactly_on_warning_boundary(self):
        """Le jour J où days == warn_days → yellow."""
        boundary = _future(30)
        assert db.status_for(boundary, 30, 14) == "yellow"

    def test_custom_thresholds(self):
        far = _future(50)
        # Avec des seuils larges (60 / 45), ce lot devrait être yellow
        assert db.status_for(far, 60, 45) == "yellow"


# ─────────────────────────────────────────────
# get_product_info
# ─────────────────────────────────────────────

class TestGetProductInfo:

    def test_returns_none_for_unknown_product(self, tmp_db):
        assert db.get_product_info(99999) is None

    def test_total_qty_and_lots_count(self, tmp_db):
        loc_id = _loc()
        prod_id = _prod()
        _lot(prod_id, loc_id, qty=3.0)
        _lot(prod_id, loc_id, qty=2.0)
        info = db.get_product_info(prod_id)
        assert info["total_qty"] == pytest.approx(5.0)
        assert info["lots_count"] == 2

    def test_fifo_is_earliest_best_before(self, tmp_db):
        loc_id = _loc()
        prod_id = _prod()
        bb_near = _future(10)
        bb_far = _future(60)
        _lot(prod_id, loc_id, best_before=bb_far)
        _lot(prod_id, loc_id, best_before=bb_near)
        info = db.get_product_info(prod_id)
        # FIFO = lot avec la DLC la plus proche
        assert info["fifo"]["best_before"] == bb_near

    def test_empty_product_has_zero_qty(self, tmp_db):
        """Un produit sans lots ouverts → total_qty = 0."""
        loc_id = _loc()
        prod_id = _prod("Produit vide")
        lot_id = _lot(prod_id, loc_id, qty=1.0)
        db.consume_lot(lot_id, 1.0)  # lot clôturé
        info = db.get_product_info(prod_id)
        assert info["total_qty"] == 0.0
        assert info["lots_count"] == 0
        assert info["fifo"] is None
