"""
Excel Loader

Loads the enterprise knowledge workbook into memory.

The workbook is loaded only once when the application starts.
"""

import pandas as pd

class ExcelLoader:
    def __init__(self, file_path: str):
        self.file_path = file_path
        # Load every sheet into memory.
        self.sheets = pd.read_excel(
            file_path,
            sheet_name=None
        )

    def get_sheet(self, sheet_name: str):
        return self.sheets.get(sheet_name)

    def get_all_sheets(self):
        return self.sheets

    # CHANGED

    def get_sheet_names(self):
        """
        Returns all sheet names.
        """
        return list(self.sheets.keys())

    def get_columns(self, sheet_name: str):
        """
        Returns all columns of a sheet.
        """
        dataframe = self.get_sheet(sheet_name)

        if dataframe is None:
            return []

        return dataframe.columns.tolist()