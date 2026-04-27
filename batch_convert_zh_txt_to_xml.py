from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path


def xml_name_from_source(file_name: str) -> str:
    if file_name.endswith(".zh.txt"):
        return file_name[: -len(".zh.txt")] + ".xml"
    return Path(file_name).stem + ".xml"


def convert_one_file(src_file: Path, src_root: Path, out_root: Path) -> Path:
    relative_parent = src_file.parent.relative_to(src_root)
    target_dir = out_root / relative_parent
    target_dir.mkdir(parents=True, exist_ok=True)

    out_file = target_dir / xml_name_from_source(src_file.name)
    content = src_file.read_text(encoding="utf-8", errors="replace")
    converted = content.replace(".zh", "")
    out_file.write_text(converted, encoding="utf-8", newline="")
    return out_file


def convert_all(src_root: Path, out_root: Path) -> tuple[int, list[Path]]:
    files = sorted(src_root.rglob("*.zh.txt"))
    outputs: list[Path] = []

    for src_file in files:
        outputs.append(convert_one_file(src_file, src_root, out_root))

    return len(files), outputs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch convert *.zh.txt to *.xml and remove '.zh' in file content."
    )
    parser.add_argument(
        "--source-dir",
        default=".",
        help="Source directory to scan recursively.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. If omitted, a new folder is auto-created in workspace root.",
    )
    args = parser.parse_args()

    workspace_root = Path(__file__).resolve().parent
    src_root = (workspace_root / args.source_dir).resolve()

    if not src_root.exists() or not src_root.is_dir():
        raise FileNotFoundError(f"Source directory not found: {src_root}")

    if args.output_dir:
        out_root = (workspace_root / args.output_dir).resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_root = (workspace_root / f"xml_output_{stamp}").resolve()

    out_root.mkdir(parents=True, exist_ok=False)

    total, outputs = convert_all(src_root, out_root)

    print(f"Source: {src_root}")
    print(f"Output directory: {out_root}")
    print(f"Converted files: {total}")
    if outputs:
        print(f"Example output: {outputs[0]}")


if __name__ == "__main__":
    main()
