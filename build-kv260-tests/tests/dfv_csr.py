#!/usr/bin/env python3
"""Decode separate indexed collision (COL3) and throttle (THR1) CSR families.

Only GPU-printed raw frames/samples supply data. RTL displays are never parsed.
Each core captures all slots of a family simultaneously. Shared observations
are kept separate by reader core; no cross-core totals are formed.
"""
from dataclasses import dataclass
import re

FRAME_MARKER = 'DFV_CSR_FRAME'
SAMPLE_MARKER = 'DFV_CSR_SAMPLE'
MARKER = FRAME_MARKER
IDS = {'collision': 0x434F4C33, 'throttle': 0x54485231}
LIMIT = 0xFFFFFFFF
FRAME_FIELDS = set('v core cores family phase info count valid cycle_lo cycle_hi'.split())
SAMPLE_FIELDS = set('v core family phase index desc owner flags count0 count1 threshold'.split())
DOMAINS = {0: 'dcache', 1: 'icache', 2: 'l1arb', 3: 'l2', 4: 'l3'}


class CollisionReportError(ValueError):
    """Invalid/missing observation data, never an implicit zero."""


@dataclass(frozen=True)
class ObservationWindow:
    core: int
    cores: int
    family: str
    index: int
    descriptor: int
    owner: int
    count0: int
    count1: int
    cycles: int
    threshold: int

    @property
    def domain(self):
        return DOMAINS[self.descriptor >> 24] if self.family == 'collision' else (
            'global' if self.descriptor >> 24 == 0 else 'bank')

    @property
    def unit(self):
        return (self.descriptor >> 16) & 255

    @property
    def bank(self):
        return self.descriptor & 65535

    @property
    def prefix(self):
        base = f'core{self.core}_{self.family}_{self.domain}_owner{self.owner}_unit{self.unit}'
        return base if self.domain == 'global' else base + f'_bank{self.bank}'

    @property
    def natural(self):
        return self.count0

    @property
    def dfv(self):
        return self.count1

    @property
    def total(self):
        return self.count0 + self.count1

    def as_dict(self):
        result = dict(core=self.core, cores=self.cores, family=self.family,
                      index=self.index, domain=self.domain, unit=self.unit,
                      bank=None if self.domain == 'global' else self.bank,
                      owner=self.owner, window_cycles=self.cycles)
        if self.family == 'collision':
            result.update(natural=self.natural, dfv=self.dfv, total=self.total)
        else:
            result.update(events=self.count0, threshold=self.threshold)
        return result


def _fields(line, marker, expected, number):
    pairs = line.split(marker, 1)[1].strip().split()
    data = {}
    for pair in pairs:
        if pair.count('=') != 1:
            raise CollisionReportError(f'Line {number}: malformed CSR field')
        key, value = pair.split('=')
        if key in data:
            raise CollisionReportError(f'Line {number}: duplicate CSR field')
        data[key] = value
    if set(data) != expected:
        raise CollisionReportError(f'Line {number}: incomplete/unrecognized CSR fields')
    for key in expected - {'family', 'phase'}:
        value = data[key]
        if not re.fullmatch(r'(?:0[xX][0-9a-fA-F]+|[0-9]+)', value):
            raise CollisionReportError(f'Line {number}: invalid numeric CSR value')
        data[key] = int(value, 16 if value.lower().startswith('0x') else 10)
        if data[key] > LIMIT:
            raise CollisionReportError(f'Line {number}: numeric CSR value exceeds 32 bits')
    if data['v'] != 2 or data['family'] not in IDS or data['phase'] not in ('BEGIN', 'END'):
        raise CollisionReportError(f'Line {number}: unsupported CSR report format')
    return data


