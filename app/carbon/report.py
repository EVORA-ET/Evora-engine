"""PDF report built from the same `footprint()` result the page shows."""

from io import BytesIO

from django.utils import timezone
from evidence.services import today_for
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

FOREST = colors.HexColor('#14422C')
GREEN = colors.HexColor('#2D5A42')
AMBER = colors.HexColor('#D99100')
INK = colors.HexColor('#1B1C1A')
MUTED = colors.HexColor('#5f665f')
CREAM = colors.HexColor('#F5F3EF')
RULE = colors.HexColor('#DDD9D0')

_ss = getSampleStyleSheet()
H1 = ParagraphStyle('h1', parent=_ss['Title'], fontName='Helvetica-Bold',
                    fontSize=20, textColor=FOREST, alignment=TA_LEFT,
                    spaceAfter=2)
H2 = ParagraphStyle('h2', parent=_ss['Heading2'], fontName='Helvetica-Bold',
                    fontSize=12.5, textColor=FOREST, spaceBefore=12,
                    spaceAfter=4)
BODY = ParagraphStyle('body', parent=_ss['BodyText'], fontName='Helvetica',
                      fontSize=9.5, leading=13, textColor=INK)
SMALL = ParagraphStyle('small', parent=BODY, fontSize=8, leading=10.5,
                       textColor=MUTED)
BIG = ParagraphStyle('big', parent=BODY, fontName='Helvetica-Bold',
                     fontSize=22, leading=26, textColor=FOREST)


def fmt_kg(kg):
    if kg is None:
        return 'Not available'
    if abs(kg) >= 1000:
        return f'{kg / 1000:,.2f} t CO2e'
    return f'{kg:,.1f} kg CO2e'


def _p(text, style=BODY):
    return Paragraph(text, style)


