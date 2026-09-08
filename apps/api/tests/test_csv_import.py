"""CSV 批量导入解析单元测试（第 9 刀）：解析规则全矩阵（纯函数，无 DB）。

覆盖：BOM 容忍 / 缺列（title/content/双缺）/ 空文件与只有表头（空批次不报错）/
空行 / 空 title / 空 content / 超限行数（200 过 201 拒）/ 超长行（边界字节数）/
列序无关与多余列忽略 / 引号包裹字段 / CRLF / 非 UTF-8 / 首尾空白剥除 /
行号口径（1 起数据行号，空行占号）。
"""

import inspect

import pytest

from suite_api.routes.assets import import_csv
from suite_api.services.csv_import import (
    MAX_CONTENT_BYTES,
    MAX_IMPORT_ROWS,
    CsvImportFormatError,
    parse_import_csv,
)


def test_import_csv_route_is_sync() -> None:
    """同步 def：FastAPI 丢线程池，不冻事件循环。"""
    assert inspect.iscoroutinefunction(import_csv) is False


def test_bom_tolerated() -> None:
    """Excel 导出常带 BOM：utf-8-sig 剥掉，表头照常识别。"""
    data = "title,content\n退款说明,七天内可退".encode("utf-8-sig")
    valid, skipped = parse_import_csv(data)
    assert skipped == []
    assert [(r.row, r.title, r.content) for r in valid] == [(1, "退款说明", "七天内可退")]


def test_empty_file_is_empty_batch() -> None:
    """空文件 = 空批次：不报错，报告即答案（spec Must 2）。"""
    assert parse_import_csv(b"") == ([], [])


def test_header_only_is_empty_batch() -> None:
    assert parse_import_csv(b"title,content\n") == ([], [])


@pytest.mark.parametrize(
    "header",
    [
        b"name,content\n",  # 缺 title
        b"title,body\n",  # 缺 content
        b"name,body\n",  # 双缺
        b"Title,Content\n",  # 大小写敏感：不算命中
    ],
)
def test_missing_columns_rejected(header: bytes) -> None:
    with pytest.raises(CsvImportFormatError, match="表头缺少必需列.*title.*content"):
        parse_import_csv(header + b"a,b\n")


def test_missing_columns_error_names_the_column() -> None:
    """缺哪列点名哪列：只缺 content 时 detail 不提 title 缺失。"""
    with pytest.raises(CsvImportFormatError) as excinfo:
        parse_import_csv("title,备注\na,b\n".encode())
    message = str(excinfo.value)
    assert "表头缺少必需列: content" in message
    assert "表头缺少必需列: content、title" not in message


def test_invalid_utf8_rejected() -> None:
    with pytest.raises(CsvImportFormatError, match="UTF-8"):
        parse_import_csv(b"title,content\n\xff\xfe,b\n")


def test_blank_row_skipped_with_reason() -> None:
    data = b"title,content\nok1,c1\n\nok2,c2"
    valid, skipped = parse_import_csv(data)
    assert [(r.row, r.title) for r in valid] == [(1, "ok1"), (3, "ok2")]  # 空行占行号
    assert [(s.row, s.reason) for s in skipped] == [(2, "空行（title 与 content 均为空）")]


def test_blank_row_of_commas_is_blank() -> None:
    """只有分隔符没有值的行（,,,）同样是空行，不是「title 为空」。"""
    _, skipped = parse_import_csv(b"title,content\n,,\n")
    assert skipped[0].reason.startswith("空行")


def test_empty_title_skipped() -> None:
    _, skipped = parse_import_csv("title,content\n,有内容没标题\n".encode())
    assert [(s.row, s.reason) for s in skipped] == [(1, "title 为空")]


def test_empty_content_skipped() -> None:
    _, skipped = parse_import_csv("title,content\n有标题,\n".encode())
    assert [(s.row, s.reason) for s in skipped] == [(1, "content 为空")]


def test_whitespace_only_cells_treated_as_empty() -> None:
    _, skipped = parse_import_csv(b"title,content\n  ,\t\n")
    assert skipped[0].reason == "空行（title 与 content 均为空）"


def test_row_limit_boundary() -> None:
    """200 行过、201 行拒：按文件实际数据行数把关（含将被跳过的行）。"""
    ok_body = b"".join(b"t%d,c%d\n" % (i, i) for i in range(MAX_IMPORT_ROWS))
    valid, _ = parse_import_csv(b"title,content\n" + ok_body)
    assert len(valid) == MAX_IMPORT_ROWS

    over_body = b"".join(b"t%d,c%d\n" % (i, i) for i in range(MAX_IMPORT_ROWS + 1))
    with pytest.raises(CsvImportFormatError, match="最多 200 行"):
        parse_import_csv(b"title,content\n" + over_body)


def test_oversize_content_skipped() -> None:
    """content 编码后 > 200_000 字节跳过；恰好 200_000 合格（边界含）。"""
    at_limit = "字" * (MAX_CONTENT_BYTES // 3) + "a" * (MAX_CONTENT_BYTES % 3)  # 恰 200_000 字节
    assert len(at_limit.encode("utf-8")) == MAX_CONTENT_BYTES
    over = at_limit + "字"
    data = f"title,content\n标题,{over}\n".encode()
    valid, skipped = parse_import_csv(data)
    assert valid == []
    assert skipped[0].row == 1
    assert skipped[0].reason == "content 超过 200000 字节上限"

    ok_data = f"title,content\n标题,{at_limit}\n".encode()
    valid_ok, skipped_ok = parse_import_csv(ok_data)
    assert skipped_ok == []
    assert valid_ok[0].content == at_limit


def test_column_order_and_extra_columns() -> None:
    """列序无关（content 在前也行）；未声明的多余列忽略。"""
    data = b"content,extra,title\nc1,x,t1\n"
    valid, skipped = parse_import_csv(data)
    assert skipped == []
    assert (valid[0].title, valid[0].content) == ("t1", "c1")


def test_short_row_treated_as_missing_cells() -> None:
    """行比表头短：缺的格子按空值处理（title 在前，行里只有一格=有标题无内容）。"""
    _, skipped = parse_import_csv("title,content\n只有标题\n".encode())
    assert skipped[0].reason == "content 为空"


def test_quoted_fields_with_comma_and_newline() -> None:
    """csv 标准库语义：引号内的逗号/换行不拆列，是 content 正文的一部分。"""
    valid, skipped = parse_import_csv('title,content\n"带,逗号","第一行\n第二行"\n'.encode())
    assert skipped == []
    assert valid[0].content == "第一行\n第二行"
    assert valid[0].title == "带,逗号"


def test_crlf_line_endings() -> None:
    data = b"title,content\r\nok1,c1\r\nok2,c2\r\n"
    valid, skipped = parse_import_csv(data)
    assert skipped == []
    assert [(r.row, r.title) for r in valid] == [(1, "ok1"), (2, "ok2")]


def test_surrounding_whitespace_stripped() -> None:
    valid, _ = parse_import_csv("title,content\n  标题  ,  内容  \n".encode())
    assert (valid[0].title, valid[0].content) == ("标题", "内容")


def test_row_numbers_are_data_rows_not_file_lines() -> None:
    """row 为 1 起数据行号（表头不计），跳过行也占号——报告行号对得上表格软件所见行。"""
    data = "title,content\n,空标题行\ngood,内容\n\nanother,内容2".encode()
    valid, skipped = parse_import_csv(data)
    assert [r.row for r in valid] == [2, 4]
    assert [s.row for s in skipped] == [1, 3]
