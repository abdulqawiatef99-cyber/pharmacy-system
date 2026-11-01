"""Build internal + hospital claim workbooks for one Hikma hospital branch.

usage:
  python hikma_claim.py <YYYY-MM> <branch_key> "<branch label>" <pharmacy.xlsx> <hospital.xlsx|.csv|-> <outdir> [advance=10000]

- pharmacy.xlsx / hospital.xlsx: E-Stock "تقرير مبيعات أصناف عميل" exported as "Excel - Data Only (XLSX)".
- hospital.csv (when the hospital store has 1-2 lines, typed from a screenshot):
  code,name,company,qty,balance,price,total_sale,total_cost,margin_pct
- "-" = nothing withdrawn from the hospital store.
See docs/runbooks/hikma-monthly-claim.md
"""
import sys, csv, calendar, openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.comments import Comment

month, key, label, ph_path, hs_path, outdir = sys.argv[1:7]
ADV = float(sys.argv[7]) if len(sys.argv) > 7 else 10000
Y, M = map(int, month.split('-'))
AR_MONTHS = ['يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر']
MNAME, NEXT = AR_MONTHS[M - 1], AR_MONTHS[M % 12]
LAST = calendar.monthrange(Y, M)[1]
PERIOD = f"الفترة: من 01/{M:02d}/{Y} إلى {LAST}/{M:02d}/{Y}"
PHARM = "صيدلية عبد القوي"

def read_pharmacy(p):
    ws = openpyxl.load_workbook(p, data_only=True).active
    hdr = [c.value for c in ws[1]]
    ix = {h: i for i, h in enumerate(hdr)}
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        name = r[ix['إسم الصنف']]
        if not isinstance(r[ix['كمية البيع']], (int, float)) or name is None:
            continue
        rows.append(dict(code=r[ix['ك.الصنف']] if 'ك.الصنف' in ix else '', name=name.strip(),
                         company=r[ix['الشركة']] if 'الشركة' in ix else '',
                         qty=r[ix['كمية البيع']], price=r[ix['سعر البيع']],
                         sale=r[ix['إجمالى س.البيع']], cost=r[ix['إجمالى التكلفة']]))
    return rows

def read_hosp(p):
    if p == '-':
        return []
    if p.lower().endswith('.xlsx'):
        return read_pharmacy(p)
    with open(p, encoding='utf-8') as f:
        return [dict(code=d['code'], name=d['name'], company=d['company'], qty=float(d['qty']),
                     price=float(d['price']), sale=float(d['total_sale']), cost=float(d['total_cost']))
                for d in csv.DictReader(f)]

ph, hs = read_pharmacy(ph_path), read_hosp(hs_path)

F = 'Arial'
thin = Side(style='thin', color='999999')
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
HDR_FILL = PatternFill('solid', fgColor='1F4E78')
SEC_FILL = PatternFill('solid', fgColor='DDEBF7')
TOT_FILL = PatternFill('solid', fgColor='F2F2F2')
KEY_FILL = PatternFill('solid', fgColor='FFF2CC')
NUM = '#,##0.00;(#,##0.00);-'
QTY = '0.00'
C = Alignment(horizontal='center', vertical='center', wrap_text=True)
R = Alignment(horizontal='right', vertical='center')

def setup(ws, title, ncols, widths):
    ws.sheet_view.rightToLeft = True
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    last = openpyxl.utils.get_column_letter(ncols)
    for row, text, size in [(1, PHARM, 14), (2, title, 13), (3, PERIOD, 11)]:
        ws.merge_cells(f'A{row}:{last}{row}')
        c = ws[f'A{row}']; c.value = text
        c.font = Font(name=F, size=size, bold=True); c.alignment = C
    return 5

def header(ws, r, cols):
    for i, h in enumerate(cols, 1):
        c = ws.cell(r, i, h); c.font = Font(name=F, bold=True, color='FFFFFF')
        c.fill = HDR_FILL; c.alignment = C; c.border = BOX

def section_title(ws, r, text, ncols):
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
    c = ws.cell(r, 1, text); c.font = Font(name=F, bold=True, size=12)
    c.fill = SEC_FILL; c.alignment = R

def cell(ws, r, col, v, fmt=None, bold=False, fill=None, blue=False):
    c = ws.cell(r, col, v)
    c.font = Font(name=F, bold=bold, color='0000FF' if blue else '000000')
    c.border = BOX; c.alignment = C
    if fmt: c.number_format = fmt
    if fill: c.fill = fill
    return c

