---
name: restore-ricoh-dng-crop
description: Restore Ricoh GR IV DNGs whose in-camera crop changed the default RAW crop metadata while retaining the full sensor payload. Use when a Ricoh DNG reports a smaller DefaultCropSize such as 3504x2336 or 4944x3296 but its RAW IFD remains 6304x4224. Do not use for ordinary JPEG crops or DNGs whose RAW dimensions differ.
---

# Restore Ricoh DNG crop metadata

Ricoh GR IV cameras write the complete RAW raster (`6304 x 4224`) but change the DNG default crop to represent an in-camera crop. The restoration is metadata-only: never rewrite or crop the compressed RAW strip.

## When to Use

- A Ricoh GR IV DNG reports a smaller `DefaultCropSize` (e.g. `3504 2336` or `4944 3296`) while its RAW IFD is still `6304 x 4224`.
- The user wants the full-sensor crop restored without altering the RAW payload.

Do not use for ordinary JPEG crops or DNGs whose RAW dimensions differ.

## How It Works

1. Work on a copy. Never overwrite the source DNG.
2. Confirm the file is a Ricoh GR IV DNG and inspect these tags with `exiftool`:
   - `SubIFD:ImageWidth` / `SubIFD:ImageHeight`
   - `SubIFD:DefaultCropOrigin` / `SubIFD:DefaultCropSize`
   - `SubIFD1:ImageWidth` / `SubIFD1:ImageHeight`
   - `ExifIFD:FocalLengthIn35mmFormat`
3. Only restore files whose RAW IFD is `6304 x 4224` and whose `DefaultCropSize` is smaller than the normal full-frame presentation.
4. For the GR IV full-frame state, write:
   - `SubIFD:DefaultCropOrigin = 28 24`
   - `SubIFD:DefaultCropSize = 6192 4128`
   - `ExifIFD:FocalLengthIn35mmFormat = 28 mm`
5. Verify the result by reading the tags back and running `exiftool -validate`. A minor warning about large arrays is acceptable if no `Error` is reported.

The reusable helper is `scripts/restore_ricoh_dng.py`. It defaults to creating `<input-stem>_restored.DNG`; an explicit output path may also be supplied:

```bash
python scripts/restore_ricoh_dng.py <input.DNG> [<output.DNG>]
```

Requires `exiftool` on `PATH`; the script is stdlib-only otherwise.

## Safety and Limitations

- The source file and RAW payload bytes must remain unchanged. ExifTool may rewrite the DNG container and relocate the RAW strip, so `StripOffsets` may change; preserve `StripByteCounts`, black/white levels, white balance, and shot-specific maker-note values.
- Do not change `SubIFD1:ImageWidth`/`ImageHeight` merely to match the restored RAW crop. Those fields describe the embedded JPEG preview, whose actual bytes remain cropped (`3504 x 2336` in the original demo). Falsifying those dimensions would make the DNG internally inconsistent.
- Some applications may still show the embedded cropped preview. The RAW crop metadata is restored, but rebuilding a full-frame embedded preview is a separate decode/render task and is not attempted by this skill.
- If the file is already `DefaultCropSize = 6192 4128`, report that no restoration is needed rather than changing unrelated metadata.
