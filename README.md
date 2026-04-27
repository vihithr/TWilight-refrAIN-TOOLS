# TWilight refrAIN TOOLS

中文 | [English](#english)

用于 `TWilight refrAIN` 的解包/封包、脚本文本处理、编码转换与汉化字体注入相关工具集合。  
本仓库面向学习、逆向研究与个人本地化流程整理，请勿用于非法分发或侵权用途。

## 功能概览

- `arc_idx_tool.py`：核心命令行工具，支持归档 `unpack / pack / list`。
- `arc_idx_tool_gui.py`：Tkinter 图形界面，便于可视化操作封包流程。
- `fix_dialog_alignment.py`：修复 `<setString>/<addString>` 对话内容对齐与控制符问题。
- `convert_utf8_to_legacy.py`：批量将 UTF-8 文本转为 `GBK` 或 `Shift-JIS`，含替换表。
- `batch_convert_zh_txt_to_xml.py`：批量将 `*.zh.txt` 转为 `*.xml`，并清理内容中的 `.zh` 标记。
- `dinput8_min.cpp`：最小化字体 Hook 的 `dinput8` 代理 DLL 源码。
- `dinput8.dll`：编译产物示例（可按本地环境重编译）。

## 环境要求

- Windows 10/11
- Python 3.9+
- 可选：MSVC/MinGW（用于编译 DLL）

## 快速开始

```bash
git clone <your-repo-url>
cd "TWilight refrAIN TOOLS"
python arc_idx_tool.py --help
python arc_idx_tool_gui.py
```

## 常见命令

```bash
# Script alignment fix
python fix_dialog_alignment.py --input your_script.xml --inplace --backup

# UTF-8 -> GBK / Shift-JIS conversion
python convert_utf8_to_legacy.py -d ./script -e gbk --backup --ext .txt .xml

# Batch *.zh.txt -> *.xml
python batch_convert_zh_txt_to_xml.py --source-dir ./script --output-dir ./xml_out
```

## DLL 注入说明

`dinput8_min.cpp` 通过代理系统 `dinput8.dll` 并在进程加载时执行字体相关 Hook，用于将部分默认字库设置重定向到可显示中文的字体。  
请在可控测试环境中使用，并始终先备份原文件。

## 合规与安全

- 请遵守当地法律法规与游戏使用条款。
- 本仓库不提供受版权保护的游戏资源文件。
- 安全策略见 `SECURITY.md`。

## 贡献

欢迎提交 Issue / PR。贡献前请先阅读 `CONTRIBUTING.md`。

## 许可证

本项目使用 MIT 协议，详见 `LICENSE`。

---

## English

This repository contains tooling for `TWilight refrAIN` archive unpack/repack, script text processing, encoding conversion, and font-hook based localization injection.

This project is intended for learning, reverse-engineering research, and personal localization workflow organization. Do not use it for illegal redistribution or infringement.

## Features

- `arc_idx_tool.py`: Core CLI for archive `unpack / pack / list`.
- `arc_idx_tool_gui.py`: Tkinter GUI wrapper for common archive workflows.
- `fix_dialog_alignment.py`: Fixes alignment/control-token issues inside `<setString>/<addString>` content.
- `convert_utf8_to_legacy.py`: Batch convert UTF-8 text files to `GBK` or `Shift-JIS` with replacement rules.
- `batch_convert_zh_txt_to_xml.py`: Batch convert `*.zh.txt` to `*.xml` and remove `.zh` markers in content.
- `dinput8_min.cpp`: Minimal font-hook proxy `dinput8` DLL source.
- `dinput8.dll`: Example compiled binary (rebuild in your own environment if needed).

## Requirements

- Windows 10/11
- Python 3.9+
- Optional: MSVC/MinGW for DLL compilation

## Quick Start

```bash
git clone <your-repo-url>
cd "TWilight refrAIN TOOLS"
python arc_idx_tool.py --help
python arc_idx_tool_gui.py
```

## Typical Commands

```bash
# Fix dialog alignment
python fix_dialog_alignment.py --input your_script.xml --inplace --backup

# UTF-8 -> GBK / Shift-JIS
python convert_utf8_to_legacy.py -d ./script -e gbk --backup --ext .txt .xml

# Batch *.zh.txt -> *.xml
python batch_convert_zh_txt_to_xml.py --source-dir ./script --output-dir ./xml_out
```

## DLL Note

`dinput8_min.cpp` proxies system `dinput8.dll` and installs font-related hooks on process attach to improve Chinese font rendering compatibility.  
Use only in controlled test environments and always keep backups.

## Compliance and Security

- Follow local laws and game EULA/terms.
- No copyrighted game assets are distributed in this repository.
- See `SECURITY.md` for policy details.

## License

Licensed under MIT. See `LICENSE`.