def summary(ws, r, items, ncols):
    """items: (label, value_or_formula, key?)"""
    lab_end = ncols - 1
    refs = {}
    for name, label_text, v, key_row in items:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=lab_end)
        c = ws.cell(r, 1, label_text); c.font = Font(name=F, bold=True)
        c.alignment = R; c.border = BOX
        for k in range(2, lab_end + 1):
            ws.cell(r, k).border = BOX
        vc = cell(ws, r, ncols, v, NUM, bold=True, fill=KEY_FILL if key_row else TOT_FILL,
                  blue=isinstance(v, (int, float)))
        refs[name] = f'{openpyxl.utils.get_column_letter(ncols)}{r}'
        r += 1
    return r, refs

# ---------------- internal ----------------
def build_internal():
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'المطالبة'
    cols = ['م', 'كود الصنف', 'اسم الصنف', 'الشركة', 'الكمية', 'سعر البيع', 'إجمالي البيع',
            'إجمالي التكلفة', 'الربح', 'ن.الربح', 'نصيب المستشفى من الربح', 'نصيب الصيدلية من الربح', 'المطلوب']
    n = len(cols)
    r = setup(ws, f'مطالبة شهر {MNAME} {Y} – {label} (نسخة داخلية – للمراجعة)', n,
              [5, 10, 34, 18, 9, 11, 13, 13, 12, 9, 14, 14, 14])
    tot = {}
    for sec, rows, mode in [('A', ph, 'pharm'), ('B', hs, 'hosp')]:
        t = ('أولاً: أدوية مخزن الصيدلية — المطلوب = التكلفة + نصيب الصيدلية (نص الربح)' if mode == 'pharm'
             else 'ثانياً: أدوية مخزن المستشفى — المطلوب = نصيب الصيدلية (نص الربح) فقط')
        section_title(ws, r, t, n); r += 1
        header(ws, r, cols); r += 1
        first = r
        for i, d in enumerate(rows, 1):
            cell(ws, r, 1, i)
            cell(ws, r, 2, d['code'])
            c = cell(ws, r, 3, d['name']); c.alignment = Alignment(horizontal='left', vertical='center')
            cell(ws, r, 4, d['company'])
            cell(ws, r, 5, d['qty'], QTY, blue=True)
            cell(ws, r, 6, d['price'], NUM, blue=True)
            cell(ws, r, 7, d['sale'], NUM, blue=True)
            cell(ws, r, 8, d['cost'], NUM, blue=True)
            cell(ws, r, 9, f'=G{r}-H{r}', NUM)
            cell(ws, r, 10, f'=IF(G{r}=0,0,I{r}/G{r})', '0.0%')
            cell(ws, r, 11, f'=I{r}/2', NUM)
            cell(ws, r, 12, f'=I{r}-K{r}', NUM)
            cell(ws, r, 13, f'=H{r}+L{r}' if mode == 'pharm' else f'=L{r}', NUM)
            r += 1
        last = r - 1
        if not rows:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=n)
            cell(ws, r, 1, 'لم يتم سحب أدوية من هذا المخزن خلال الفترة'); r += 1
            tot[sec] = '0'
        else:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
            cell(ws, r, 1, f'الإجمالي ({len(rows)} صنف)', bold=True, fill=TOT_FILL)
            for col in range(5, 14):
                L = openpyxl.utils.get_column_letter(col)
                if col == 10:
                    cell(ws, r, col, f'=IF(G{r}=0,0,I{r}/G{r})', '0.0%', bold=True, fill=TOT_FILL)
                elif col in (5, 6):
                    cell(ws, r, col, '', fill=TOT_FILL)
                else:
                    cell(ws, r, col, f'=SUM({L}{first}:{L}{last})', NUM, bold=True, fill=TOT_FILL)
            tot[sec] = r
            r += 1
        r += 1
    def claim(sec, mode):
        if sec not in tot or tot[sec] == '0': return '=0'
        t = tot[sec]
        return f'=G{t}-ROUND(K{t},2)' if mode == 'pharm' else f'=ROUND(L{t},2)'
    section_title(ws, r, 'ملخص المطالبة', n); r += 1
    r, refs = summary(ws, r, [
        ('a', 'مستحقات أدوية مخزن الصيدلية (التكلفة + نص الربح)', claim('A','pharm'), False),
        ('b', 'مستحقات أدوية مخزن المستشفى (نص الربح)', claim('B','hosp'), False),
        ('c', f'إجمالي مستحقات شهر {MNAME}', None, False),
        ('d', f'يُخصم: رصيد تحت الحساب (دفعة {MNAME} المقدمة)', ADV, False),
        ('e', f'صافي مستحقات شهر {MNAME}', None, False),
        ('f', f'يُضاف: رصيد تحت الحساب لشهر {NEXT} (دفعة مقدمة)', ADV, False),
        ('g', 'إجمالي المطلوب من المستشفى', None, True),
    ], n)
    ws[refs['c']] = f'={refs["a"]}+{refs["b"]}'
    ws[refs['e']] = f'={refs["c"]}-{refs["d"]}'
    ws[refs['f']].value = ADV
    ws[refs['g']] = f'={refs["e"]}+{refs["f"]}'
    ws[refs['d']].comment = Comment('دفعة مقدمة ثابتة 10,000 لكل فرع شهرياً (حسب كلام صاحب الصيدلية). عدّلها لو اتغيرت.', 'Claude')
    ws[refs['f']].comment = Comment(f'دفعة {NEXT} المقدمة 10,000 (حسب الاتفاق). عدّلها لو اتغيرت.', 'Claude')
    r += 1
    notes = ['ملاحظات للمراجعة:',
             '• الأرقام باللون الأزرق منقولة كما هي من تقرير "مبيعات أصناف عميل" في E-Stock؛ الأسود معادلات.',
             '• نصيب المستشفى من الربح = (إجمالي البيع − إجمالي التكلفة) ÷ 2.',
             '• المرتجعات: لم تُخصم في هذه النسخة — يُراجع تقرير "مرتجع المبيعات عن فترة" قبل الاعتماد.']
    for t in notes:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=n)
        c = ws.cell(r, 1, t); c.font = Font(name=F, bold=t.endswith(':'), size=10); c.alignment = R
        r += 1
    ws.freeze_panes = 'A5'
    ws.print_options.horizontalCentered = True
    ws.page_setup.orientation = 'landscape'; ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    p = f'{outdir}/{key}-{month}-INTERNAL.xlsx'; wb.save(p); return p

