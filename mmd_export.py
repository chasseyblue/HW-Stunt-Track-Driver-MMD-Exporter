#!/usr/bin/env python3
"""
mmd_export.py

MMD exporter for the Hot Wheels Stunt Track Driver 1998
`.MMD` geometry format.

Current proven layout:
- File header:
    0x00  u32  record_count
    0x04  u32  unknown / reserved
- Followed by `record_count` records of 0x80 bytes each

Per-record layout:
- 0x00..0x3f : 32 x int16  (quantized geometry + metadata/flags)
- 0x40..0x4f : 4 x float32 (X0..X3)
- 0x50..0x5f : 4 x float32 (Y0..Y3)
- 0x60..0x6f : 4 x float32 (Z0..Z3)
- 0x70..0x7f : trailing metadata block

Strong current UV hypothesis:
- +0x2e : U0 (u8)
- +0x30 : U1 (u8)
- +0x32 : U2 (u8)
- +0x34 : attr0 (u8)
- +0x36 : V0 (u16)
- +0x38 : V1 (u16)
- +0x3a : V2 (u16)
- +0x3c : attr1/page? (u16)
- +0x3e : flags (u16)

Observed invariant on BLASTER.MMD:
- vertex[3] == vertex[2] for every record
- therefore records are exported as triangles using vertices 0,1,2

Outputs are named from the input file stem, e.g.:
- BLASTER.obj
- BLASTER.mtl
- BLASTER.csv
- BLASTER.report.txt
- BLASTER.summary.json

The material name and texture reference are always `<stem>.BMP`, e.g.:
- newmtl BLASTER.BMP
- map_Kd BLASTER.BMP
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

RECORD_OFFSET = 0x08
RECORD_SIZE = 0x80
FLOAT_X_OFF = 0x40
FLOAT_Y_OFF = 0x50
FLOAT_Z_OFF = 0x60
TAIL_OFF = 0x70

UV_U0_OFF = 0x2E
UV_U1_OFF = 0x30
UV_U2_OFF = 0x32
UV_ATTR0_OFF = 0x34
UV_V0_OFF = 0x36
UV_V1_OFF = 0x38
UV_V2_OFF = 0x3A
UV_ATTR1_OFF = 0x3C
UV_FLAGS_OFF = 0x3E


class FormatError(RuntimeError):
    """Raised when the input does not match the current `.MMD` hypothesis."""


def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def entropy_from_counter(counter: Counter[int], total: int) -> float:
    if total <= 0:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counter.values())


def parse_records(data: bytes) -> tuple[int, int, list[dict]]:
    if len(data) < RECORD_OFFSET:
        raise FormatError("file too small to contain 8-byte header")
    if (len(data) - RECORD_OFFSET) % RECORD_SIZE != 0:
        raise FormatError(
            f"payload does not divide evenly into 0x{RECORD_SIZE:x}-byte records: "
            f"size=0x{len(data):x}"
        )

    count_field = struct.unpack_from("<I", data, 0x00)[0]
    unknown_header = struct.unpack_from("<I", data, 0x04)[0]
    count_calc = (len(data) - RECORD_OFFSET) // RECORD_SIZE
    if count_field != count_calc:
        raise FormatError(
            f"record count mismatch: header={count_field} calculated={count_calc}"
        )

    records: list[dict] = []
    for index in range(count_calc):
        off = RECORD_OFFSET + index * RECORD_SIZE
        rec = data[off:off + RECORD_SIZE]

        s16 = list(struct.unpack_from("<32h", rec, 0x00))
        xs = list(struct.unpack_from("<4f", rec, FLOAT_X_OFF))
        ys = list(struct.unpack_from("<4f", rec, FLOAT_Y_OFF))
        zs = list(struct.unpack_from("<4f", rec, FLOAT_Z_OFF))
        verts = [(xs[i], ys[i], zs[i]) for i in range(4)]

        u0 = rec[UV_U0_OFF]
        u1 = rec[UV_U1_OFF]
        u2 = rec[UV_U2_OFF]
        attr0 = rec[UV_ATTR0_OFF]
        v0 = struct.unpack_from("<H", rec, UV_V0_OFF)[0]
        v1 = struct.unpack_from("<H", rec, UV_V1_OFF)[0]
        v2 = struct.unpack_from("<H", rec, UV_V2_OFF)[0]
        attr1 = struct.unpack_from("<H", rec, UV_ATTR1_OFF)[0]
        flags = struct.unpack_from("<H", rec, UV_FLAGS_OFF)[0]

        tail_u16 = list(struct.unpack_from("<8H", rec, TAIL_OFF))
        tail_u32 = list(struct.unpack_from("<4I", rec, TAIL_OFF))

        records.append(
            {
                "index": index,
                "offset": off,
                "s16": s16,
                "xs": xs,
                "ys": ys,
                "zs": zs,
                "verts": verts,
                "u0": u0,
                "u1": u1,
                "u2": u2,
                "attr0": attr0,
                "v0": v0,
                "v1": v1,
                "v2": v2,
                "attr1": attr1,
                "flags": flags,
                "tail_u16": tail_u16,
                "tail_u32": tail_u32,
                "raw_uv_hex": rec[UV_U0_OFF:0x40].hex(),
                "raw_tail_hex": rec[TAIL_OFF:TAIL_OFF + 0x10].hex(),
            }
        )

    return count_field, unknown_header, records


def vertex3_equals_vertex2_all(records: Iterable[dict], eps: float = 1e-6) -> bool:
    for rec in records:
        a = rec["verts"][2]
        b = rec["verts"][3]
        if any(abs(a[i] - b[i]) > eps for i in range(3)):
            return False
    return True


def geometry_bounds(records: Iterable[dict]) -> dict[str, list[float]]:
    verts = [v for rec in records for v in rec["verts"][:3]]
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    return {
        "x": [min(xs), max(xs)],
        "y": [min(ys), max(ys)],
        "z": [min(zs), max(zs)],
    }


def unique_vertex_count(records: Iterable[dict]) -> int:
    seen: set[tuple[float, float, float]] = set()
    for rec in records:
        for v in rec["verts"][:3]:
            seen.add(tuple(round(c, 6) for c in v))
    return len(seen)


def short_float_error_stats(records: Iterable[dict]) -> tuple[float | None, float | None]:
    errs: list[float] = []
    for rec in records:
        # Empirically observed on BLASTER.MMD:
        # s16[0..10] tracks floats[1..11] closely.
        floats_1_11 = rec["xs"][1:] + rec["ys"] + rec["zs"]
        for i in range(11):
            errs.append(abs(rec["s16"][i] - floats_1_11[i]))
    if not errs:
        return None, None
    return sum(errs) / len(errs), max(errs)


def field_stats(records: Iterable[dict], key: str) -> dict:
    values = [int(rec[key]) for rec in records]
    counts = Counter(values)
    return {
        "min": min(values),
        "max": max(values),
        "unique": len(counts),
        "entropy": entropy_from_counter(counts, len(values)),
        "top_values": counts.most_common(12),
    }


def write_mtl(path: Path, material_name: str) -> None:
    lines = [
        f"newmtl {material_name}",
        "Ka 1.000000 1.000000 1.000000",
        "Kd 1.000000 1.000000 1.000000",
        "Ks 0.000000 0.000000 0.000000",
        "d 1.0",
        "illum 1",
        f"map_Kd {material_name}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def normalize_uv(u: int, v: int, u_width: int, v_height: int, invert_v: bool) -> tuple[float, float]:
    uu = u / float(u_width)
    vv = v / float(v_height)
    if invert_v:
        vv = 1.0 - vv
    return uu, vv


def write_obj(
    path: Path,
    records: list[dict],
    mtl_filename: str,
    material_name: str,
    u_width: int,
    v_height: int,
    invert_v: bool,
) -> tuple[int, int, int]:
    vertex_count = 0
    vt_count = 0
    face_count = 0

    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"# exported from {path.stem}\n")
        f.write("# current hypothesis: one triangle per 0x80-byte record\n")
        f.write(f"mtllib {mtl_filename}\n")
        f.write(f"usemtl {material_name}\n")

        for rec in records:
            for vert in rec["verts"][:3]:
                f.write(f"v {vert[0]:.6f} {vert[1]:.6f} {vert[2]:.6f}\n")
                vertex_count += 1

            uv_triplet = [
                normalize_uv(rec["u0"], rec["v0"], u_width, v_height, invert_v),
                normalize_uv(rec["u1"], rec["v1"], u_width, v_height, invert_v),
                normalize_uv(rec["u2"], rec["v2"], u_width, v_height, invert_v),
            ]
            for uv in uv_triplet:
                f.write(f"vt {uv[0]:.6f} {uv[1]:.6f}\n")
                vt_count += 1

            base_v = vertex_count - 2
            base_vt = vt_count - 2
            f.write(
                f"# rec={rec['index']} off=0x{rec['offset']:08x} "
                f"attr0={rec['attr0']} attr1={rec['attr1']} flags=0x{rec['flags']:04x}\n"
            )
            f.write(
                f"f {base_v}/{base_vt} {base_v + 1}/{base_vt + 1} {base_v + 2}/{base_vt + 2}\n"
            )
            face_count += 1

    return vertex_count, vt_count, face_count


def write_csv(path: Path, records: list[dict]) -> None:
    header = [
        "index",
        "offset_hex",
        *[f"s16_{i:02d}" for i in range(32)],
        *[f"x{i}" for i in range(4)],
        *[f"y{i}" for i in range(4)],
        *[f"z{i}" for i in range(4)],
        "u0",
        "u1",
        "u2",
        "attr0",
        "v0",
        "v1",
        "v2",
        "attr1",
        "flags",
        *[f"tail_u16_{i}" for i in range(8)],
        *[f"tail_u32_{i}" for i in range(4)],
        "raw_uv_hex",
        "raw_tail_hex",
    ]

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for rec in records:
            writer.writerow(
                [rec["index"], f"0x{rec['offset']:08x}"]
                + rec["s16"]
                + rec["xs"]
                + rec["ys"]
                + rec["zs"]
                + [
                    rec["u0"],
                    rec["u1"],
                    rec["u2"],
                    rec["attr0"],
                    rec["v0"],
                    rec["v1"],
                    rec["v2"],
                    rec["attr1"],
                    rec["flags"],
                ]
                + rec["tail_u16"]
                + rec["tail_u32"]
                + [rec["raw_uv_hex"], rec["raw_tail_hex"]]
            )


def build_summary(
    input_path: Path,
    data: bytes,
    count_field: int,
    unknown_header: int,
    records: list[dict],
    u_width: int,
    v_height: int,
    invert_v: bool,
    material_name: str,
) -> dict:
    mean_abs_err, max_abs_err = short_float_error_stats(records)
    summary = {
        "file": input_path.name,
        "size": len(data),
        "sha256": sha256_bytes(data),
        "entropy": shannon_entropy(data),
        "header": {
            "record_count": count_field,
            "unknown_u32": unknown_header,
            "record_offset": RECORD_OFFSET,
            "record_size": RECORD_SIZE,
        },
        "geometry": {
            "record_count": len(records),
            "vertex3_equals_vertex2_all": vertex3_equals_vertex2_all(records),
            "unique_vertex_count": unique_vertex_count(records),
            "bounds": geometry_bounds(records),
            "short_vs_float_mean_abs_err": mean_abs_err,
            "short_vs_float_max_abs_err": max_abs_err,
        },
        "uv_hypothesis": {
            "u_width_divisor": u_width,
            "v_height_divisor": v_height,
            "invert_v": invert_v,
            "material_name": material_name,
            "offsets": {
                "u0_u8": UV_U0_OFF,
                "u1_u8": UV_U1_OFF,
                "u2_u8": UV_U2_OFF,
                "attr0_u8": UV_ATTR0_OFF,
                "v0_u16": UV_V0_OFF,
                "v1_u16": UV_V1_OFF,
                "v2_u16": UV_V2_OFF,
                "attr1_or_page_u16": UV_ATTR1_OFF,
                "flags_u16": UV_FLAGS_OFF,
            },
            "field_stats": {
                "u0": field_stats(records, "u0"),
                "u1": field_stats(records, "u1"),
                "u2": field_stats(records, "u2"),
                "attr0": field_stats(records, "attr0"),
                "v0": field_stats(records, "v0"),
                "v1": field_stats(records, "v1"),
                "v2": field_stats(records, "v2"),
                "attr1": field_stats(records, "attr1"),
                "flags": field_stats(records, "flags"),
            },
        },
    }
    return summary


def write_report(path: Path, summary: dict, records: list[dict], obj_name: str, mtl_name: str, csv_name: str) -> None:
    bounds = summary["geometry"]["bounds"]
    uv = summary["uv_hypothesis"]
    lines: list[str] = []
    lines.append(f"[FILE] {summary['file']}")
    lines.append(f"  size: {summary['size']} bytes (0x{summary['size']:x})")
    lines.append(f"  sha256: {summary['sha256']}")
    lines.append(f"  entropy: {summary['entropy']:.4f} bits/byte")
    lines.append("")
    lines.append("[HEADER]")
    lines.append(f"  record_count @ 0x00000000: {summary['header']['record_count']}")
    lines.append(f"  unknown_u32  @ 0x00000004: {summary['header']['unknown_u32']} (0x{summary['header']['unknown_u32']:08x})")
    lines.append(f"  record_offset: 0x{summary['header']['record_offset']:x}")
    lines.append(f"  record_size: 0x{summary['header']['record_size']:x}")
    lines.append("")
    lines.append("[GEOMETRY]")
    lines.append(f"  record_count: {summary['geometry']['record_count']}")
    lines.append(f"  vertex3 == vertex2 for all records: {summary['geometry']['vertex3_equals_vertex2_all']}")
    lines.append(f"  unique vertices (triangle export): {summary['geometry']['unique_vertex_count']}")
    lines.append(f"  bounds X: {bounds['x'][0]:.6f} .. {bounds['x'][1]:.6f}")
    lines.append(f"  bounds Y: {bounds['y'][0]:.6f} .. {bounds['y'][1]:.6f}")
    lines.append(f"  bounds Z: {bounds['z'][0]:.6f} .. {bounds['z'][1]:.6f}")
    lines.append(f"  s16[0..10] vs floats[1..11] mean abs err: {summary['geometry']['short_vs_float_mean_abs_err']:.6f}")
    lines.append(f"  s16[0..10] vs floats[1..11] max abs err: {summary['geometry']['short_vs_float_max_abs_err']:.6f}")
    lines.append("")
    lines.append("[UV HYPOTHESIS]")
    lines.append(f"  material name / texture ref: {uv['material_name']}")
    lines.append(f"  U divisor: {uv['u_width_divisor']}")
    lines.append(f"  V divisor: {uv['v_height_divisor']}")
    lines.append(f"  invert_v: {uv['invert_v']}")
    lines.append("  offsets:")
    for key, value in uv["offsets"].items():
        lines.append(f"    {key}: record+0x{value:02x}")
    lines.append("")
    lines.append("  field stats:")
    for name, info in uv["field_stats"].items():
        lines.append(
            f"    {name}: min={info['min']} max={info['max']} unique={info['unique']} entropy={info['entropy']:.3f} top={info['top_values']}"
        )
    lines.append("")
    lines.append("[OUTPUTS]")
    lines.append(f"  OBJ : {obj_name}")
    lines.append(f"  MTL : {mtl_name}")
    lines.append(f"  CSV : {csv_name}")
    lines.append("")
    lines.append("[SAMPLE RECORDS]")
    for rec in records[:8]:
        lines.append(f"  rec {rec['index']:03d} off=0x{rec['offset']:08x}")
        lines.append(f"    x = {[round(v, 6) for v in rec['xs']]}")
        lines.append(f"    y = {[round(v, 6) for v in rec['ys']]}")
        lines.append(f"    z = {[round(v, 6) for v in rec['zs']]}")
        lines.append(f"    uv raw = {rec['raw_uv_hex']}")
        lines.append(f"    U = ({rec['u0']}, {rec['u1']}, {rec['u2']})")
        lines.append(f"    V = ({rec['v0']}, {rec['v1']}, {rec['v2']})")
        lines.append(f"    attr0={rec['attr0']} attr1={rec['attr1']} flags=0x{rec['flags']:04x}")
        lines.append(f"    tail_u16={rec['tail_u16']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def make_names(stem: str) -> dict[str, str]:
    return {
        "obj": f"{stem}.obj",
        "mtl": f"{stem}.mtl",
        "csv": f"{stem}.csv",
        "report": f"{stem}.report.txt",
        "summary": f"{stem}.summary.json",
        "material": f"{stem}.BMP",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export the current Hot Wheels Stunt Track Driver `.MMD` geometry/UV hypothesis."
    )
    parser.add_argument("input", help="Input .MMD file")
    parser.add_argument(
        "-o",
        "--outdir",
        default=None,
        help="Output directory (default: alongside input, in <stem>_export)",
    )
    parser.add_argument(
        "--u-width",
        type=int,
        default=255,
        help="Divisor used to normalize U byte values into OBJ vt coordinates (default: 255)",
    )
    parser.add_argument(
        "--v-height",
        type=int,
        default=599,
        help="Divisor used to normalize V uint16 values into OBJ vt coordinates (default: 599)",
    )
    parser.add_argument(
        "--no-invert-v",
        action="store_true",
        help="Do not flip V during OBJ vt export",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    data = input_path.read_bytes()
    outdir = Path(args.outdir) if args.outdir else (input_path.parent / f"{input_path.stem}_export")
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        count_field, unknown_header, records = parse_records(data)
    except FormatError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    names = make_names(input_path.stem)
    invert_v = not args.no_invert_v

    write_mtl(outdir / names["mtl"], names["material"])
    obj_vertex_count, obj_vt_count, obj_face_count = write_obj(
        outdir / names["obj"],
        records,
        names["mtl"],
        names["material"],
        args.u_width,
        args.v_height,
        invert_v,
    )
    write_csv(outdir / names["csv"], records)

    summary = build_summary(
        input_path,
        data,
        count_field,
        unknown_header,
        records,
        args.u_width,
        args.v_height,
        invert_v,
        names["material"],
    )
    summary["exports"] = {
        "obj": names["obj"],
        "mtl": names["mtl"],
        "csv": names["csv"],
        "report": names["report"],
        "summary": names["summary"],
        "obj_vertex_count": obj_vertex_count,
        "obj_vt_count": obj_vt_count,
        "obj_face_count": obj_face_count,
    }

    (outdir / names["summary"]).write_text(json.dumps(summary, indent=2), encoding="utf-8", newline="\n")
    write_report(
        outdir / names["report"],
        summary,
        records,
        names["obj"],
        names["mtl"],
        names["csv"],
    )

    print(f"[OK] wrote {outdir}")
    print(f"  OBJ     : {outdir / names['obj']}")
    print(f"  MTL     : {outdir / names['mtl']}")
    print(f"  CSV     : {outdir / names['csv']}")
    print(f"  REPORT  : {outdir / names['report']}")
    print(f"  SUMMARY : {outdir / names['summary']}")
    print(f"  MATERIAL: {names['material']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
