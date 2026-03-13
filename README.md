# Hot Wheels Stunt Track Driver 1998 `.MMD` Exporter

`mmd_export.py` is a focused Python 3 CLI for the currently proven `.MMD` geometry format used by **Hot Wheels Stunt Track Driver 1998**.

The tool does not guess blindly. It exports the format according to the structure that was verified from `BLASTER.MMD`:

- `u32 @ 0x00` matches the record count exactly
- file layout is `8-byte header + N * 0x80-byte records`
- each record contains a clean float geometry block at `+0x40..+0x6f`
- each record also contains a repeatable UV-like metadata block at `+0x2e..+0x3f`
- `vertex[3] == vertex[2]` for every tested record, so faces are exported as **triangles**, not quads

---

## Current format hypothesis

### File layout

```text
0x0000  u32  record_count
0x0004  u32  unknown / reserved
0x0008  ...  record table
```

### Record layout (`0x80` bytes each)

```text
0x00..0x3f  32 x int16   quantized geometry + metadata
0x40..0x4f  4 x float32  X0..X3
0x50..0x5f  4 x float32  Y0..Y3
0x60..0x6f  4 x float32  Z0..Z3
0x70..0x7f  tail metadata block
```

### UV hypothesis

The strongest current UV mapping is:

```text
record +0x2e  U0  (u8)
record +0x30  U1  (u8)
record +0x32  U2  (u8)
record +0x34  attr0 (u8)
record +0x36  V0  (u16)
record +0x38  V1  (u16)
record +0x3a  V2  (u16)
record +0x3c  attr1 / page? (u16)
record +0x3e  flags (u16)
```

Interpretation used by the exporter:

- one triangle per record
- geometry vertices = `v0, v1, v2`
- UV vertices = `(u0,v0), (u1,v1), (u2,v2)`
- `attr0`, `attr1`, and `flags` are exported as comments / report data for later reverse engineering

---

## Why this is considered credible

From the tested sample:

- header record count and calculated record count match exactly
- no compression/container behavior was observed
- the float block decodes into plausible model-space coordinates
- the signed short block correlates strongly with the float block
- the UV candidate fields have the right kind of entropy and range for per-face texture coordinates
- the non-UV candidate fields are low-entropy and behave more like flags/page/material state

Example proven values from `BLASTER.MMD`:

- file size: `0x8688`
- header count: `269`
- `(0x8688 - 0x08) / 0x80 = 269`

---

## Requirements

- Python 3.9+
- no third-party packages required

---

## Usage

### Basic export

```bash
python mmd_export.py BLASTER.MMD
```

Default output directory:

```text
BLASTER_export/
```

### Specify output directory

```bash
python mmd_export.py BLASTER.MMD -o out
```

### Adjust UV normalization

```bash
python mmd_export.py BLASTER.MMD --u-width 255 --v-height 599
```

### Disable V flip

```bash
python mmd_export.py BLASTER.MMD --no-invert-v
```

---

## Output naming

All emitted files use the **input stem**.

For input:

```text
TOWJAM.MMD
```

the tool writes:

```text
TOWJAM.obj
TOWJAM.mtl
TOWJAM.csv
TOWJAM.report.txt
TOWJAM.summary.json
```

The material name and texture reference are also derived from the input stem:

```text
TOWJAM.BMP
```

So the emitted OBJ/MTL relationship is:

```obj
mtllib TOWJAM.mtl
usemtl TOWJAM.BMP
```

and:

```mtl
newmtl TOWJAM.BMP
map_Kd TOWJAM.BMP
```

---

## Output files

### `<stem>.obj`

Exports one triangle per record using the first three geometry vertices and the current UV hypothesis.

### `<stem>.mtl`

Simple material file referencing `<stem>.BMP`.

### `<stem>.csv`

Full parsed record dump including:

- all 32 signed shorts
- all float coordinates
- UV candidate fields
- trailing metadata fields
- raw hex for the UV and tail regions

### `<stem>.report.txt`

Human-readable technical summary with:

- file metrics
- structural proof
- bounds
- UV field statistics
- sample record dumps

### `<stem>.summary.json`

Machine-readable summary for tooling / follow-up analysis.

---

## Caveats

This exporter reflects the **current best-supported format interpretation**, not a final complete spec.

Known open questions:

- exact meaning of header `u32 @ 0x04`
- exact meaning of `attr0`
- exact meaning of `attr1`
- exact meaning of `flags`
- whether `attr1` encodes texture page selection, material ID, or another render-state field
- whether other `.MMD` files preserve the exact same UV normalization ranges

In other words: geometry extraction is strong, UV extraction is strong, but some render metadata is still under active reverse engineering.

---

## Suggested next work

- compare multiple `.MMD` files to confirm stable field semantics
- validate the UV mapping against known texture pages
- identify whether `attr1` is a page index or material selector
- correlate `flags` with in-game face properties
- check whether the `0x04` header value has global model metadata meaning

---

## Example workflow

1. Export from `.MMD`:

```bash
python mmd_export.py BLASTER.MMD
```

2. Place the matching texture next to the exported mesh:

```text
BLASTER.obj
BLASTER.mtl
BLASTER.BMP
```

3. Import `BLASTER.obj` into Blender or another DCC.
  
4. If the UVs appear vertically flipped, re-export with:
  

```bash
python mmd_export.py BLASTER.MMD --no-invert-v
```

5. If V scale is slightly off, test an adjusted `--v-height`.

---
