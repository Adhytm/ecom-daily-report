"""文件解析层测试（T03）：编码 / 表头定位 / 清洗 / 空行 / 尾行。"""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from backend.app.core import EncodingError, FileParseError
from backend.app.core.file_parser import parse_file
from backend.app.schemas.adapter import FileMatching


def _fm(**overrides) -> FileMatching:
    base = dict(
        extensions=[".csv", ".xlsx", ".xlsm", ".xls", ".tsv", ".txt"],
        encoding_candidates=["utf-8-sig", "gb18030", "utf-8"],
        header_row="auto",
        auto_header_hints=["商品ID", "支付金额", "统计时间"],
        skip_footer=0,
        detect_keywords=["测试"],
    )
    base.update(overrides)
    return FileMatching(**base)


def _csv_bytes(lines: list[str], encoding: str, bom: bool = False) -> bytes:
    text = "\r\n".join(lines) + "\r\n"
    data = text.encode(encoding)
    return (b"\xef\xbb\xbf" + data) if bom else data


def _parse(raw: bytes, filename: str, fm: FileMatching):
    return parse_file(raw, filename, fm)


# ---------------------------------------------------------------------------
# 编码
# ---------------------------------------------------------------------------


def test_gbk_csv():
    raw = _csv_bytes(
        ["统计时间,商品ID,支付金额", "2026-09-06,SKU001,123.45"], "gb18030"
    )
    df, meta = _parse(raw, "a.csv", _fm())
    assert meta.encoding == "gb18030"
    assert list(df.columns) == ["统计时间", "商品ID", "支付金额"]
    assert len(df) == 1


def test_utf8_bom_csv():
    raw = _csv_bytes(
        ["统计时间,商品ID,支付金额", "2026-09-06,SKU001,10"], "utf-8", bom=True
    )
    df, meta = _parse(raw, "a.csv", _fm(encoding_candidates=["utf-8-sig", "utf-8"]))
    assert meta.encoding == "utf-8-sig"
    assert df.columns[0] == "统计时间"  # BOM 已剥离


def test_utf8_csv():
    raw = _csv_bytes(
        ["统计时间,商品ID,支付金额", "2026-09-06,SKU001,10"], "utf-8"
    )
    df, meta = _parse(raw, "a.csv", _fm(encoding_candidates=["utf-8"]))
    assert meta.encoding == "utf-8"
    assert len(df) == 1


def test_gb18030_csv():
    raw = _csv_bytes(
        ["统计时间,商品ID,支付金额", "2026-09-06,SKU001,10"], "gb18030"
    )
    df, meta = _parse(raw, "a.csv", _fm(encoding_candidates=["gb18030", "utf-8"]))
    assert meta.encoding == "gb18030"


def test_encoding_failure_contains_filename():
    # 全字节段的二进制垃圾：候选编码与 charset_normalizer 均无法解码
    raw = bytes(range(0, 256)) * 4
    with pytest.raises(EncodingError) as exc_info:
        _parse(raw, "神秘文件.csv", _fm(encoding_candidates=["utf-8", "gb18030"]))
    assert "神秘文件.csv" in str(exc_info.value)


def test_unsupported_extension():
    with pytest.raises(FileParseError):
        _parse(b"abc", "data.pdf", _fm())


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------


def _make_xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_basic():
    raw = _make_xlsx(
        [
            ["统计时间", "商品ID", "支付金额"],
            ["2026-09-06", "SKU001", "99.90"],
            ["2026-09-06", "SKU002", "59.90"],
        ]
    )
    df, meta = _parse(raw, "a.xlsx", _fm())
    assert len(df) == 2
    assert df.iloc[1]["商品ID"] == "SKU002"


def test_xlsx_header_not_first_row():
    raw = _make_xlsx(
        [
            ["拼多多商家后台商品数据导出"],
            ["导出时间：2026-09-06 10:00"],
            ["统计日期", "商品ID", "成团金额(GMV)"],
            ["2026-09-06", "SKU001", "88.00"],
        ]
    )
    df, meta = _parse(
        raw, "a.xlsx",
        _fm(header_row="auto", auto_header_hints=["商品ID", "成团金额(GMV)"]),
    )
    assert meta.header_row_index == 3
    assert len(df) == 1


