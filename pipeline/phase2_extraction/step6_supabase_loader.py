"""
Step 2.6: Supabase 데이터 적재 스크립트
=======================================
normalized_entities.json, chunks.json → Supabase 5개 테이블

사용법:
  python step6_supabase_loader.py [--phase PHASE_NUM] [--dry-run] [--clean]

환경변수 (.env):
  SUPABASE_URL=https://xxx.supabase.co
  SUPABASE_SERVICE_ROLE_KEY=eyJ...  (⚠️ anon key 아님!)
"""

import json
import os
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

# ─── 경로 설정 ───────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
PHASE2_OUTPUT = PROJECT_ROOT / "phase2_output"
PHASE1_OUTPUT = PROJECT_ROOT / "phase1_output"

ENTITIES_FILE = PHASE2_OUTPUT / "normalized_entities.json"
CHUNKS_FILE = PHASE1_OUTPUT / "chunks.json"

# ─── 환경 로드 ───────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # F3: service_role 필수

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ .env 파일에 SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY를 설정하세요.")
    sys.exit(1)

from supabase import create_client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── 로그 설정 ───────────────────────────────────────
LOG_DIR = PHASE2_OUTPUT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"step6_loader_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log(msg: str):
    """콘솔 + 파일 동시 출력"""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ═══════════════════════════════════════════════════════
# 1. 변환 함수들
# ═══════════════════════════════════════════════════════

def entity_to_row(e: dict) -> dict:
    """entity → graph_entities 행"""
    properties = {}
    for key in ['spec', 'unit', 'quantity', 'confidence', 'code',
                'normalized_name', 'source_method', 'source_chunk_ids']:
        val = e.get(key)
        if val is not None:
            properties[key] = val
    if e.get('properties'):
        properties.update(e['properties'])

    return {
        'id': e['entity_id'],
        'name': e['name'],
        'type': e['type'],
        'properties': properties if properties else {},
        'source_section': e.get('source_section_id'),
    }


def extract_all_relationships(data: dict) -> list[dict]:
    """extractions[].relationships[] → flat 추출
    F1 반영: dedup 키를 6-tuple로 확장 (quantity/unit/per_unit이 다른 관계 보존)
    """
    rows = []
    seen = set()

    for extraction in data.get('extractions', []):
        for rel in extraction.get('relationships', []):
            qty = rel.get('quantity')
            unit = rel.get('unit')
            per_unit = rel.get('per_unit')
            key = (
                rel.get('source_entity_id', ''),
                rel.get('target_entity_id', ''),
                rel.get('type', ''),
                qty, unit, per_unit,
            )
            if key in seen:
                continue
            seen.add(key)

            properties = {}
            for k in ['quantity', 'unit', 'per_unit']:
                v = rel.get(k)
                if v is not None:
                    properties[k] = v
            if rel.get('properties'):
                properties.update(rel['properties'])

            rows.append({
                'source_id': rel['source_entity_id'],
                'target_id': rel['target_entity_id'],
                'relation': rel['type'],
                'properties': properties if properties else {},
                'source_chunk_id': rel.get('source_chunk_id'),
            })

    return rows


def extract_global_relationships(data: dict) -> list[dict]:
    """global_relationships{HAS_CHILD: [...], REFERENCES: [...]} → flat 추출"""
    rows = []
    gr = data.get('global_relationships', {})

    for rel_type, rel_list in gr.items():
        for rel in rel_list:
            rows.append({
                'source_id': rel['source_entity_id'],
                'target_id': rel['target_entity_id'],
                'relation': rel.get('type', rel_type),
                'properties': rel.get('properties', {}),
            })

    return rows


def chunk_to_row(c: dict) -> dict:
    """chunk → graph_chunks 행"""
    return {
        'id': c['chunk_id'],
        'section_id': c.get('section_id'),
        'title': c.get('title'),
        'department': c.get('department'),
        'chapter': c.get('chapter'),
        'section': c.get('section'),
        'text': c.get('text'),
        'tables': c.get('tables'),
        'notes': c.get('notes'),
        'conditions': c.get('conditions'),
        'cross_references': c.get('cross_references'),
        'revision_year': c.get('revision_year'),
        'token_count': c.get('token_count'),
    }


# ═══════════════════════════════════════════════════════
# 2. FK 검증
# ═══════════════════════════════════════════════════════

def validate_fk(entity_ids: set, relationships: list[dict]) -> tuple[list, list]:
    """관계의 source_id, target_id가 엔티티에 존재하는지 검증"""
    valid = []
    orphaned = []

    for rel in relationships:
        src = rel['source_id']
        tgt = rel['target_id']
        if src in entity_ids and tgt in entity_ids:
            valid.append(rel)
        else:
            missing = []
            if src not in entity_ids:
                missing.append(f"source={src}")
            if tgt not in entity_ids:
                missing.append(f"target={tgt}")
            orphaned.append({
                'relation': rel['relation'],
                'missing': ", ".join(missing),
                'source_id': src,
                'target_id': tgt,
            })

    return valid, orphaned