def parse_observation_windows(lines):
    frames, samples = {}, {}
    expected_cores = None
    for number, line in enumerate(lines, 1):
        if 'DFV_CSR_ERROR' in line:
            raise CollisionReportError(f'GPU reported a CSR error: {line.strip()}')
        if FRAME_MARKER in line:
            f = _fields(line, FRAME_MARKER, FRAME_FIELDS, number)
            key = f['core'], f['family'], f['phase']
            if key in frames:
                raise CollisionReportError('Duplicate CSR frame')
            if not 1 <= f['cores'] <= 4096 or not f['core'] < f['cores']:
                raise CollisionReportError('Invalid core count/ID')
            if expected_cores is None:
                expected_cores = f['cores']
            if f['cores'] != expected_cores:
                raise CollisionReportError('Inconsistent core count')
            if f['info'] != IDS[f['family']] or f['valid'] != 1:
                raise CollisionReportError('Unavailable/invalid CSR family or incompatible RTL image')
            if f['count'] > 4096 or (f['family'] == 'collision' and not f['count']):
                raise CollisionReportError('Invalid monitor count')
            if f['cycle_hi'] >= 4096:
                raise CollisionReportError('Invalid captured 44-bit cycle count')
            if f['phase'] == 'END' and (f['core'], f['family'], 'BEGIN') not in frames:
                raise CollisionReportError('END frame appeared before BEGIN')
            frames[key] = f
        elif SAMPLE_MARKER in line:
            s = _fields(line, SAMPLE_MARKER, SAMPLE_FIELDS, number)
            frame_key = s['core'], s['family'], s['phase']
            if frame_key not in frames:
                raise CollisionReportError('Sample appeared before its frame')
            key = frame_key + (s['index'],)
            if key in samples or s['index'] >= frames[frame_key]['count']:
                raise CollisionReportError('Duplicate/out-of-range monitor index')
            if s['flags'] != 9 or s['count0'] == LIMIT or s['count1'] == LIMIT:
                raise CollisionReportError('Invalid/unavailable/at-limit monitor snapshot')
            domain, unit, bank = s['desc'] >> 24, (s['desc'] >> 16) & 255, s['desc'] & 65535
            if s['family'] == 'collision':
                if (domain not in DOMAINS or s['threshold'] != 0
                        or (domain == 1 and bank != 0)
                        or (domain == 2 and (unit != 0 or bank != 0))
                        or (domain >= 3 and unit != 0)):
                    raise CollisionReportError('Invalid collision descriptor')
            elif (domain not in (0, 1) or s['count1'] != 0 or s['threshold'] > 65535
                  or (domain == 0 and bank != 65535) or (domain == 1 and bank == 65535)):
                raise CollisionReportError('Invalid throttle descriptor')
            samples[key] = s
    if expected_cores is None:
        raise CollisionReportError('No indexed GPU CSR reports; rebuild RTL and kernels for COL3/THR1')
    if len(frames) != 4 * expected_cores:
        raise CollisionReportError('Missing core/family/BEGIN/END frame')
    if len(samples) != sum(f['count'] for f in frames.values()):
        raise CollisionReportError('Missing monitor sample (partial report)')
    result = []
    shapes = {}
    for core in range(expected_cores):
        for family in IDS:
            a, b = frames.get((core, family, 'BEGIN')), frames.get((core, family, 'END'))
            if not a or not b or a['count'] != b['count']:
                raise CollisionReportError('Missing/inconsistent monitor frames')
            cycles = ((b['cycle_hi'] << 32) | b['cycle_lo']) - ((a['cycle_hi'] << 32) | a['cycle_lo'])
            if cycles <= 0:
                raise CollisionReportError('Reset/wrapped/nonpositive measurement interval')
            identities = set()
            for index in range(a['count']):
                x = samples.get((core, family, 'BEGIN', index))
                y = samples.get((core, family, 'END', index))
                if not x or not y:
                    raise CollisionReportError('Missing indexed BEGIN/END pair')
                if any(x[k] != y[k] for k in ('desc', 'owner', 'threshold')):
                    raise CollisionReportError('Monitor identity/threshold changed during measurement')
                if y['count0'] < x['count0'] or y['count1'] < x['count1']:
                    raise CollisionReportError('Counter reset/wrap during measurement')
                identity = x['desc'], x['owner']
                if identity in identities:
                    raise CollisionReportError('Duplicate physical monitor in a core frame')
                identities.add(identity)
                result.append(ObservationWindow(core, expected_cores, family, index,
                    x['desc'], x['owner'], y['count0'] - x['count0'],
                    y['count1'] - x['count1'], cycles, x['threshold']))
            shape = tuple(samples[core, family, 'BEGIN', i]['desc'] for i in range(a['count']))
            if family in shapes and shapes[family] != shape:
                raise CollisionReportError('Inconsistent monitor topology across cores')
            shapes[family] = shape
    return result


def parse_collision_windows(lines):
    return [w for w in parse_observation_windows(lines) if w.family == 'collision']


def collision_counts(windows):
    return {f'{w.prefix}_{kind}': getattr(w, kind)
            for w in windows if w.family == 'collision' for kind in ('natural', 'dfv', 'total')}


def collision_cycles(windows):
    return {w.prefix: w.cycles for w in windows}