# ---------------------------------------------------------------------------
# 表头定位与清洗
# ---------------------------------------------------------------------------


def test_header_row_auto_with_description_lines():
    raw = _csv_bytes(
        [
            "本报表由测试系统导出",            # 说明行 1
            "导出时间：2026-09-06 10:00:00",   # 说明行 2
            "统计时间,商品ID,支付金额",
            "2026-09-06,SKU001,10",
        ],
        "utf-8",
    )
    df, meta = _parse(raw, "a.csv", _fm())
    assert meta.header_row_index == 3
    assert len(df) == 1


def test_header_auto_no_hit_falls_back_with_warning():
    """零命中不再抛错：降级猜表头，并把"为什么猜"写进 warnings（兼容性回归防线）。"""
    raw = _csv_bytes(["a,b,c", "1,2,3"], "utf-8")
    df, meta = _parse(raw, "a.csv", _fm())
    assert list(df.columns) == ["a", "b", "c"]
    assert meta.header_row_index == 1
    assert len(df) == 1
    assert any("auto_header_hints" in w for w in meta.warnings)
    assert any("a" in w for w in meta.warnings)  # 附了文件前几行预览


def test_generic_xlsx_without_hints_is_parsed():
    """不含任何平台特征词的通用销售表：必须能解析出表头，交给下游判断。"""
    raw = _make_xlsx([["日期", "商品名", "数量"], ["2026-09-06", "甲", "3"]])
    df, meta = _parse(raw, "通用.xlsx", _fm())
    assert list(df.columns) == ["日期", "商品名", "数量"]
    assert len(df) == 1
    assert any("auto_header_hints" in w for w in meta.warnings)


def test_header_fallback_skips_title_and_blank_rows():
    """兜底要跳过"单列标题行 + 空行"，落到真正的表头行。"""
    raw = _make_xlsx(
        [
            ["示例公司 电商销售日报 2026-09-06（周日）"],
            [],
            ["指标", "今日", "昨日"],
            ["GMV", "465234.01", "644878.59"],
        ]
    )
    df, meta = _parse(raw, "日报.xlsx", _fm())
    assert meta.header_row_index == 3
    assert list(df.columns) == ["指标", "今日", "昨日"]
    assert len(df) == 1


def test_header_row_fixed_integer():
    raw = _csv_bytes(
        ["垃圾行1", "统计时间,商品ID,支付金额", "2026-09-06,SKU001,10"], "utf-8"
    )
    df, meta = _parse(raw, "a.csv", _fm(header_row=2))
    assert meta.header_row_index == 2
    assert list(df.columns) == ["统计时间", "商品ID", "支付金额"]


def test_header_clean_invisible_chars_and_newlines():
    # 表头单元格内含换行（CSV 引号包裹）、前后空格与零宽字符
    raw = _csv_bytes(
        ['"统计\r\n时间", 商品ID ,​支付金额​', "2026-09-06,SKU001,10"],
        "utf-8",
    )
    df, meta = _parse(raw, "a.csv", _fm())
    assert list(df.columns) == ["统计时间", "商品ID", "支付金额"]


def test_duplicate_headers_get_suffix():
    raw = _csv_bytes(
        ["统计时间,商品ID,商品ID,支付金额", "2026-09-06,S1,备注值,10"], "utf-8"
    )
    df, meta = _parse(raw, "a.csv", _fm())
    assert list(df.columns) == ["统计时间", "商品ID", "商品ID_2", "支付金额"]
    assert any("重复表头" in w for w in meta.warnings)
    assert df.iloc[0]["商品ID_2"] == "备注值"


# ---------------------------------------------------------------------------
# 空行 / 尾行
# ---------------------------------------------------------------------------


