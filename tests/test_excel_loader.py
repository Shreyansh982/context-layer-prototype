from pathlib import Path
import unittest

from app.data.excel_loader import ExcelLoader


class ExcelLoaderTests(unittest.TestCase):
    def setUp(self):
        workbook = Path(__file__).resolve().parents[1] / "data" / "knowledge_base.xlsx"
        self.loader = ExcelLoader(workbook)

    def test_exposes_loaded_sheets(self):
        self.assertTrue(self.loader.get_sheet_names())

    def test_exposes_columns_for_existing_sheet(self):
        sheet_name = self.loader.get_sheet_names()[0]
        self.assertTrue(self.loader.get_columns(sheet_name))

    def test_unknown_sheet_has_no_columns(self):
        self.assertEqual(self.loader.get_columns("missing"), [])


if __name__ == "__main__":
    unittest.main()
