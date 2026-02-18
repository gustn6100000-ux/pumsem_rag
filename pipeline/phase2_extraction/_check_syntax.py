# -*- coding: utf-8 -*-
"""4C-1 수정 구문 검증 스크립트"""
import ast
import sys

try:
    with open("step1_table_extractor.py", encoding="utf-8") as f:
        source = f.read()
    ast.parse(source)
    print("SYNTAX OK")
    print(f"Total lines: {len(source.splitlines())}")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")
    sys.exit(1)

# 간단한 import 테스트
try:
    from step1_table_extractor import (
        normalize_entity_name,
        _determine_column_label,
        _extract_sub_title,
    )
    
    # (e) 공백 정규화 테스트
    assert normalize_entity_name("42 kg/cm2") == "42kg/cm2", f"Got: {normalize_entity_name('42 kg/cm2')}"
    assert normalize_entity_name("인 력") == "인력", f"Got: {normalize_entity_name('인 력')}"
    print("(e) normalize OK")
    
    # (a) SCH 조건부 테스트
    assert _determine_column_label("40", "13-2-3", "강관용접") == "SCH 40"
    assert _determine_column_label("40", "8-2-12", "크러셔") == "40"
    assert _determine_column_label("측량 기술자", "9-5-4", "수치지도 작성") == "측량 기술자"
    print("(a) column_label OK")
    
    # (c) 소제목 테스트
    assert _extract_sub_title("1. 전기아크용접(V형)") == "V형"
    assert _extract_sub_title("2. 전기아크용접(U형)") == "U형"
    assert _extract_sub_title("일반 텍스트") is None
    print("(c) sub_title OK")
    
    print("\nALL TESTS PASSED")
except Exception as e:
    print(f"TEST FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