# ═══════════════════════════════════════════════════════
# 3. 클린 로드 (--clean 모드)
# ═══════════════════════════════════════════════════════

def clean_tables(dry_run: bool = False):
    """기존 데이터 전체 삭제 (FK 의존성 순서 준수)
    Why: 재추출 시 이전 관계/엔티티가 누적되는 문제 방지
    """
    tables = [
        'graph_relationships',
        'graph_global_relationships',
        'graph_entities',
        'graph_chunks',
    ]
    for table in tables:
        if dry_run:
            count_resp = supabase.table(table).select('id', count='exact').limit(1).execute()
            log(f"  [DRY RUN] {table}: {count_resp.count}건 삭제 예정")
        else:
            supabase.table(table).delete().neq('id', '__impossible__').execute()
            log(f"  🗑️ {table} 전체 삭제 완료")
            time.sleep(0.5)


# ═══════════════════════════════════════════════════════
# 4. 배치 적재 함수 (F2 반영: upsert/insert 분리)
# ═══════════════════════════════════════════════════════

def batch_upsert(table: str, rows: list[dict], batch_size: int = 500) -> dict:
    """TEXT PK 테이블용 — graph_entities, graph_chunks"""
    total = len(rows)
    success = 0
    errors = []

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        try:
            supabase.table(table).upsert(batch).execute()
            success += len(batch)
            if success % 2000 == 0 or success == total:
                log(f"  [{table}] {success}/{total} upsert 완료")
        except Exception as e:
            errors.append({'batch_start': i, 'error': str(e)[:200]})
            log(f"  ❌ [{table}] 배치 {i}~{i+len(batch)} 실패: {str(e)[:200]}")
        time.sleep(0.1)  # rate limit 방지

    return {'total': total, 'success': success, 'errors': errors}


def batch_insert(table: str, rows: list[dict], batch_size: int = 500) -> dict:
    """SERIAL PK 테이블용 — graph_relationships, graph_global_relationships"""
    total = len(rows)
    success = 0
    errors = []

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        try:
            supabase.table(table).insert(batch).execute()
            success += len(batch)
            if success % 2000 == 0 or success == total:
                log(f"  [{table}] {success}/{total} insert 완료")
        except Exception as e:
            errors.append({'batch_start': i, 'error': str(e)[:200]})
            log(f"  ❌ [{table}] 배치 {i}~{i+len(batch)} 실패: {str(e)[:200]}")
        time.sleep(0.1)

    return {'total': total, 'success': success, 'errors': errors}


# ═══════════════════════════════════════════════════════
# 5. Phase 실행 함수들
# ═══════════════════════════════════════════════════════

def phase2_load_entities(data: dict, dry_run: bool = False) -> dict:
    """Phase 2: 엔티티 적재"""
    log("━━━ Phase 2: 엔티티 적재 ━━━")
    entities = data.get('entities', [])
    rows = [entity_to_row(e) for e in entities]
    log(f"  변환 완료: {len(rows)}건")

    # 타입 분포 확인
    type_counts = {}
    for r in rows:
        t = r['type']
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        log(f"    {t}: {c}")

    if dry_run:
        log("  [DRY RUN] 적재 건너뜀")
        return {'total': len(rows), 'success': 0, 'errors': [], 'dry_run': True}

    result = batch_upsert('graph_entities', rows, batch_size=500)
    log(f"  ✅ 엔티티 적재 완료: {result['success']}/{result['total']}")
    if result['errors']:
        log(f"  ⚠️ 에러 {len(result['errors'])}건")
    return result


def phase3_load_relationships(data: dict, entity_ids: set, dry_run: bool = False) -> dict:
    """Phase 3: 관계 적재"""
    log("━━━ Phase 3: 관계 적재 ━━━")
    all_rels = extract_all_relationships(data)
    log(f"  추출 완료: {len(all_rels)}건 (6-tuple dedup 적용)")

    # FK 검증
    valid, orphaned = validate_fk(entity_ids, all_rels)
    log(f"  FK 검증: valid={len(valid)}, orphaned={len(orphaned)}")

    if orphaned:
        orphan_file = LOG_DIR / "orphaned_relationships.json"
        with open(orphan_file, 'w', encoding='utf-8') as f:
            json.dump(orphaned[:100], f, ensure_ascii=False, indent=2)  # 상위 100건만
        log(f"  ⚠️ orphaned 관계 {len(orphaned)}건 → {orphan_file.name}")

    # 관계 타입 분포
    rel_counts = {}
    for r in valid:
        t = r['relation']
        rel_counts[t] = rel_counts.get(t, 0) + 1
    for t, c in sorted(rel_counts.items(), key=lambda x: -x[1]):
        log(f"    {t}: {c}")

    if dry_run:
        log("  [DRY RUN] 적재 건너뜀")
        return {'total': len(valid), 'success': 0, 'errors': [], 'dry_run': True}

    result = batch_insert('graph_relationships', valid, batch_size=500)
    log(f"  ✅ 관계 적재 완료: {result['success']}/{result['total']}")
    return result


