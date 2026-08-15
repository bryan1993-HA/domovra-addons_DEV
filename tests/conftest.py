"""
conftest.py — Fixtures partagées pour tous les tests Domovra.

Le module db.py utilise une variable globale DB_PATH qui est lue à chaque
appel de _conn(). On la remplace par un fichier temporaire via monkeypatch
pour isoler complètement les tests de /data/domovra.sqlite3.

On utilise un fichier temporaire (et non :memory:) car db.py ouvre une
nouvelle connexion à chaque appel — chaque connexion à ':memory:' obtient
sa propre base vide, ce qui rendrait init_db() inutile.
"""
import os
import sys
import importlib
import pytest


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """
    Fixture principale : une base SQLite fraîche dans un répertoire temporaire.
    - Patch db.DB_PATH avant tout appel à _conn().
    - Appelle init_db() pour créer le schéma complet (migrations incluses).
    - Recharge le module db si nécessaire pour que DB_PATH soit pris en compte.
    Yields le chemin de la base (rarement utile dans les tests).
    """
    db_file = str(tmp_path / "test_domovra.sqlite3")

    # On patche la variable de module db.DB_PATH (lue par _conn())
    import db
    monkeypatch.setattr(db, "DB_PATH", db_file)

    # Initialise le schéma complet (CREATE TABLE + migrations)
    db.init_db()

    yield db_file