# ---------------- hospital ----------------
def build_hospital():
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'المطالبة'
    cols = ['م', 'اسم الصنف', 'الكمية', 'سعر الجمهور', 'الإجمالي']
    n = len(cols)
    r = setup(ws, f'مطالبة شهر {MNAME} {Y} – {label}', n, [6, 40, 11, 15, 16])
    ph_sale = sum(d['sale'] for d in ph)
    ph_share = round(sum((d['sale'] - d['cost']) / 2 for d in ph), 2)
    hs_fee = round(sum((d['sale'] - d['cost']) / 2 for d in hs), 2)
    tot = {}
    for sec, rows, t in [('A', ph, 'أولاً: أدوية مخزن الصيدلية'), ('B', hs, 'ثانياً: أدوية مخزن المستشفى')]:
        section_title(ws, r, t, n); r += 1
        if not rows:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=n)
            cell(ws, r, 1, 'لم يتم سحب أدوية من مخزن المستشفى خلال الفترة'); r += 2
            continue
        header(ws, r, cols); r += 1
        first = r
        for i, d in enumerate(rows, 1):
            cell(ws, r, 1, i)
            c = cell(ws, r, 2, d['name']); c.alignment = Alignment(horizontal='left', vertical='center')
            cell(ws, r, 3, d['qty'], QTY)
            cell(ws, r, 4, d['price'], NUM)
            cell(ws, r, 5, d['sale'], NUM)
            r += 1
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        cell(ws, r, 1, f'الإجمالي ({len(rows)} صنف)', bold=True, fill=TOT_FILL)
        cell(ws, r, 5, f'=SUM(E{first}:E{r-1})', NUM, bold=True, fill=TOT_FILL)
        tot[sec] = f'E{r}'
        r += 2
    section_title(ws, r, 'ملخص المطالبة', n); r += 1
    items = [('a', 'إجمالي مسحوبات مخزن الصيدلية بسعر الجمهور', f'={tot["A"]}', False),
             ('b', 'يُخصم: نصيب المستشفى من الربح', ph_share, False),
             ('c', 'صافي مستحقات أدوية مخزن الصيدلية', None, False)]
    if hs:
        items.append(('h', 'يُضاف: مقابل صرف أدوية مخزن المستشفى', hs_fee, False))
    items += [('e', f'إجمالي مستحقات شهر {MNAME}', None, False),
              ('d', f'يُخصم: رصيد تحت الحساب (دفعة {MNAME} المقدمة)', ADV, False),
              ('f', f'صافي مستحقات شهر {MNAME}', None, False),
              ('n', f'يُضاف: رصيد تحت الحساب لشهر {NEXT}', ADV, False),
              ('g', 'إجمالي المطلوب', None, True)]
    r, refs = summary(ws, r, items, n)
    ws[refs['c']] = f'={refs["a"]}-{refs["b"]}'
    ws[refs['e']] = f'={refs["c"]}+{refs["h"]}' if hs else f'={refs["c"]}'
    ws[refs['f']] = f'={refs["e"]}-{refs["d"]}'
    ws[refs['g']] = f'={refs["f"]}+{refs["n"]}'
    for k, v in refs.items():  # hospital copy: all black text
        ws[v].font = Font(name=F, bold=True)
    ws.page_setup.orientation = 'portrait'; ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    p = f'{outdir}/{key}-{month}-HOSPITAL.xlsx'; wb.save(p); return p

print(build_internal()); print(build_hospital())
