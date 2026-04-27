#!/usr/bin/env python3
"""
批量将 UTF‑8 文本文件转换为 GBK 或 Shift‑JIS 编码。
内置自定义字符替换表，用于处理目标编码不支持的字符。

支持命令行参数与交互模式，可选备份与进度显示。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

# ---------- 目标编码映射 ----------
ENCODING_MAP = {
    "gbk":      ("GBK", "gb18030"),      # gb18030 是 GBK 的超集，兼容性更好
    "shiftjis": ("Shift-JIS", "shift_jis"),
}

# ---------- 自定义字符替换表 ----------
# 说明：键为 UTF‑8 字符，值为转换后安全字符。值为空字符串表示直接移除。
CUSTOM_REPLACE_MAP: Dict[str, str] = {
    # 1. 零宽字符与排版控制符（移除）
    "\u200b": "",        # 零宽空格 (ZWSP)
    "\u200c": "",        # 零宽不连字 (ZWNJ)
    "\u200d": "",        # 零宽连字 (ZWJ)
    "\ufeff": "",        # BOM / 零宽不换行空格
    "\u00A0": " ",       # 不换行空格 (NBSP) → 普通空格

    # 2. 标点符号规范化
    "\u301c": "\uff5e",   # 波浪号 (WAVE DASH) → 全角波浪号 (FULLWIDTH TILDE)
    "\u30fb": "\u00b7",   # 片假名中点 (KATAKANA MIDDLE DOT) → 中间点 (·)
    "\u300c": "\u201c",   # 「 → “ (左直角引号降级为左弯引号)
    "\u300d": "\u201d",   # 」 → ”
    "\u300e": "\u2018",   # 『 → ‘
    "\u300f": "\u2019",   # 』 → ’
    "\u2026": "...",      # 省略号 (…) → 三个英文句号

    # 3. 常见日文汉字 / 异体字 → 中文标准用字 (按需求启用)
    "\u6ca2": "\u6cfd",   # 沢 → 泽
    "\u685c": "\u6a31",   # 桜 → 樱
    "\u6c17": "\u6c14",   # 気 → 气
    "\u5358": "\u5355",   # 単 → 单
    "\u6226": "\u6218",   # 戦 → 战
    "\u767a": "\u53d1",   # 発 → 发

    # 4. 特殊符号降级
    "\u2661": "\u2665",   # 空心心形 ♡ → 实心心形 ♥
    "\u266a": "~",        # 音符 ♪ → 波浪号
    "\u00a9": "(C)",      # © → (C)
    "\u00ae": "(R)",      # ® → (R)
}

# 预编译转换表，大幅加速字符串替换
TRANSLATE_TABLE = str.maketrans(CUSTOM_REPLACE_MAP)


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="将目录中的 UTF‑8 文本文件转换为 GBK 或 Shift‑JIS 编码。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例: %(prog)s -d /path/to/dir -e gbk --backup --ext .txt .md"
    )
    parser.add_argument(
        "-d", "--directory", type=str, default=None,
        help="要处理的根目录（默认交互输入）"
    )
    parser.add_argument(
        "-e", "--encoding", type=str, choices=["gbk", "shiftjis"], default=None,
        help="目标编码（默认交互选择）"
    )
    parser.add_argument(
        "--ext", nargs="*", default=None,
        help="只处理指定扩展名的文件，如 .txt .md（默认处理所有文件）"
    )
    parser.add_argument(
        "--backup", action="store_true",
        help="转换前备份原文件（扩展名 .bak）"
    )
    parser.add_argument(
        "--no-progress", action="store_true",
        help="禁用进度条显示"
    )
    return parser


def ask_directory() -> Path:
    """交互式获取并验证目录路径。"""
    while True:
        raw = input("请输入要转换的目录路径: ").strip().strip('"')
        if not raw:
            print("路径不能为空。")
            continue
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            print("目录不存在。")
            continue
        if not path.is_dir():
            print("这不是一个目录。")
            continue
        return path


def ask_encoding() -> Tuple[str, str]:
    """交互式选择目标编码。"""
    print("\n选择目标编码:")
    print("  1) GBK")
    print("  2) Shift-JIS")
    while True:
        choice = input("请输入 1 或 2: ").strip()
        if choice == "1":
            return ENCODING_MAP["gbk"]
        if choice == "2":
            return ENCODING_MAP["shiftjis"]
        print("无效选择，请重新输入。")


def is_likely_utf8(path: Path) -> Optional[str]:
    """
    尝试将文件作为 UTF-8 文本读取。
    成功则返回解码后的字符串，否则返回 None。
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None

    # 去除 UTF‑8 BOM
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]

    # 尝试用 UTF‑8 解码
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # 可选：使用 charset_normalizer/charset 进一步检测，这里简单返回 None
        return None

    # 快速排除纯二进制文件：如果包含过多空字符，大概率是二进制
    if "\x00" in text:
        return None

    return text


