from dataclasses import dataclass
from typing import Optional

import openpyxl


@dataclass
class TemplateField:
    sheet: str
    coordinate: str
    row: int
    col: int
    value: Optional[str]
    formula: Optional[str]
    has_formula: bool
    is_data_entry: bool

    @property
    def label(self) -> str:
        return self.value or self.formula or ""


def read_template(path: str, sheet: Optional[str] = None) -> list[TemplateField]:
    wb = openpyxl.load_workbook(path, data_only=False)
    sheet_names = [sheet] if sheet else wb.sheetnames
    fields: list[TemplateField] = []

    for name in sheet_names:
        ws = wb[name]
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                is_formula = isinstance(cell.value, str) and cell.value.startswith("=")
                formula = cell.value if is_formula else None
                value = cell.value if not is_formula else None

                fields.append(
                    TemplateField(
                        sheet=name,
                        coordinate=cell.coordinate,
                        row=cell.row,
                        col=cell.column,
                        value=str(value) if value is not None else None,
                        formula=formula,
                        has_formula=is_formula,
                        is_data_entry=not is_formula,
                    )
                )

    return fields
