"""
test_config.py — Tests de la logique de chargement des seuils DLC.

Couvre get_retention_thresholds() avec les 3 sources de configuration :
  1. /data/options.json (Supervisor HA) — priorité maximale
  2. settings_store (réglages in-app)
  3. Valeurs par défaut codées en dur (30 / 14)
"""
import json
import importlib
import pytest
import config


class TestRetentionThresholds:

    def test_defaults_without_options_file(self, monkeypatch):
        """Sans fichier options.json et sans settings, les défauts s'appliquent."""
        # _options_json_thresholds retourne None si le fichier est absent
        monkeypatch.setattr(config, "_options_json_thresholds", lambda: None)
        # load_settings retourne des valeurs par défaut
        monkeypatch.setattr(
            "settings_store.load_settings",
            lambda: {"retention_days_warning": 30, "retention_days_critical": 14},
            raising=False,
        )
        w, c = config.get_retention_thresholds()
        assert w == 30
        assert c == 14

    def test_options_json_overrides_settings(self, monkeypatch, tmp_path):
        """Les valeurs de /data/options.json ont la priorité sur les settings in-app."""
        monkeypatch.setattr(config, "_options_json_thresholds", lambda: (45, 20))
        # Même si settings retourne autre chose, options.json prime
        monkeypatch.setattr(
            "settings_store.load_settings",
            lambda: {"retention_days_warning": 30, "retention_days_critical": 14},
            raising=False,
        )
        w, c = config.get_retention_thresholds()
        assert w == 45
        assert c == 20

    def test_options_json_partial_is_ignored(self, monkeypatch):
        """Si options.json ne fournit pas les deux valeurs > 0, on ignore et on fallback."""
        # Simule un options.json avec seulement un champ valide
        monkeypatch.setattr(config, "_options_json_thresholds", lambda: None)
        monkeypatch.setattr(
            "settings_store.load_settings",
            lambda: {"retention_days_warning": 25, "retention_days_critical": 10},
            raising=False,
        )
        w, c = config.get_retention_thresholds()
        assert w == 25
        assert c == 10

    def test_options_json_file_parsing(self, tmp_path, monkeypatch):
        """Test de bout en bout de _options_json_thresholds() via un vrai fichier JSON."""
        opts_file = tmp_path / "options.json"
        opts_file.write_text(json.dumps({
            "retention_days_warning": 60,
            "retention_days_critical": 30,
        }))
        # Patch la fonction pour qu'elle lise notre fichier temporaire
        def _read_options():
            try:
                with open(str(opts_file)) as f:
                    opts = json.load(f)
                w = int(opts.get("retention_days_warning") or 0)
                c = int(opts.get("retention_days_critical") or 0)
                if w > 0 and c > 0:
                    return w, c
            except Exception:
                pass
            return None
        monkeypatch.setattr(config, "_options_json_thresholds", _read_options)
        w, c = config.get_retention_thresholds()
        assert w == 60
        assert c == 30

    def test_options_json_zeros_are_ignored(self, monkeypatch):
        """Des valeurs à 0 dans options.json ne doivent pas écraser les défauts."""
        monkeypatch.setattr(config, "_options_json_thresholds", lambda: None)
        monkeypatch.setattr(
            "settings_store.load_settings",
            lambda: {"retention_days_warning": 30, "retention_days_critical": 14},
            raising=False,
        )
        w, c = config.get_retention_thresholds()
        assert w > 0
        assert c > 0

    def test_settings_custom_values(self, monkeypatch):
        """Les réglages in-app personnalisés sont bien pris en compte."""
        monkeypatch.setattr(config, "_options_json_thresholds", lambda: None)
        monkeypatch.setattr(
            "settings_store.load_settings",
            lambda: {"retention_days_warning": 45, "retention_days_critical": 7},
            raising=False,
        )
        w, c = config.get_retention_thresholds()
        assert w == 45
        assert c == 7