def test_empty_rows_dropped():
    raw = _csv_bytes(
        [
            "统计时间,商品ID,支付金额",
            "2026-09-06,SKU001,10",
            "",
            "   ,  ,",
            "2026-09-06,SKU002,20",
        ],
        "utf-8",
    )
    df, meta = _parse(raw, "a.csv", _fm())
    assert len(df) == 2
    assert meta.dropped_empty_rows == 2


def test_skip_footer():
    raw = _csv_bytes(
        [
            "统计时间,商品ID,支付金额",
            "2026-09-06,SKU001,10",
            "2026-09-06,SKU002,20",
            "以上数据仅供参考",
            "Generated by TestSystem",
        ],
        "utf-8",
    )
    df, meta = _parse(raw, "a.csv", _fm(skip_footer=2))
    assert len(df) == 2
    assert any("尾部" in w for w in meta.warnings)


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------


def test_header_only_file():
    raw = _csv_bytes(["统计时间,商品ID,支付金额"], "utf-8")
    df, meta = _parse(raw, "a.csv", _fm())
    assert len(df) == 0
    assert list(df.columns) == ["统计时间", "商品ID", "支付金额"]


def test_quoted_csv_with_comma_in_value():
    raw = _csv_bytes(
        ['统计时间,商品ID,支付金额', '2026-09-06,"SKU,001",10'], "utf-8"
    )
    df, _ = _parse(raw, "a.csv", _fm())
    assert df.iloc[0]["商品ID"] == "SKU,001"


def test_parse_meta_fields():
    raw = _csv_bytes(["统计时间,商品ID,支付金额", "2026-09-06,S1,1"], "utf-8")
    _, meta = _parse(raw, "a.csv", _fm(encoding_candidates=["utf-8"]))
    assert meta.encoding == "utf-8"
    assert meta.header_row_index == 1
    assert meta.detected_columns == ["统计时间", "商品ID", "支付金额"]
    assert meta.dropped_empty_rows == 0
    assert isinstance(meta.warnings, list)


# ---------------------------------------------------------------------------
# 老式 .xls（BIFF）与内容路由
# ---------------------------------------------------------------------------


def test_real_xls_routed_to_xls_reader():
    """BIFF 头必须走 _read_xls_rows，而不是被丢给 openpyxl。"""
    from backend.app.core.file_parser import _is_biff

    assert _is_biff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64) is True
    assert _is_biff(b"PK\x03\x04rest") is False


def test_broken_excel_gives_business_error_not_badzipfile():
    """损坏的 xlsx 必须抛 FileParseError，不能漏出 zipfile.BadZipFile。"""
    with pytest.raises(FileParseError):
        _parse(b"not a zip at all", "坏文件.xlsx", _fm())


def test_xlsx_renamed_as_xls_still_works():
    """后缀是 .xls 但内容是 zip → 仍应正常解析（按内容路由）。"""
    raw = _make_xlsx([["统计时间", "商品ID", "支付金额"], ["2026-09-06", "S1", "10"]])
    df, meta = _parse(raw, "改错后缀.xls", _fm())
    assert len(df) == 1


def test_real_xls_end_to_end():
    """用 xlwt 造一个真 BIFF .xls → 应完整解析（P0-1 端到端）。"""
    xlwt = pytest.importorskip("xlwt")

    book = xlwt.Workbook()
    ws = book.add_sheet("数据")
    for r, row in enumerate(
        [["统计时间", "商品ID", "支付金额"], ["2026-09-06", "S1", "10"]]
    ):
        for c, v in enumerate(row):
            ws.write(r, c, v)
    buf = io.BytesIO()
    book.save(buf)

    df, meta = _parse(buf.getvalue(), "真老式.xls", _fm())
    assert meta.encoding == "xls"
    assert len(df) == 1
    assert df.iloc[0]["商品ID"] == "S1"


# ---------------------------------------------------------------------------
# .xlsm / .tsv / .txt
# ---------------------------------------------------------------------------


def test_tsv_with_tab_delimiter():
    raw = "统计时间\t商品ID\t支付金额\r\n2026-09-06\tSKU001\t10\r\n".encode("utf-8")
    df, meta = _parse(raw, "a.tsv", _fm())
    assert list(df.columns) == ["统计时间", "商品ID", "支付金额"]
    assert len(df) == 1