def preserve_metadata(original: Path, target: Path) -> None:
    """
    将原文件的元数据（修改时间、访问时间、权限）复制到目标文件。
    如果失败则静默忽略。
    """
    try:
        stat = original.stat()
        os.utime(target, (stat.st_atime, stat.st_mtime))
        shutil.copymode(original, target)
    except Exception:
        pass  # 非关键操作


def convert_file(
    path: Path,
    target_codec: str,
    backup: bool = False
) -> Tuple[bool, str]:
    """
    转换单个文件。
    返回 (成功标志, 原因字符串)。
    """
    text = is_likely_utf8(path)
    if text is None:
        return False, "skip_not_utf8"

    # 1) 应用自定义字符映射清洗
    cleaned = text.translate(TRANSLATE_TABLE)

    # 2) 编码为目标格式，无法表示的字符用 '?' 填充
    try:
        encoded = cleaned.encode(target_codec, errors="replace")
    except Exception:
        return False, "skip_encode_error"

    # 3) 如果需要备份，先创建 .bak 文件（保留原始属性）
    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        try:
            shutil.copy2(path, backup_path)
        except OSError:
            return False, "skip_backup_failed"

    # 4) 写回原文件（覆盖）
    try:
        path.write_bytes(encoded)
    except OSError:
        return False, "skip_write_error"

    # 5) 恢复文件时间与权限（从原始状态读取，但原始已被覆盖，故从备份或自己获取）
    #    这里从备份文件恢复属性（若有备份），否则尽力而为
    if backup:
        preserve_metadata(backup_path, path)

    return True, "converted"


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # 获取目录
    if args.directory:
        root = Path(args.directory).expanduser().resolve()
        if not root.is_dir():
            print(f"错误：{root} 不是一个有效目录。", file=sys.stderr)
            sys.exit(1)
        print(f"使用指定目录: {root}")
    else:
        root = ask_directory()

    # 获取目标编码
    if args.encoding:
        target_name, target_codec = ENCODING_MAP[args.encoding]
        print(f"使用指定编码: {target_name}")
    else:
        target_name, target_codec = ask_encoding()

    print(f"\n目标目录: {root}")
    print(f"目标编码: {target_name}")
    if args.backup:
        print("备份模式：启用（原文件将被保留为 .bak）")
    if args.ext:
        exts = [e.lower() if e.startswith(".") else f".{e.lower()}" for e in args.ext]
        print(f"文件过滤: 只处理扩展名为 {', '.join(exts)} 的文件")
    else:
        exts = None
    print()

    # 收集待处理文件列表（支持扩展名过滤）
    all_files = [
        f for f in root.rglob("*")
        if f.is_file() and (exts is None or f.suffix.lower() in exts)
    ]

    # 进度条设置
    use_progress = not args.no_progress
    try:
        from tqdm import tqdm
        progress = tqdm(all_files, desc="转换进度", unit="file") if use_progress else all_files
    except ImportError:
        if use_progress:
            print("提示: 安装 tqdm 可显示进度条。")
        progress = all_files

    # 统计
    converted = 0
    skipped_not_utf8 = 0
    skipped_write_error = 0
    skipped_backup_failed = 0
    skipped_encode_error = 0

    for file_path in progress:
        ok, reason = convert_file(file_path, target_codec, backup=args.backup)

        if ok:
            converted += 1
            if use_progress and not isinstance(progress, list):
                progress.set_postfix_str(f"已转换: {converted}")
        else:
            if reason == "skip_not_utf8":
                skipped_not_utf8 += 1
                print(f"[跳过-非UTF-8] {file_path}")
            elif reason == "skip_write_error":
                skipped_write_error += 1
                print(f"[跳过-写入失败] {file_path}")
            elif reason == "skip_backup_failed":
                skipped_backup_failed += 1
                print(f"[跳过-备份失败] {file_path}")
            elif reason == "skip_encode_error":
                skipped_encode_error += 1
                print(f"[跳过-编码失败] {file_path}")

    # 总结报告
    print("\n===== 完成 =====")
    print(f"成功转换:   {converted}")
    print(f"跳过（非UTF‑8）: {skipped_not_utf8}")
    if skipped_encode_error:
        print(f"跳过（编码异常）: {skipped_encode_error}")
    if skipped_backup_failed:
        print(f"跳过（备份失败）: {skipped_backup_failed}")
    if skipped_write_error:
        print(f"跳过（写入失败）: {skipped_write_error}")
    print("注意: 未在自定义替换表中覆盖的字符已用 '?' 替代。")


if __name__ == "__main__":
    main()