def phase4_load_global_relationships(data: dict, entity_ids: set, dry_run: bool = False) -> dict:
    """Phase 4: 전역 관계 적재"""
    log("━━━ Phase 4: 전역 관계 적재 ━━━")
    all_rels = extract_global_relationships(data)
    log(f"  추출 완료: {len(all_rels)}건")

    # FK 검증
    valid, orphaned = validate_fk(entity_ids, all_rels)
    log(f"  FK 검증: valid={len(valid)}, orphaned={len(orphaned)}")

    if orphaned:
        log(f"  ⚠️ orphaned 전역 관계 {len(orphaned)}건 skip")

    # 관계 타입 분포
    rel_counts = {}
    for r in valid:
        t = r['relation']
        rel_counts[t] = rel_counts.get(t, 0) + 1
    for t, c in sorted(rel_counts.items(), key=lambda x: -x[1]):
        log(f"    {t}: {c}")

    if dry_run:
        log("  [DRY RUN] 적재 건너뜀")
        return {'total': len(valid), 'success': 0, 'errors': [], 'dry_run': True}

    result = batch_insert('graph_global_relationships', valid, batch_size=500)
    log(f"  ✅ 전역 관계 적재 완료: {result['success']}/{result['total']}")
    return result


def phase5_load_chunks(dry_run: bool = False) -> dict:
    """Phase 5: 청크 적재"""
    log("━━━ Phase 5: 청크 적재 ━━━")

    with open(CHUNKS_FILE, 'r', encoding='utf-8') as f:
        chunks_data = json.load(f)

    chunks = chunks_data.get('chunks', [])
    rows = [chunk_to_row(c) for c in chunks]
    log(f"  변환 완료: {len(rows)}건")

    if dry_run:
        log("  [DRY RUN] 적재 건너뜀")
        return {'total': len(rows), 'success': 0, 'errors': [], 'dry_run': True}

    result = batch_upsert('graph_chunks', rows, batch_size=200)
    log(f"  ✅ 청크 적재 완료: {result['success']}/{result['total']}")
    return result


# ═══════════════════════════════════════════════════════
# 6. 메인 실행
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Step 2.6: Supabase 데이터 적재")
    parser.add_argument('--phase', type=int, default=0,
                       help="특정 Phase만 실행 (2~5). 0=전체")
    parser.add_argument('--dry-run', action='store_true',
                        help="데이터 변환만 하고 적재 안 함")
    parser.add_argument('--clean', action='store_true',
                        help="적재 전 기존 데이터 전체 삭제 (재추출 시 사용)")
    args = parser.parse_args()

    log("═" * 60)
    log("Step 2.6: Supabase 데이터 적재 시작")
    log(f"  Supabase URL: {SUPABASE_URL}")
    log(f"  Dry Run: {args.dry_run}")
    log(f"  Clean: {args.clean}")
    log(f"  Phase: {'전체' if args.phase == 0 else args.phase}")
    log("═" * 60)

    # 데이터 로드
    log("📂 normalized_entities.json 로드 중...")
    with open(ENTITIES_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    log(f"  entities: {len(data.get('entities', []))}건")
    log(f"  extractions: {len(data.get('extractions', []))}개")

    # entity_id 집합 (FK 검증용)
    entity_ids = {e['entity_id'] for e in data.get('entities', [])}
    log(f"  entity_ids: {len(entity_ids)}개")

    results = {}
    start_time = time.time()

    # Phase 1: 클린 (선택)
    if args.clean:
        log("━━━ Phase 1: 기존 데이터 삭제 ━━━")
        clean_tables(args.dry_run)
        log("")

    # Phase 2: 엔티티
    if args.phase in (0, 2):
        results['phase2'] = phase2_load_entities(data, args.dry_run)

    # Phase 3: 관계
    if args.phase in (0, 3):
        results['phase3'] = phase3_load_relationships(data, entity_ids, args.dry_run)

    # Phase 4: 전역 관계
    if args.phase in (0, 4):
        results['phase4'] = phase4_load_global_relationships(data, entity_ids, args.dry_run)

    # Phase 5: 청크
    if args.phase in (0, 5):
        results['phase5'] = phase5_load_chunks(args.dry_run)

    elapsed = time.time() - start_time

    log("═" * 60)
    log("📊 최종 결과:")
    for phase_name, result in results.items():
        status = "DRY" if result.get('dry_run') else "OK" if not result['errors'] else "ERR"
        log(f"  {phase_name}: {result['success']}/{result['total']} [{status}]")
    log(f"⏱ 총 소요: {elapsed:.1f}초")
    log(f"📝 로그: {LOG_FILE}")
    log("═" * 60)

    # 결과 요약 저장
    summary_file = LOG_DIR / f"step6_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    log(f"📄 결과 요약: {summary_file}")


if __name__ == "__main__":
    main()
