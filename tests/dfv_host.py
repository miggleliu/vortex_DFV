#!/usr/bin/env python3
"""One runtime DFV settings format for RTL simulation and board runners."""
import json
from pathlib import Path

NAMES = ['enable', 'icache_fill_req_stall', 'random_seed', 'set_threshold',
         'dcache_fill_req_stall', 'writeback_stall', 'dcache_fill_rsp_stall',
         'release_threshold', 'release_seed', 'release_delay', 'release_forever',
         'throttle_threshold', 'fill_bank_mask', 'dcache_core_req_stall',
         'bank_throttle_threshold']
ADDRESSES = {name: 0x7c0 + i for i, name in enumerate(NAMES)}

def number(value):
    if isinstance(value, bool): return int(value)
    if not isinstance(value, (str, int)): raise ValueError('CSR values must be integers')
    return int(value, 0) if isinstance(value, str) else value

def validate(values):
    if set(values) != set(range(0x7c0, 0x7cf)):
        raise ValueError('Expected the complete DFV control CSR set 0x7c0..0x7ce')
    for addr, value in values.items():
        index = addr - 0x7c0
        limit = 1 if index in (0, 1, 4, 5, 6, 10, 13) else (
            0xffffffff if index in (2, 8) else 0xffff)
        if not isinstance(value, int) or not 0 <= value <= limit:
            raise ValueError(f'CSR {addr:#x}: value outside 0..{limit:#x}')
        if index == 9 and any((value >> shift) & 15 > 8 for shift in (0, 4, 8, 12)):
            raise ValueError('Each packed release delay must be in 0..8')
    return values

def combo_values(combo, defaults):
    stalls = combo['stalls']
    values = dict(zip(range(0x7c0, 0x7cf), [
        int(any(stalls)), int(stalls[0]), combo.get('random_seed', 0xabcdef00),
        combo.get('set_threshold', defaults.DEFAULT_SET_THRESHOLD),
        int(stalls[1]), 0, int(stalls[2]),
        combo.get('release_threshold', defaults.DEFAULT_RELEASE_THRESHOLD),
        combo.get('release_seed', 0x87654321),
        combo.get('release_delay', defaults.DEFAULT_RELEASE_DELAY),
        combo.get('release_forever', defaults.DEFAULT_RELEASE_FOREVER),
        combo.get('throttle_threshold', defaults.DEFAULT_THROTTLE_THRESHOLD),
        combo.get('fill_bank_mask', defaults.DEFAULT_FILL_BANK_MASK), int(stalls[3]),
        combo.get('bank_throttle_threshold', defaults.DEFAULT_BANK_THROTTLE_THRESHOLD)]))
    return validate(values)

def overrides(pairs=(), path=None):
    data = json.loads(Path(path).read_text()) if path else {}
    if not isinstance(data, dict): raise ValueError('--dfv-config must contain a JSON object')
    items = list(data.items())
    for pair in pairs:
        if pair.count('=') != 1: raise ValueError('--csr expects NAME=VALUE or ADDRESS=VALUE')
        items.append(pair.split('=', 1))
    result = {}
    for name, value in items:
        addr = ADDRESSES[name] if name in ADDRESSES else number(name)
        if addr not in range(0x7c0, 0x7cf): raise ValueError(f'Unsupported DFV CSR: {name}')
        result[addr] = number(value)
    # Validate overrides with a neutral complete configuration.
    validate({**dict.fromkeys(range(0x7c0, 0x7cf), 0), **result})
    return result

def environment(values):
    validate(values)
    return ','.join(f'{addr:#x}={values[addr]:#x}' for addr in sorted(values))