def test_txt_tab_delimited():
    raw = "统计时间\t商品ID\t支付金额\r\n2026-09-06\tSKU001\t10\r\n".encode("gb18030")
    df, _ = _parse(raw, "导出数据.txt", _fm())
    assert len(df) == 1


def test_xlsm_accepted():
    raw = _make_xlsx([["统计时间", "商品ID", "支付金额"], ["2026-09-06", "S1", "10"]])
    df, _ = _parse(raw, "带宏.xlsm", _fm())
    assert len(df) == 1


# ---------------------------------------------------------------------------
# FileMatching.extensions 生效
# ---------------------------------------------------------------------------


def test_adapter_declared_extensions_win():
    """适配器只声明 .csv 时，.xlsx 应被拒绝。"""
    fm = _fm(extensions=[".csv"])
    with pytest.raises(FileParseError) as e:
        _parse(_make_xlsx([["统计时间", "商品ID", "支付金额"]]), "a.xlsx", fm)
    assert "不支持的文件类型" in str(e.value)


def test_no_declared_extensions_falls_back_to_global():
    fm = _fm(extensions=[])
    raw = "统计时间,商品ID,支付金额\r\n2026-09-06,S1,10\r\n".encode("utf-8")
    df, _ = _parse(raw, "a.csv", fm)
    assert len(df) == 1


# ---------------------------------------------------------------------------
# 多 Sheet 支持
# ---------------------------------------------------------------------------


def _make_multi_sheet(sheets: dict[str, list[list]]) -> bytes:
    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        for r in rows:
            ws.append(r)
        first = False
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_multi_sheet_auto_picks_data_sheet():
    """首个 Sheet 是说明页，数据在第二页 → auto 应挑中第二页。"""
    raw = _make_multi_sheet({
        "说明": [["本表为导出说明"], ["导出时间：2026-09-06"]],
        "商品效果": [["统计时间", "商品ID", "支付金额"], ["2026-09-06", "S1", "10"]],
    })
    fm = _fm(sheet="auto", auto_header_hints=["商品ID", "支付金额"])
    df, meta = _parse(raw, "多sheet.xlsx", fm)
    assert meta.sheet_name == "商品效果"
    assert meta.sheet_names == ["说明", "商品效果"]
    assert len(df) == 1


def test_multi_sheet_explicit_name():
    raw = _make_multi_sheet({
        "汇总": [["统计时间", "商品ID", "支付金额"], ["2026-09-06", "S9", "77"]],
        "明细": [["统计时间", "商品ID", "支付金额"], ["2026-09-06", "S1", "10"]],
    })
    df, meta = _parse(raw, "多sheet.xlsx", _fm(sheet="明细"))
    assert meta.sheet_name == "明细"
    assert df.iloc[0]["商品ID"] == "S1"


def test_multi_sheet_missing_name_lists_available():
    raw = _make_multi_sheet({"汇总": [["统计时间", "商品ID", "支付金额"]]})
    with pytest.raises(FileParseError) as e:
        _parse(raw, "多sheet.xlsx", _fm(sheet="不存在"))
    assert "available_sheets" in (e.value.detail or {})


def test_single_sheet_default_unchanged():
    """单 Sheet 回归：auto 不得改变原有行为。"""
    raw = _make_xlsx([["统计时间", "商品ID", "支付金额"], ["2026-09-06", "S1", "10"]])
    df, meta = _parse(raw, "a.xlsx", _fm(sheet="auto"))
    assert len(df) == 1 and meta.header_row_index == 1


def test_multi_sheet_no_hit_falls_back_to_first_sheet():
    """一页都没命中时回退首个工作表（改造前行为），不得整份文件报废。"""
    raw = _make_multi_sheet({
        "日报总览": [["示例公司 日报"], [], ["指标", "今日"], ["GMV", "1"]],
        "分SKU": [["SKU", "商品名", "GMV"], ["SKU1", "甲", "1"]],
    })
    df, meta = _parse(raw, "日报.xlsx", _fm(auto_header_hints=["商品ID", "支付金额"]))
    assert meta.sheet_name == "日报总览"
    assert meta.sheet_names == ["日报总览", "分SKU"]
    assert list(df.columns) == ["指标", "今日"]
    assert any("auto_header_hints" in w for w in meta.warnings)


