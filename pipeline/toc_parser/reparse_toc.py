# -*- coding: utf-8 -*-
"""
TOC 재파싱 및 JSON 저장 스크립트
"""
import json
import sys
import os

# 같은 폴더의 toc_parser 모듈 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from toc_parser import parse_toc, build_page_to_sections_map

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    toc_path = os.path.join(script_dir, "목차_gemini.md")
    output_path = os.path.join(script_dir, "toc_parsed.json")
    
    print(f"📖 목차 파싱 중: {toc_path}")
    
    # 파싱
    section_map = parse_toc(toc_path)
    page_map = build_page_to_sections_map(section_map)
    
    print(f"✅ {len(section_map)}개 섹션 파싱 완료")
    
    # Chapter 통계
    chapters = {}
    for k, v in section_map.items():
        ch = v.get('chapter', 'UNKNOWN')
        chapters[ch] = chapters.get(ch, 0) + 1
    
    print("\n📊 부문별 섹션 수:")
    for ch, cnt in sorted(chapters.items()):
        print(f"  {ch}: {cnt}개")
    
    # Section 샘플 (끝부분)
    print("\n📋 마지막 10개 섹션:")
    items = list(section_map.items())
    for k, v in items[-10:]:
        display_id = v.get("id", k)
        print(f"  [{k}] {v['chapter']} > {v['section']} > {display_id} {v['title']} (p.{v['page']})")
    
    # JSON 저장
    result = {
        "section_map": section_map,
        "page_map": page_map
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 저장 완료: {output_path}")

if __name__ == "__main__":
    main()
