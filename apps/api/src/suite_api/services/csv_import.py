"""CSV 批量导入解析（第 9 刀数据接入）：纯函数、无框架依赖（便于单测）。

CSV 导入 = 上传通道的批量形态（ADR 0025：不新增来源枚举，source_kind 仍由
端点语义定值 upload，Zendesk 同款口径）。解析规则（spec 第 9 刀 Must 1）：

- utf-8-sig 解码（容忍 Excel 导出的 BOM）；失败属整批不可受理。
- 表头必须含 title 与 content 两列，缺一整批拒绝（路由层转 422 列名）。
- 行数上限 200/次（超限整批拒绝）：操作者手工场景的合理上界，防误传巨型
  文件占机洗。
- 逐行校验（不合格跳过记 reason，不拦整批）：空行 / 空 title / 空 content /
  content 编码后超过 200_000 字节（行级上限，与单份登记 2MB 同方向——批量
  通道单行不该逼近单份全档上限）。
- row 为 1 起数据行号（表头下一行 = 第 1 行），与操作者表格软件所见行对应；
  空行占行号（报告里的行号能对上用户打开文件看到的行）。

空批次（空文件/只有表头/全跳过）不报错：报告即答案。
"""

import csv
import io
from dataclasses import dataclass

# 必需表头列（精确名，剥首尾空白后匹配；列序不限）
REQUIRED_COLUMNS = ("title", "content")
# 单次导入数据行上限（含将被跳过的行：按文件实际行数把关）
MAX_IMPORT_ROWS = 200
# 单行 content 的 UTF-8 字节上限
MAX_CONTENT_BYTES = 200_000


class CsvImportFormatError(Exception):
    """整批不可受理（非 UTF-8 / 缺列 / 超行数）：路由层转 422。"""


@dataclass(frozen=True)
class ImportRow:
    """一条合格数据行：title/content 已剥空白，待走 register_asset。"""

    row: int
    title: str
    content: str


@dataclass(frozen=True)
class SkippedRow:
    """一条被跳过的数据行：行号 + 原因（报告原文给操作者看）。"""

    row: int
    reason: str


def parse_import_csv(data: bytes) -> tuple[list[ImportRow], list[SkippedRow]]:
    """解析 CSV 字节 -> (合格行, 跳过行)。格式不可受理抛 CsvImportFormatError。"""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvImportFormatError("CSV 不是合法 UTF-8 文本（utf-8-sig 解码失败）") from exc

    records = list(csv.reader(io.StringIO(text)))
    if not records:
        return [], []  # 空文件 = 空批次：不报错，报告即答案

    header = [cell.strip() for cell in records[0]]
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise CsvImportFormatError(
            f"表头缺少必需列: {'、'.join(missing)}（须同时包含 title 与 content）"
        )

    data_records = records[1:]
    if len(data_records) > MAX_IMPORT_ROWS:
        raise CsvImportFormatError(
            f"单次导入最多 {MAX_IMPORT_ROWS} 行数据，收到 {len(data_records)} 行"
        )

    title_idx = header.index("title")
    content_idx = header.index("content")
    valid: list[ImportRow] = []
    skipped: list[SkippedRow] = []
    for row_no, record in enumerate(data_records, start=1):
        title = (record[title_idx] if title_idx < len(record) else "").strip()
        content = (record[content_idx] if content_idx < len(record) else "").strip()
        if not title and not content:
            skipped.append(SkippedRow(row=row_no, reason="空行（title 与 content 均为空）"))
        elif not title:
            skipped.append(SkippedRow(row=row_no, reason="title 为空"))
        elif not content:
            skipped.append(SkippedRow(row=row_no, reason="content 为空"))
        elif len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            skipped.append(
                SkippedRow(row=row_no, reason=f"content 超过 {MAX_CONTENT_BYTES} 字节上限")
            )
        else:
            valid.append(ImportRow(row=row_no, title=title, content=content))
    return valid, skipped