# ---------------------------------------------------------------------------
# 兼容性回归：改造前可解析、改造后一度被拦死的"无平台特征词"文件
# ---------------------------------------------------------------------------


def test_regression_generic_report_roundtrip(client):
    """本系统自己导出的多 Sheet 日报：上传要能进到"识别平台"阶段并如实登记。

    回归点：曾报「无法在任何工作表中自动定位表头行」，平台识别根本没机会运行。
    """
    raw = _make_multi_sheet({
        "日报总览": [["示例公司 电商销售日报 2026-09-06"], [], ["指标", "今日"], ["GMV", "1"]],
        "分SKU": [["SKU", "商品名", "GMV"], ["SKU1", "甲", "1"]],
        "口径说明": [["数据来源"], ["· 数据来源：某平台"]],
    })
    resp = client.post(
        "/api/uploads",
        files={"files": ("电商销售日报_示例.xlsx", raw,
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 400
    err = resp.json()["error"]
    # 关键：必须走到"识别平台"这一步，而不是卡在表头定位
    assert "无法自动识别平台" in err["message"]
    assert "无法在任何工作表中自动定位表头行" not in err["message"]
    upload_id = err["detail"]["files"][0]["error"]["detail"]["upload_id"]

    # 登记为 failed 且持有原始文件 → 用户改选平台可复活（reparse 不再是死路）
    listed = client.get("/api/uploads", params={"status": "failed"}).json()["data"]["items"]
    assert any(x["id"] == upload_id for x in listed)

    # 手选平台后给出的是"缺哪几列"的可行动错误，而不是含糊的表头定位失败
    re = client.post(f"/api/uploads/{upload_id}/reparse", json={"platform_key": "doudian"})
    assert re.status_code == 400
    body = re.json()["error"]
    assert body["code"] == "MISSING_REQUIRED_COLUMN"
    assert "缺少必需列" in body["message"]
    assert body["detail"]["file_columns"]  # 附上实际读到的列名


def test_regression_platform_samples_unchanged(client):
    """回归对照：4 平台导出文件必须仍按 hints 命中路径解析，行为与基线一致。"""
    cases = [
        ("抖店罗盘_商品分析.csv", "doudian", ["统计时间", "商品ID", "支付金额"], 0),
        ("京东商智_商品分析.csv", "jd", ["日期", "商品编号", "下单金额"], 1),
    ]
    for fname, expect_platform, cols, pad in cases:
        lines = ["报表导出说明"] * pad + [",".join(cols), "2026-09-06,S1,10"]
        raw = _csv_bytes(lines, "utf-8")
        resp = client.post("/api/uploads", files={"files": (fname, raw, "text/csv")})
        assert resp.status_code == 200, resp.text
        item = resp.json()["data"]["files"][0]
        assert item["detected_platform"] == expect_platform
        # 命中路径不得产生兜底 warning
        assert not any("auto_header_hints" in w for w in item["parse_meta"]["warnings"])


# ---------------------------------------------------------------------------
# 报错可行动化
# ---------------------------------------------------------------------------


def test_error_detail_contains_preview():
    """表头定位零命中时，文件前几行预览仍要透出（供用户自查），只是改为 warning。"""
    raw = "foo,bar,baz\r\n1,2,3\r\n".encode("utf-8")
    _, meta = _parse(raw, "a.csv", _fm())
    joined = " ".join(meta.warnings)
    assert "foo" in joined and "bar" in joined and "baz" in joined


def test_unsupported_extension_message_lists_all():
    with pytest.raises(FileParseError) as e:
        _parse(b"abc", "data.pdf", _fm())
    assert "PDF" not in str(e.value)      # 不是支持格式
    assert ".csv" in str(e.value.detail.get("supported"))  # 支持清单可见
