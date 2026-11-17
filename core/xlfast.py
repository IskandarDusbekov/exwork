"""Excel'ни фақат ЎҚИШ учун тез очиш (таҳлил, қийматлар): бирлашган катакларнинг чегара чизиқлари тикланмайди.

openpyxl файлни очганда ҳар бир бирлашган катак (``A5:F5``) учун чегара услубларини қайта тузади — 17 000
бирлашмали 20 000 қаторли сметада бу ~10 сония (очишнинг ярмидан кўпи). Лист турини аниқлаш, устунларни топиш,
қийматларни ўқиш учун чегаралар керак эмас. ``MergedCell``'лар эса ҳар доимгидек яратилади (код уларга қарайди).

Фақат шу оқимда (thread) ва ``lean_load`` ичида ишлайди — бошқа оқимда натижа файли учун очилаётган китоб
(чегаралари билан сақланади) бунга тегмайди. Бундай очилган китобни САҚЛАМАНГ.
"""
import threading

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.worksheet._reader import WorksheetReader
from openpyxl.worksheet.cell_range import CellRange, MultiCellRange
from openpyxl.worksheet.merge import MergedCellRange

_state = threading.local()
_orig_bind = WorksheetReader.bind_merged_cells


def _bind_merged_lean(self):
    if not getattr(_state, "lean", False):
        return _orig_bind(self)
    if not self.parser.merged_cells:
        return
    ws, ranges = self.ws, []
    for cr in self.parser.merged_cells.mergeCell:
        mcr = MergedCellRange.__new__(MergedCellRange)
        CellRange.__init__(mcr, range_string=cr.ref)
        mcr.ws = ws
        mcr.start_cell = ws._cells.get((mcr.min_row, mcr.min_col)) or ws.cell(row=mcr.min_row, column=mcr.min_col)
        cells = mcr.cells
        next(cells)  # чап-юқори катак — қиймати шунда
        for row, col in cells:
            ws._cells[row, col] = MergedCell(ws, row, col)
        ranges.append(mcr)
    ws.merged_cells = MultiCellRange(ranges)


WorksheetReader.bind_merged_cells = _bind_merged_lean


def lean_load(path, **kwargs):
    """``load_workbook`` — бирлашган катаклар чегараларисиз (фақат ўқиш учун)."""
    _state.lean = True
    try:
        return load_workbook(path, **kwargs)
    finally:
        _state.lean = False
