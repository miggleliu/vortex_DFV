#!/usr/bin/env python3
"""Create a three-tab Excel report from KV260 dfv_perf.csv (standard library only).

Usage: python3 dfv_report.py results-all-int/dfv_perf.csv
Raw data is never changed. No FPGA access, SDK, or external Python packages needed.
"""
import argparse
import csv
import math
from pathlib import Path
import re
import warnings
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
ET.register_namespace('', NS)
ET.register_namespace('r', REL)


def node(parent, tag, text=None, **attrs):
    element = ET.SubElement(parent, '{%s}%s' % (NS, tag), attrs)
    if text is not None:
        element.text = str(text)
    return element


def xml(element):
    return ET.tostring(element, encoding='utf-8', xml_declaration=True)


def column(index):
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def short_name(name, test_type):
    # Only remove the fixed zero IDs. Nonzero IDs remain distinguishable.
    name = re.sub(r'(^|_)core0_', r'\1', name)
    name = re.sub(r'_owner0(?=_|$)|_unit0(?=_|$)', '', name)
    name = re.sub(r'^collision_' if test_type == 1 else r'^throttle_', '', name)
    name = re.sub(r'_bank(\d+)', r'_b\1', name)
    return name


def natural_key(value):
    return tuple((1, int(x)) if x.isdigit() else (0, x)
                 for x in re.split(r'(\d+)', value))


def number(value):
    if value is None or str(value).strip() == '':
        return None
    value = str(value).strip()
    try:
        return int(value)
    except ValueError:
        try:
            result = float(value)
            return result if math.isfinite(result) else value
        except ValueError:
            return value


def window_value(row):
    values = list(dict.fromkeys(str(v).strip() for k, v in row.items()
                               if k.endswith('_window_cycles') and v is not None
                               and str(v).strip() != ''))
    if len(values) > 1:
        warnings.warn('Different monitor windows for %s/type%s/%s: %s; all retained'
                      % (row.get('app'), row.get('type'), row.get('combo'), values))
        return ' / '.join(values)
    return number(values[0]) if values else None


def table(rows, test_type):
    base = [('app', 'App'), ('combo', 'Scenario'), ('status', 'Status'),
            ('coverage', 'Coverage'), ('instrs', 'Instructions'),
            ('cycles', 'Cycles'), ('IPC', 'IPC'), (None, 'Window cycles')]
    suffixes = ('_natural', '_dfv', '_total') if test_type == 1 else ('_events', '_threshold')
    dynamic = {k for row in rows for k, v in row.items()
               if k.startswith('core') and k.endswith(suffixes)
               and v is not None and str(v).strip() != ''}
    def metric_order(key):
        monitor, metric = key.rsplit('_', 1)
        return natural_key(monitor), suffixes.index('_' + metric)
    fields = base + [(key, short_name(key, test_type).rsplit('_', 1)[0] + '\n'
                      + key.rsplit('_', 1)[1]) for key in sorted(dynamic, key=metric_order)]
    if len({label for _, label in fields}) != len(fields):
        raise ValueError('Shortened column names are ambiguous; raw CSV remains available')
    # App order follows execution; baseline always starts each app's block.
    apps = list(dict.fromkeys(r.get('app', '') for r in rows))
    ordered = sorted(enumerate(rows), key=lambda pair:
                     (apps.index(pair[1].get('app', '')),
                      pair[1].get('combo') != 'none', pair[0]))
    return fields, [row for _, row in ordered]


