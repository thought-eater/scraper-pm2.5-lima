"""Contratos del HTML JavaScript consumido por el scraper PM2.5."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import polars as pl


# El punto en `scraper_pm2.5.py` impide importarlo con la sintaxis `import`
# habitual. importlib carga el mismo archivo sin exigir renombrar un entregable.
MODULE_PATH = Path(__file__).parents[2] / "scraper_pm2.5.py"
SPEC = importlib.util.spec_from_file_location("scraper_pm25", MODULE_PATH)
scraper = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scraper)


class ParseSenamhiHtmlTests(unittest.TestCase):
    def test_uses_last_data_array_and_preserves_missing_value(self) -> None:
        html = """
        categories: ['01/01/202500:00:', '01/01/202501:00:'],
        data: [999, 999],
        data: [12.5, null],
        """

        rows = scraper.parse_senamhi_html(html)

        self.assertEqual(rows[0], {"FECHA": 20250101, "HORA": 0, "PM2_5": 12.5})
        self.assertEqual(rows[1], {"FECHA": 20250101, "HORA": 10000, "PM2_5": None})

    def test_skips_calendar_values_that_only_match_the_regex_shape(self) -> None:
        html = """
        categories: ['31/02/202500:00:'],
        data: [0],
        data: [15],
        """

        with self.assertRaisesRegex(ValueError, "timestamps horarios válidos"):
            scraper.parse_senamhi_html(html)

    def test_rejects_mismatched_category_and_value_lengths(self) -> None:
        html = """
        categories: ['01/01/202500:00:', '01/01/202501:00:'],
        data: [0, 0],
        data: [15],
        """

        with self.assertRaisesRegex(ValueError, "distinta longitud"):
            scraper.parse_senamhi_html(html)

    def test_rejects_non_hourly_timestamp(self) -> None:
        html = """
        categories: ['01/01/202501:30:'],
        data: [0],
        data: [15],
        """

        with self.assertRaisesRegex(ValueError, "timestamps horarios válidos"):
            scraper.parse_senamhi_html(html)

    def test_normalizes_nonfinite_and_numeric_sentinels(self) -> None:
        for value in ["NaN", "inf", "-inf", "-999", "9999", "S/D"]:
            with self.subTest(value=value):
                self.assertIsNone(scraper._safe_measurement(value))


class MergeObservationsTests(unittest.TestCase):
    @staticmethod
    def _frame(rows: list[dict]) -> pl.DataFrame:
        return pl.DataFrame(rows).select(scraper.OUTPUT_COLUMNS)

    def test_new_value_wins_and_history_fills_missing_cells(self) -> None:
        fresh = self._frame(
            [
                {
                    "ESTACION": "CAMPO_DE_MARTE",
                    "FECHA": 20250101,
                    "HORA": 0,
                    "LONGITUD": 0.0,
                    "LATITUD": 0.0,
                    "ALTITUD": 0.0,
                    "PM2_5": 20.0,
                    "PROVINCIA": "LIMA",
                    "DISTRITO": "PRUEBA",
                },
                {
                    "ESTACION": "CAMPO_DE_MARTE",
                    "FECHA": 20250101,
                    "HORA": 10000,
                    "LONGITUD": 0.0,
                    "LATITUD": 0.0,
                    "ALTITUD": 0.0,
                    "PM2_5": None,
                    "PROVINCIA": "LIMA",
                    "DISTRITO": "PRUEBA",
                },
            ]
        )
        historical = self._frame(
            [
                {**row, "PM2_5": value}
                for row, value in zip(fresh.to_dicts(), [10.0, 30.0])
            ]
        )

        merged = scraper.fusionar_observaciones(fresh, [historical])

        self.assertEqual(merged["PM2_5"].to_list(), [20.0, 30.0])
        self.assertEqual(merged["LONGITUD"].unique().to_list(), [-77.0432])

    def test_preserves_historical_station_missing_from_new_capture(self) -> None:
        historical = self._frame(
            [
                {
                    "ESTACION": "PUENTE_PIEDRA",
                    "FECHA": 20251225,
                    "HORA": 40000,
                    "LONGITUD": -77.0,
                    "LATITUD": -11.8,
                    "ALTITUD": None,
                    "PM2_5": 158.2,
                    "PROVINCIA": "LIMA",
                    "DISTRITO": "PUENTE_PIEDRA",
                }
            ]
        )

        merged = scraper.fusionar_observaciones(None, [historical])

        self.assertEqual(merged["PM2_5"].to_list(), [158.2])

    def test_collapses_fresh_duplicates_and_keeps_first_non_null_value(self) -> None:
        base = {
            "ESTACION": "CERES",
            "FECHA": 20250819,
            "HORA": 140000,
            "LONGITUD": -76.9485,
            "LATITUD": -12.029,
            "ALTITUD": None,
            "PROVINCIA": "LIMA",
            "DISTRITO": "ATE",
        }
        fresh = self._frame(
            [
                {**base, "PM2_5": None},
                {**base, "PM2_5": 12.0},
                {**base, "PM2_5": 15.0},
            ]
        )

        merged = scraper.fusionar_observaciones(fresh, [])

        self.assertEqual(merged["PM2_5"].to_list(), [12.0])

    def test_invalid_fresh_value_does_not_displace_history(self) -> None:
        base = {
            "ESTACION": "CAMPO_DE_MARTE",
            "FECHA": 20250101,
            "HORA": 0,
            "LONGITUD": -77.0,
            "LATITUD": -12.0,
            "ALTITUD": 117.0,
            "PROVINCIA": "LIMA",
            "DISTRITO": "JESUS_MARIA",
        }
        fresh = self._frame([{**base, "PM2_5": float("nan")}])
        historical = self._frame([{**base, "PM2_5": 25.0}])

        merged = scraper.fusionar_observaciones(fresh, [historical])

        self.assertEqual(merged["PM2_5"].to_list(), [25.0])

    def test_historical_loader_preserves_rows_outside_requested_period(self) -> None:
        historical = self._frame(
            [
                {
                    "ESTACION": "CAMPO_DE_MARTE",
                    "FECHA": date,
                    "HORA": 0,
                    "LONGITUD": -77.0432,
                    "LATITUD": -12.0705,
                    "ALTITUD": 117.0,
                    "PM2_5": 10.0,
                    "PROVINCIA": "LIMA",
                    "DISTRITO": "JESUS_MARIA",
                }
                for date in [20241231, 20250101, 20260701]
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "historical.csv"
            historical.write_csv(path)

            loaded = scraper.cargar_historicos([path])

        self.assertEqual(
            loaded[0]["FECHA"].to_list(), [20241231, 20250101, 20260701]
        )

    def test_merge_existing_flag_is_enabled_by_default_and_can_be_disabled(self) -> None:
        parser = scraper.build_parser()

        self.assertTrue(parser.parse_args([]).fusionar_existente)
        self.assertFalse(parser.parse_args(["--no-fusionar-existente"]).fusionar_existente)

    def test_backs_up_existing_csv_inside_timestamped_history(self) -> None:
        self.assertEqual(scraper.HISTORY_DIR, MODULE_PATH.parent / "history")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "pm25.csv"
            output.write_text("contenido anterior", encoding="utf-8")

            history = root / "history"
            with patch.object(scraper, "HISTORY_DIR", history):
                backup = scraper.respaldar_csv_existente(output)

            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_text(encoding="utf-8"), "contenido anterior")
            self.assertEqual(backup.name, "pm25.csv")
            self.assertEqual(backup.parent.parent, history)
            self.assertRegex(backup.parent.name, r"^\d{8}_\d{6}$")

if __name__ == "__main__":
    unittest.main()