def _table(rows, widths, header=True):
    t = Table(rows, colWidths=widths, hAlign='LEFT',
              repeatRows=1 if header else 0)
    style = [
        ('FONT', (0, 0), (-1, -1), 'Helvetica', 8.5),
        ('TEXTCOLOR', (0, 0), (-1, -1), INK),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, RULE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    if header:
        style += [('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 8.5),
                  ('BACKGROUND', (0, 0), (-1, 0), CREAM)]
    t.setStyle(TableStyle(style))
    return t


def _month_chart(months):
    vals = [m['kgco2e'] for m in months]
    known = [v for v in vals if v is not None]
    w, h = 170 * mm, 52 * mm
    d = Drawing(w, h)
    if not known:
        return None
    top = max(known) or 1
    left, bottom = 14 * mm, 9 * mm
    pw, ph = w - left - 4, h - bottom - 6
    band = pw / max(1, len(vals))
    bw = min(9 * mm, band * 0.6)
    d.add(Line(left, bottom, left + pw, bottom, strokeColor=RULE))
    d.add(String(0, bottom + ph - 6, fmt_kg(top), fontSize=6.5,
                 fillColor=MUTED))
    for i, (m, v) in enumerate(zip(months, vals)):
        x = left + i * band + (band - bw) / 2
        if v is None:
            d.add(Line(x, bottom + 1, x + bw, bottom + 1,
                       strokeColor=colors.HexColor('#BBB6AC'),
                       strokeWidth=1.5))
        else:
            d.add(Rect(x, bottom, bw, max(0.5, ph * v / top),
                       fillColor=GREEN, strokeColor=None))
        d.add(String(x + bw / 2, 2, m['month'][2:], fontSize=6,
                     fillColor=MUTED, textAnchor='middle'))
    return d


def build_pdf(org, user, fp, share_pct):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f'EVORA carbon report {fp["period"]["start"]} to '
              f'{fp["period"]["end"]}', author='EVORA')
    s = []
    per = fp['period']
    _, tz = today_for(org)
    now = timezone.now().astimezone(tz)
    s.append(_p('<font color="#2D5A42">EVORA · FLEET INTELLIGENCE</font>',
                SMALL))
    s.append(_p('Carbon Footprint Report', H1))
    s.append(_p(f'{org.name} · Reporting period {per["start"]} to '
                f'{per["end"]} ({per["days"]} days)', BODY))
    s.append(_p(f'Generated {now:%Y-%m-%d %H:%M %Z} by {user.name or user.email}'
                f' · {fp["method_version"]}', SMALL))
    s.append(Spacer(1, 6 * mm))

    t = fp['totals']
    cov = fp['coverage']
    s.append(_p('Estimated operational emissions', H2))
    if fp['status'] != 'ok':
        s.append(_p('A footprint could not be calculated for this period: no '
                    'fuel or charging record could be converted with the '
                    'recorded emission factors.', BODY))
    else:
        s.append(_p(fmt_kg(t['kgco2e']), BIG))
        s.append(_p(f'Boundary: {fp["boundary"]["label"]}. '
                    f'{fp["boundary"]["explanation"]} Lifecycle (vehicle and '
                    'battery manufacturing) emissions are not included.',
                    SMALL))
        s.append(Spacer(1, 3 * mm))
        s.append(_table([
            ['Component', 'Activity', 'Emissions'],
            ['Fuel (ICE vehicles)', f'{t["fuel_litres"]:,.1f} L',
             fmt_kg(t['fuel_kgco2e'])],
            ['Electricity (EVs)', f'{t["electricity_kwh"]:,.1f} kWh',
             fmt_kg(t['electricity_kgco2e'])],
        ], [70 * mm, 45 * mm, 50 * mm]))

    s.append(_p('Coverage and data completeness', H2))
    s.append(_p(
        f'{cov["reporting_vehicles"]} of {cov["vehicles_in_service"]} vehicles '
        f'in service have fuel or charging records in the period '
        f'({cov["ice_reporting"]} of {cov["ice_vehicles"]} ICE, '
        f'{cov["ev_reporting"]} of {cov["ev_vehicles"]} EV). Vehicles without '
        'records are not part of the figure; their emissions are unknown, not '
        f'zero. {cov["records_counted"]} records were converted.', BODY))
    if cov['records_excluded']:
        s.append(_table([['Records not converted', 'Count']] + [
            [_p(e['reason'], SMALL), str(e['records'])]
            for e in cov['records_excluded']], [140 * mm, 25 * mm]))

    prev = fp['previous']
    s.append(_p('Comparison with the previous period', H2))
    if prev['comparable']:
        direction = 'lower' if prev['change_kgco2e'] < 0 else 'higher'
        s.append(_p(
            f'Previous period {prev["period"]["start"]} to '
            f'{prev["period"]["end"]}: {fmt_kg(prev["kgco2e"])}. This period '
            f'is {fmt_kg(abs(prev["change_kgco2e"]))} '
            f'({abs(prev["change_pct"] or 0):.1f}%) {direction}, '
            'calculated with the same factors, boundary and vehicles.', BODY))
    else:
        s.append(_p('Not compared: ' + ' '.join(prev['reasons']), BODY))

    chart = _month_chart(fp['months'])
    if chart is not None and sum(
            1 for m in fp['months'] if m['kgco2e'] is not None) >= 2:
        s.append(KeepTogether([
            _p('Monthly emissions', H2), chart,
            _p('Months without converted records are shown as a flat line '
               '(no data), not as zero.', SMALL)]))

    comp = fp['composition']
    s.append(_p('Fleet composition', H2))
    share = (f'{comp["ev_share_pct"]}% electric' if comp['ev_share_pct']
             is not None else 'no classified vehicles')
    s.append(_p(f'{comp["ice"]} ICE and {comp["ev"]} electric vehicles '
                f'recorded ({share})'
                + (f'; {comp["unclassified"]} with fuel type "other" are not '
                   'classified' if comp['unclassified'] else '')
                + '. Fleet composition is not an emissions measure: the share '
                'of electric vehicles does not translate directly into an '
                'emissions reduction.', BODY))

    sc = fp['scenario']
    s.append(_p('Electrification scenario (modelled estimate)', H2))
    if sc['status'] != 'ok':
        s.append(_p(sc.get('reason', 'Not available.'), BODY))
    else:
        chosen = next((x for x in sc['shares'] if x['share_pct'] == share_pct),
                      sc['shares'][-1])
        s.append(_p(
            f'If {chosen["share_pct"]}% of the recorded activity of the '
            f'{sc["eligible_vehicles"]} eligible ICE vehicles moved to the EV '
            f'model that met every readiness check for each, modelled '
            f'emissions would be {fmt_kg(chosen["modelled_kgco2e"])}, '
            f'{fmt_kg(chosen["reduction_kgco2e"])} '
            f'({chosen["reduction_pct"]}%) below the period\'s footprint. '
            'This is a modelled estimate, not a measured or guaranteed '
            'reduction.', BODY))
        s.append(Spacer(1, 2 * mm))
        s.append(_table([['Vehicle', 'Distance', 'ICE', 'EV model',
                          'EV (grid)']] + [
            [r['registration_number'],
             _p(f'{r["distance_km"]:,.0f} km<br/>{r["distance_note"]}',
                SMALL),
             fmt_kg(r['ice_kgco2e']),
             _p(f'{r["ev_model"]}<br/>{r["ev_grid_kwh_per_km"]} kWh/km',
                SMALL),
             fmt_kg(r['ev_kgco2e'])] for r in sc['vehicles']],
            [28 * mm, 45 * mm, 27 * mm, 40 * mm, 27 * mm]))
        if sc.get('skipped'):
            s.append(_p('Not modelled: ' + '; '.join(
                f'{x["vehicles"]} vehicle(s) — {x["reason"]}'
                for x in sc['skipped']) + '.', SMALL))

    s.append(_p('Method, factors and limitations', H2))
    rows = [['Factor', 'Value', 'Source']]
    for fuel, f in fp['factors']['fuel'].items():
        rows.append([f'{fuel.title()} ({fp["boundary"]["fuel"]})',
                     f'{f["value"]} {f["unit"]}' if f else 'Not recorded',
                     _p(f['source']['title'] if f and f['source'] else '—',
                        SMALL)])
    g = fp['factors']['grid']
    rows.append([f'Grid ({fp["boundary"]["grid"]})',
                 f'{g["value"]} {g["unit"]}' if g else 'Not recorded',
                 _p(g['source']['title'] if g and g['source'] else '—',
                    SMALL)])
    s.append(_table(rows, [50 * mm, 40 * mm, 75 * mm]))
    s.append(Spacer(1, 2 * mm))
    for line in (
        fp['definition'],
        f'Factors in force on {fp["factors"]["as_of"]} are applied to the '
        'whole period and to the previous period.',
        'Figures are calculated from records stored in EVORA when this report '
        'was generated; later corrections are not reflected.',
    ):
        s.append(_p(line, SMALL))

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9 * mm,
                          f'EVORA carbon report · {org.name} · '
                          f'{per["start"]} to {per["end"]}')
        canvas.drawRightString(A4[0] - 18 * mm, 9 * mm,
                               f'Page {canvas.getPageNumber()}')
        canvas.restoreState()

    doc.build(s, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