def styles():
    root = ET.Element('{%s}styleSheet' % NS)
    fonts = node(root, 'fonts', count='3')
    for bold, color, size in [(False, 'FF243746', '11'), (True, 'FFFFFFFF', '11'),
                              (True, 'FF17365D', '16')]:
        font = node(fonts, 'font')
        node(font, 'sz', val=size); node(font, 'color', rgb=color)
        node(font, 'name', val='Calibri')
        if bold:
            node(font, 'b')
    fills = node(root, 'fills', count='4')
    for pattern, color in [('none', None), ('gray125', None),
                           ('solid', 'FF17365D'), ('solid', 'FFEAF1F8')]:
        fill = node(fills, 'fill'); p = node(fill, 'patternFill', patternType=pattern)
        if color:
            node(p, 'fgColor', rgb=color); node(p, 'bgColor', indexed='64')
    borders = node(root, 'borders', count='2')
    for thick in [False, True]:
        border = node(borders, 'border')
        for side in ['left', 'right', 'top', 'bottom', 'diagonal']:
            edge = node(border, side, **({'style': 'medium'} if thick and side == 'top' else {}))
            if thick and side == 'top':
                node(edge, 'color', rgb='FF17365D')
    masters = node(root, 'cellStyleXfs', count='1')
    node(masters, 'xf', numFmtId='0', fontId='0', fillId='0', borderId='0')
    formats = node(root, 'cellXfs', count='10')
    # 0 default; 1 header; 2 title; 3 text; 4 integer; 5 IPC;
    # 6/7/8 corresponding baseline styles with a bold top border; 9 metadata.
    for font, fill, border, fmt, wrap in [(0,0,0,0,False), (1,2,0,0,True),
            (2,0,0,0,False), (0,0,0,0,True), (0,0,0,3,False),
            (0,0,0,4,False), (0,3,1,0,True), (0,3,1,3,False),
            (0,3,1,4,False), (0,0,0,0,True)]:
        xf = node(formats, 'xf', numFmtId=str(fmt), fontId=str(font),
                  fillId=str(fill), borderId=str(border), xfId='0',
                  applyAlignment='1', applyNumberFormat='1', applyFill='1', applyBorder='1')
        node(xf, 'alignment', vertical='center', wrapText='1' if wrap else '0')
    # Use an explicit four-decimal IPC format instead of Excel's two-decimal default.
    formats[5].set('numFmtId', '164'); formats[8].set('numFmtId', '164')
    numfmts = ET.Element('{%s}numFmts' % NS, {'count': '1'})
    node(numfmts, 'numFmt', numFmtId='164', formatCode='0.0000')
    root.insert(0, numfmts)
    cellstyles = node(root, 'cellStyles', count='1')
    node(cellstyles, 'cellStyle', name='Normal', xfId='0', builtinId='0')
    return xml(root)


def add_cell(row, ref, value, style):
    c = node(row, 'c', r=ref, s=str(style))
    if value is None:
        return
    # Excel stores only 15 significant decimal digits. Preserve large counters as text.
    if isinstance(value, (int, float)) and (not isinstance(value, int) or abs(value) < 10**15):
        node(c, 'v', text=value)
    else:
        c.set('t', 'inlineStr')
        inline = node(c, 'is')
        clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))
        node(inline, 't', text=clean)


def sheet(rows, test_type, commits):
    fields, ordered = table(rows, test_type)
    width = len(fields); last = column(width); end = max(5, 4 + len(ordered))
    root = ET.Element('{%s}worksheet' % NS)
    node(root, 'dimension', ref='A1:%s%d' % (last, end))
    views = node(root, 'sheetViews'); view = node(views, 'sheetView', workbookViewId='0', showGridLines='0')
    node(view, 'pane', xSplit='2', ySplit='4', topLeftCell='C5', activePane='bottomRight', state='frozen')
    node(view, 'selection', pane='bottomRight', activeCell='C5', sqref='C5')
    node(root, 'sheetFormatPr', defaultRowHeight='20')
    cols = node(root, 'cols')
    for i in range(1, width + 1):
        w = {1:15, 2:48, 3:17, 4:19, 5:15, 6:15, 7:12, 8:17}.get(i, 19)
        node(cols, 'col', min=str(i), max=str(i), width=str(w), customWidth='1')
    data = node(root, 'sheetData')
    title = {1:'Collision', 2:'Global throttle', 3:'Bank throttle'}[test_type]
    for i, text in [(1, 'DFV type %d — %s' % (test_type, title)),
                    (2, 'Upstream commit: ' + (', '.join(commits) or 'not recorded')),
                    (3, 'One window per scenario across monitors. Different windows are separated by /; blank means unavailable.')]:
        row = node(data, 'row', r=str(i), ht='28' if i == 1 else '26', customHeight='1')
        add_cell(row, 'A%d' % i, text, 2 if i == 1 else 9)
    row = node(data, 'row', r='4', ht='42', customHeight='1')
    for i, (_, label) in enumerate(fields, 1):
        add_cell(row, '%s4' % column(i), label, 1)
    previous_app = None
    for r, record in enumerate(ordered, 5):
        app = record.get('app', '')
        baseline = record.get('combo') == 'none' or app != previous_app
        previous_app = app
        row = node(data, 'row', r=str(r), ht='42', customHeight='1')
        for i, (key, _) in enumerate(fields, 1):
            value = window_value(record) if key is None else record.get(key)
            numeric = key in ('instrs', 'cycles', 'IPC') or i > 8
            if numeric:
                value = number(value)
            style = (5 if key == 'IPC' else 4) if numeric or isinstance(value, (int,float)) else 3
            if baseline:
                style += 3
            add_cell(row, '%s%d' % (column(i), r), value, style)
    if not ordered:
        row = node(data, 'row', r='5')
        add_cell(row, 'A5', 'No cases recorded for this type', 3)
    node(root, 'autoFilter', ref='A4:%s%d' % (last, max(4, 4+len(ordered))))
    merges = node(root, 'mergeCells', count='3')
    for i in range(1,4):
        node(merges, 'mergeCell', ref='A%d:%s%d' % (i,last,i))
    node(root, 'pageMargins', left='0.25', right='0.25', top='0.5', bottom='0.5', header='0.2', footer='0.2')
    return xml(root)


