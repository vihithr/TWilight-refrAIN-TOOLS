#!/usr/bin/env python3
import argparse
import re
from pathlib import Path


LINE_PATTERN = re.compile(
    r"(?P<prefix><(?P<tag>setString|addString)>\s*<string>)(?P<content>.*?)(?P<suffix></string>\s*</(?P=tag)>)"
)
ENCODING_CANDIDATES = ("utf-8", "utf-8-sig", "gb18030", "shift_jis", "cp932")


def align_printable_chunks_parser_mode(text: str) -> tuple[str, bool]:
    out: list[str] = []
    i = 0
    byte_pos = 0
    changed = False
    pending_spaces: list[str] = []

    def align_before_token(expect_odd: bool) -> None:
        nonlocal byte_pos, changed
        desired = 1 if expect_odd else 0
        current = byte_pos % 2

        if current == desired:
            if pending_spaces:
                changed = True
            pending_spaces.clear()
            return

        # Need one byte to flip parity. Keep at most one ASCII space.
        if pending_spaces:
            changed = True
        pending_spaces.clear()
        out.append(" ")
        byte_pos += 1
        changed = True

    while i < len(text):
        ch = text[i]

        # Preserve any '*' run as an atomic token (never split/insert spaces inside).
        # Special case: "****" (+ optional full-width space) is treated as one
        # 2-byte newline control token.
        if ch == "*":
            j = i
            while j < len(text) and text[j] == "*":
                j += 1
            star_run = text[i:j]
            if len(star_run) == 4:
                token_end = j
                if token_end < len(text) and text[token_end] == "\u3000":
                    token_end += 1
                align_before_token(expect_odd=False)
                out.append(text[i:token_end])
                byte_pos += 2
                i = token_end
                continue

            align_before_token(expect_odd=True)
            out.append(star_run)
            byte_pos += len(star_run)
            i = j
            continue

        if ch.isspace():
            pending_spaces.append(ch)
            i += 1
            continue

        code = ord(ch)
        if 0x21 <= code <= 0x7E:
            # Single-byte printable chars must start at odd byte position.
            align_before_token(expect_odd=True)
            out.append(ch)
            byte_pos += 1
            i += 1
            continue

        if code >= 0x80 and not ch.isspace():
            # Non-ASCII chars (e.g. CJK) are treated as 2-byte units and
            # must start at even byte position.
            align_before_token(expect_odd=False)
            out.append(ch)
            byte_pos += 2
            i += 1
            continue

        out.append(ch)
        if code < 0x80:
            byte_pos += 1
        else:
            # Target parser treats non-ASCII as 2-byte units.
            byte_pos += 2
        i += 1

    # Trailing spaces are considered redundant for parser alignment.
    if pending_spaces:
        changed = True
        pending_spaces.clear()

    return "".join(out), changed


def normalize_content(content: str, encoding: str) -> tuple[str, list[str]]:
    issues = []
    original = content

    # Self-heal star divider lines if they were previously corrupted with spaces.
    no_space_content = re.sub(r"[ \t\u3000]+", "", content)
    if no_space_content and set(no_space_content) == {"*"}:
        if content != no_space_content:
            issues.append("normalize-pure-star-divider")
            issues.append("modified")
        return no_space_content, issues

    # Keep star control markers as-is in normal text.
    content_with_fullwidth_after_break = re.sub(r"\*\*\*\*(?!\u3000)", "****\u3000", content)
    if content_with_fullwidth_after_break != content:
        issues.append("append-fullwidth-space-after-break-token")
        content = content_with_fullwidth_after_break

    if content.startswith(" "):
        issues.append("leading-space-after-<string>")
        content = content.lstrip(" ")

    if len(content) == 1 and not re.match(r"[\u4e00-\u9fffA-Za-z0-9]", content):
        issues.append("single-symbol-content")

    aligned_content, aligned = align_printable_chunks_parser_mode(content)
    if aligned:
        issues.append("align-parser-byte-start")
        content = aligned_content

    if content != original and "modified" not in issues:
        issues.append("modified")

    return content, issues


def read_text_with_fallback(path: Path) -> tuple[str, str]:
    last_err = None
    for enc in ENCODING_CANDIDATES:
        try:
            return path.read_text(encoding=enc), enc
        except UnicodeDecodeError as err:
            last_err = err
            continue
    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        f"Unable to decode file with encodings: {ENCODING_CANDIDATES}. Last error: {last_err}",
    )


def process_file(path: Path, inplace: bool, make_backup: bool) -> tuple[int, int]:
    text, encoding_used = read_text_with_fallback(path)
    print(f"Detected file encoding: {encoding_used}")
    lines = text.splitlines(keepends=True)
    changed_count = 0
    report_count = 0
    new_lines = []

    for idx, line in enumerate(lines, start=1):
        m = LINE_PATTERN.search(line)
        if not m:
            new_lines.append(line)
            continue

        content = m.group("content")
        fixed_content, issues = normalize_content(content, encoding_used)
        if issues:
            report_count += 1
            print(f"[line {idx}] issues={','.join(sorted(set(issues)))}")

        if fixed_content != content:
            changed_count += 1
            line = (
                line[: m.start("content")]
                + fixed_content
                + line[m.end("content") :]
            )

        new_lines.append(line)

    print(f"Detected entries: {report_count}")
    print(f"Modified entries: {changed_count}")

    if changed_count and inplace:
        if make_backup:
            backup_path = path.with_suffix(path.suffix + ".bak")
            backup_path.write_text(text, encoding=encoding_used)
            print(f"Backup created: {backup_path}")
        path.write_text("".join(new_lines), encoding=encoding_used)
        print(f"File updated: {path}")

    if changed_count and not inplace:
        output_path = path.with_name(path.stem + ".fixed" + path.suffix)
        output_path.write_text("".join(new_lines), encoding=encoding_used)
        print(f"Fixed file written: {output_path}")

    return report_count, changed_count


def iter_xml_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix.lower() == ".xml" else []
    if target.is_dir():
        return sorted(p for p in target.glob("*.xml") if p.is_file())
    return []


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Batch detect/fix <setString>/<addString> <string> content in XML files: "
            "remove leading spaces and space out ASCII alnum/symbol chars."
        )
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        type=Path,
        help="Target XML file or directory (default: current directory)",
    )
    parser.add_argument(
        "--write-fixed-file",
        action="store_true",
        help="Write .fixed file instead of overwriting original file",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Create .bak backup when overwriting original file",
    )
    args = parser.parse_args()

    if not args.target.exists():
        raise SystemExit(f"Target not found: {args.target}")

    xml_files = iter_xml_files(args.target)
    if not xml_files:
        raise SystemExit(f"No XML files found under: {args.target}")

    total_detected = 0
    total_modified = 0
    print(f"Found {len(xml_files)} XML file(s).")
    for xml_file in xml_files:
        print(f"\n=== Processing: {xml_file} ===")
        detected, modified = process_file(
            path=xml_file,
            inplace=not args.write_fixed_file,
            make_backup=args.backup,
        )
        total_detected += detected
        total_modified += modified

    print("\n=== Overall Summary ===")
    print(f"Total detected entries: {total_detected}")
    print(f"Total modified entries: {total_modified}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
