#!/usr/bin/env python3
"""Decode GPU-printed COL2 snapshots; RTL $display messages are not measurements.

The fixed target is local-socket D-cache unit 0 / bank 0. A core ID identifies
the reporting core, not ownership of the shared cache events. No cross-core
aggregation is performed. BEGIN/END values are printed after END is captured.
"""
from dataclasses import dataclass
import re

COL2_ID = 0x434F4C32
COUNTER_LIMIT = 4095
MARKER = "DFV_CSR_SNAPSHOT"
FIELDS = frozenset(("v", "core", "cores", "cache", "bank", "phase", "info",
                    "flags", "natural", "dfv", "cycle_lo", "cycle_hi"))


class CollisionReportError(ValueError):
    """A missing or invalid GPU CSR report, never an implicit zero count."""


@dataclass(frozen=True)
class Snapshot:
    core: int
    cores: int
    phase: str
    natural: int
    dfv: int
    cycles: int


@dataclass(frozen=True)
class CollisionWindow:
    core: int
    cores: int
    begin: Snapshot
    end: Snapshot

    @property
    def prefix(self):
        return f"core{self.core}_cache0_bank0"

    @property
    def natural(self):
        return self.end.natural - self.begin.natural

    @property
    def dfv(self):
        return self.end.dfv - self.begin.dfv

    @property
    def total(self):
        return self.natural + self.dfv

    @property
    def cycles(self):
        return self.end.cycles - self.begin.cycles

    def as_dict(self):
        return {"core": self.core, "cores": self.cores, "cache_unit": 0, "bank": 0,
                "natural": self.natural, "dfv": self.dfv, "total": self.total,
                "window_cycles": self.cycles,
                "edges_per_million_cycles": self.total * 1e6 / self.cycles}


def _snapshot(line, number):
    payload = line.split(MARKER, 1)[1].strip()
    tokens = payload.split()
    fields = {}
    for token in tokens:
        if token.count("=") != 1:
            raise CollisionReportError(f"Line {number}: malformed CSR field {token!r}")
        key, value = token.split("=", 1)
        if key in fields:
            raise CollisionReportError(f"Line {number}: duplicate CSR field {key}")
        fields[key] = value
    if fields.keys() != FIELDS:
        raise CollisionReportError(f"Line {number}: incomplete/unrecognized CSR snapshot fields")
    if fields["phase"] not in ("BEGIN", "END"):
        raise CollisionReportError(f"Line {number}: unknown snapshot phase")
    values = {}
    try:
        for key in FIELDS - {"phase"}:
            if not re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", fields[key]):
                raise ValueError(key)
            values[key] = int(fields[key], 16 if fields[key].lower().startswith("0x") else 10)
    except ValueError as exc:
        raise CollisionReportError(f"Line {number}: invalid numeric CSR field") from exc
    if values["v"] != 1 or values["info"] != COL2_ID:
        raise CollisionReportError(f"Line {number}: unsupported GPU collision CSR ABI")
    if not 1 <= values["cores"] <= 0xFFFFFFFF or not 0 <= values["core"] < values["cores"]:
        raise CollisionReportError(f"Line {number}: invalid reporting core/count")
    if values["cache"] != 0 or values["bank"] != 0:
        raise CollisionReportError(f"Line {number}: CSR ABI only observes local cache unit 0 / bank 0")
    if values["flags"] != 9:
        raise CollisionReportError(f"Line {number}: unavailable/invalid/at-limit CSR snapshot (flags={values['flags']})")
    if not 0 <= values["natural"] < COUNTER_LIMIT or not 0 <= values["dfv"] < COUNTER_LIMIT:
        raise CollisionReportError(f"Line {number}: 12-bit collision counter reached its limit")
    if not 0 <= values["cycle_lo"] <= 0xFFFFFFFF or not 0 <= values["cycle_hi"] < (1 << 12):
        raise CollisionReportError(f"Line {number}: invalid 44-bit captured core-cycle count")
    return Snapshot(values["core"], values["cores"], fields["phase"],
                    values["natural"], values["dfv"],
                    (values["cycle_hi"] << 32) | values["cycle_lo"])


def parse_collision_windows(lines):
    """Require exactly one BEGIN/END pair per reported core; accept genuine zeros.

    Deliberately ignores DFV_COLLISION_* and COLLISION: summary lines. A log
    containing only these legacy formats fails instead of reporting zero.
    """
    snapshots = {}
    expected_cores = None
    for number, line in enumerate(lines, 1):
        if "DFV_CSR_ERROR" in line:
            raise CollisionReportError(f"GPU reported a CSR measurement error: {line.strip()}")
        if MARKER not in line:
            continue
        snapshot = _snapshot(line, number)
        if expected_cores is None:
            expected_cores = snapshot.cores
        if snapshot.cores != expected_cores:
            raise CollisionReportError("Inconsistent number of cores in CSR reports")
        key = (snapshot.core, snapshot.phase)
        if key in snapshots:
            raise CollisionReportError(f"Duplicate {snapshot.phase} snapshot from core {snapshot.core}")
        if snapshot.phase == "END" and (snapshot.core, "BEGIN") not in snapshots:
            raise CollisionReportError(f"END appeared before BEGIN for core {snapshot.core}")
        snapshots[key] = snapshot
    if expected_cores is None:
        raise CollisionReportError("No GPU CSR snapshots found; use the updated RTL and regression kernels")
    # Check cardinality first rather than allocate a range from untrusted log data.
    if len(snapshots) != 2 * expected_cores:
        raise CollisionReportError("Missing core or BEGIN/END CSR snapshot (partial run)")
    windows = []
    for core in range(expected_cores):
        begin = snapshots.get((core, "BEGIN"))
        end = snapshots.get((core, "END"))
        if begin is None or end is None:
            raise CollisionReportError(f"Missing BEGIN/END CSR snapshot for core {core}")
        if end.natural < begin.natural or end.dfv < begin.dfv or end.cycles <= begin.cycles:
            raise CollisionReportError(f"Core {core}: reset/wrapped/nonpositive CSR measurement interval")
        windows.append(CollisionWindow(core, expected_cores, begin, end))
    return windows


def collision_counts(windows):
    return {f"{window.prefix}_{kind}": getattr(window, kind)
            for window in windows for kind in ("natural", "dfv", "total")}


def collision_cycles(windows):
    return {window.prefix: window.cycles for window in windows}