def write_report(rows, output):
    """Write exactly three tabs. Measurement rows/values are never aggregated."""
    groups = {i: [] for i in (1,2,3)}
    for row in rows:
        test_type = int(row['type'])
        if test_type not in groups:
            raise ValueError('Unknown DFV type %r' % row['type'])
        groups[test_type].append(row)
    commits = sorted({r['upstream_commit'] for r in rows if r.get('upstream_commit')})
    workbook = ET.Element('{%s}workbook' % NS)
    sheets = node(workbook, 'sheets')
    relationships = ET.Element('Relationships', xmlns=PKG)
    content = ET.Element('Types', xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
    ET.SubElement(content, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    ET.SubElement(content, 'Default', Extension='xml', ContentType='application/xml')
    for part, kind in [('workbook.xml','sheet.main'), ('styles.xml','styles')]:
        ET.SubElement(content, 'Override', PartName='/xl/'+part,
                      ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.'+kind+'+xml')
    names = ('Type 1 - Collision', 'Type 2 - Global throttle', 'Type 3 - Bank throttle')
    output = Path(output)
    # Build/validate all sheets before opening output, leaving existing reports intact on error.
    sheet_bytes = [sheet(groups[i], i, commits) for i in (1,2,3)]
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for i, name in enumerate(names,1):
            node(sheets, 'sheet', name=name, sheetId=str(i), **{'{%s}id' % REL:'rId%d' % i})
            ET.SubElement(relationships, 'Relationship', Id='rId%d' % i,
                          Type=REL+'/worksheet', Target='worksheets/sheet%d.xml' % i)
            ET.SubElement(content, 'Override', PartName='/xl/worksheets/sheet%d.xml' % i,
                          ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
            archive.writestr('xl/worksheets/sheet%d.xml' % i, sheet_bytes[i-1])
        ET.SubElement(relationships, 'Relationship', Id='rId4', Type=REL+'/styles', Target='styles.xml')
        rootrels = ET.Element('Relationships', xmlns=PKG)
        ET.SubElement(rootrels, 'Relationship', Id='rId1', Type=REL+'/officeDocument', Target='xl/workbook.xml')
        archive.writestr('_rels/.rels', xml(rootrels))
        archive.writestr('[Content_Types].xml', xml(content))
        archive.writestr('xl/workbook.xml', xml(workbook))
        archive.writestr('xl/_rels/workbook.xml.rels', xml(relationships))
        archive.writestr('xl/styles.xml', styles())
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--out', type=Path, help='Default: input CSV with .xlsx extension')
    args = parser.parse_args()
    output = args.out or args.csv.with_suffix('.xlsx')
    if output.resolve() == args.csv.resolve():
        parser.error('Output must not overwrite the input CSV')
    with args.csv.open(newline='', encoding='utf-8-sig') as source:
        reader = csv.DictReader(source)
        missing = {'app','type','combo'} - set(reader.fieldnames or [])
        if missing:
            parser.error('Missing CSV columns: ' + ', '.join(sorted(missing)))
        rows = list(reader)
    print('Excel report: ' + str(write_report(rows, output)))


if __name__ == '__main__':
    main()
