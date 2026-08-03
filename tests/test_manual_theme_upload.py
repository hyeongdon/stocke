"""수동 테마 매핑 텍스트/테이블 파서 테스트."""

from utils.theme_map_store import (
    parse_manual_theme_mapping_file,
    parse_manual_theme_mapping_table,
    parse_manual_theme_mapping_text,
)


def test_parse_pipe_text_with_header_and_trailing_comma():
    text = """종목코드 | 테마
000660 | 반도체,SK,
005935 | 반도체,우선주
"""
    parsed = parse_manual_theme_mapping_text(text)
    assert parsed["row_count"] == 2
    assert parsed["errors"] == []
    assert parsed["rows"][0] == {
        "line": 2,
        "stock_code": "000660",
        "themes": ["반도체", "SK"],
    }
    assert parsed["rows"][1]["stock_code"] == "005935"
    assert parsed["rows"][1]["themes"] == ["반도체", "우선주"]


def test_parse_skips_bad_lines():
    text = """
# comment
foobar
123 | 
000660 | AI
"""
    parsed = parse_manual_theme_mapping_text(text)
    assert parsed["row_count"] == 1
    assert parsed["rows"][0]["stock_code"] == "000660"
    assert len(parsed["errors"]) >= 2


def test_parse_table_records():
    parsed = parse_manual_theme_mapping_table(
        [
            {"종목코드": "660", "테마": "반도체, HBM"},
            {"종목코드": "005930", "테마": "반도체"},
        ]
    )
    assert parsed["row_count"] == 2
    assert parsed["rows"][0]["stock_code"] == "000660"
    assert parsed["rows"][0]["themes"] == ["반도체", "HBM"]


def test_parse_txt_file_bytes():
    content = "종목코드 | 테마\n000660 | 반도체,SK,\n".encode("utf-8")
    parsed = parse_manual_theme_mapping_file("manual.txt", content)
    assert parsed["row_count"] == 1
    assert parsed["rows"][0]["themes"] == ["반도체", "SK"]
