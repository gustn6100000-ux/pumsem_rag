"""
Step 2.7: 임베딩 생성기 (graph_entities + graph_chunks)
======================================================
Gemini embedding-001 API로 768차원 벡터 생성 → Supabase UPDATE

사용법:
  python step7_embedding_generator.py [--target entities|chunks|all] [--batch-size N] [--dry-run]

환경변수 (.env):
  GEMINI_API_KEY=...               → Billing ON 키 (RPM 1,500)
  SUPABASE_URL=https://xxx.supabase.co
  SUPABASE_SERVICE_ROLE_KEY=eyJ...  (⚠️ anon key 아님!)
"""

import json
import math
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

# ─── 환경 로드 ───────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not GEMINI_API_KEY:
    print("❌ .env 파일에 GEMINI_API_KEY를 설정하세요.")
    sys.exit(1)
if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ .env 파일에 SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY를 설정하세요.")
    sys.exit(1)

from supabase import create_client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── Gemini 클라이언트 ────────────────────────────────
from google import genai
from google.genai import types

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# ─── 상수 ─────────────────────────────────────────────
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768
MAX_RETRIES = 5
INITIAL_BACKOFF = 1.0   # 초
MAX_BACKOFF = 60.0       # 초

# ─── 로그 설정 ───────────────────────────────────────
LOG_DIR = PHASE2_OUTPUT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"step7_embedding_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log(msg: str):
    """콘솔 + 파일 동시 출력"""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ═══════════════════════════════════════════════════════
# 1. 임베딩 텍스트 빌더
# ═══════════════════════════════════════════════════════

def build_entity_embedding_text(entity: dict) -> str:
    """
    엔티티 → 임베딩 입력 텍스트
    Why: type 프리픽스로 동명이인 구분, spec으로 검색 정밀도 향상
    """
    text = f"{entity.get('type', '')}: {entity.get('name', '')}"
    props = entity.get('properties', {}) or {}

    spec = props.get('spec', '')
    if spec:
        text += f" [규격: {spec}]"

    unit = props.get('unit', '')
    if unit:
        text += f" [{unit}]"

    return text.strip()


def build_chunk_embedding_text(chunk: dict) -> str:
    """
    청크 → 임베딩 입력 텍스트
    Why: 계층 메타데이터 프리픽스로 분류 맥락 제공
    """
    parts = []
    for key in ['department', 'chapter', 'section', 'title']:
        val = chunk.get(key)
        if val:
            parts.append(str(val))

    header = " > ".join(parts) if parts else ""
    body = chunk.get('text', '') or ''

    if header and body:
        return f"{header}\n{body}"
    elif header:
        return header
    else:
        return body


# ═══════════════════════════════════════════════════════
# 2. Gemini 임베딩 호출
# ═══════════════════════════════════════════════════════

def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    최대 100건 배치 임베딩. 768차원 벡터 반환.
    빈 텍스트는 '[EMPTY]'로 대체 (API 빈 문자열 거부 방어).
    """
    # Why: 빈 문자열 방어
    sanitized = [t if t.strip() else '[EMPTY]' for t in texts]

    result = gemini_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=sanitized,
        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM)
    )

    vectors = [emb.values for emb in result.embeddings]

    # 차원 검증
    for i, vec in enumerate(vectors):
        if len(vec) != EMBEDDING_DIM:
            raise ValueError(
                f"벡터 차원 불일치: index={i}, expected={EMBEDDING_DIM}, got={len(vec)}"
            )

    return vectors


def validate_embedding(vector: list[float], index: int) -> bool:
    """
    벡터 유효성 검증: NaN/Inf 방어
    Why: pgvector는 NaN/Inf 삽입 시 에러 발생
    """
    if len(vector) != EMBEDDING_DIM:
        log(f"  ⚠️ 차원 불일치: index={index}, got={len(vector)}")
        return False
    for j, v in enumerate(vector):
        if math.isnan(v) or math.isinf(v):
            log(f"  ⚠️ NaN/Inf 감지: index={index}, position={j}")
            return False
    return True


def embed_batch_with_retry(texts: list[str]) -> list[list[float]] | None:
    """
    재시도 포함 배치 임베딩 (Exponential Backoff)
    Why: 429/500 에러는 일시적. backoff로 안정적 처리.
    """
    for attempt in range(MAX_RETRIES):
        try:
            return embed_batch(texts)
        except Exception as e:
            error_str = str(e)
            is_retryable = any(
                code in error_str
                for code in ['429', '500', '503', 'RESOURCE_EXHAUSTED', 'UNAVAILABLE']
            )

            if not is_retryable or attempt == MAX_RETRIES - 1:
                log(f"  ❌ 비복구 에러 (attempt {attempt+1}/{MAX_RETRIES}): {error_str[:200]}")
                return None

            wait = min(INITIAL_BACKOFF * (2 ** attempt), MAX_BACKOFF)
            log(f"  ⏳ 재시도 {attempt+1}/{MAX_RETRIES} ({wait:.1f}초 대기): {error_str[:100]}")
            time.sleep(wait)

    return None


# ═══════════════════════════════════════════════════════
# 3. Supabase 데이터 조회 (Keyset 페이징 — Codex F3)
# ═══════════════════════════════════════════════════════

def fetch_entities_needing_embedding(batch_size: int = 1000) -> list[dict]:
    """
    embedding IS NULL인 엔티티를 keyset 페이징으로 가져옴.
    Why: offset 페이징은 처리 중 행이 NULL 집합에서 빠지면
         다음 페이지에서 건너뛸 위험 있음 → keyset으로 해결.
    """
    all_rows = []
    last_id = ''

    while True:
        response = (
            supabase.table('graph_entities')
            .select('id, name, type, properties')
            .is_('embedding', 'null')
            .gt('id', last_id)
            .order('id', desc=False)
            .limit(batch_size)
            .execute()
        )
        rows = response.data
        if not rows:
            break
        all_rows.extend(rows)
        last_id = rows[-1]['id']
        if len(rows) < batch_size:
            break

    return all_rows


def fetch_chunks_needing_embedding(batch_size: int = 1000) -> list[dict]:
    """fetch_entities_needing_embedding과 동일 패턴, 테이블만 다름"""
    all_rows = []
    last_id = ''

    while True:
        response = (
            supabase.table('graph_chunks')
            .select('id, department, chapter, section, title, text')
            .is_('embedding', 'null')
            .gt('id', last_id)
            .order('id', desc=False)
            .limit(batch_size)
            .execute()
        )
        rows = response.data
        if not rows:
            break
        all_rows.extend(rows)
        last_id = rows[-1]['id']
        if len(rows) < batch_size:
            break

    return all_rows


# ═══════════════════════════════════════════════════════
# 4. Supabase UPDATE (Codex F4: fallback 포함)
# ═══════════════════════════════════════════════════════

def update_embeddings(table: str, updates: list[dict], batch_size: int = 100):
    """
    임베딩 벡터 → Supabase UPDATE (RPC bulk_update_embeddings 사용)
    
    Why: 건별 update().eq()는 DB 왕복이 행 수만큼 발생하여 너무 느림.
         서버사이드 PL/pgSQL 함수로 100건씩 묶어 1회 RPC 호출 → 속도 100x 향상.
    
    벡터 캐스팅 전략 (Codex F4):
      RPC 함수 내부에서 TEXT→vector(768) 캐스팅. 
      Python 측에서 json.dumps()로 문자열 전달.
    """
    total = len(updates)
    success = 0
    errors = []

    for i in range(0, total, batch_size):
        batch = updates[i:i + batch_size]
        ids = [row['id'] for row in batch]
        embeddings = [json.dumps(row['embedding']) for row in batch]

        try:
            result = supabase.rpc('bulk_update_embeddings', {
                'p_table': table,
                'p_ids': ids,
                'p_embeddings': embeddings,
            }).execute()
            
            count = result.data if isinstance(result.data, int) else len(batch)
            success += count
        except Exception as e:
            # Fallback: 건별 update
            for row in batch:
                try:
                    supabase.table(table).update(
                        {'embedding': json.dumps(row['embedding'])}
                    ).eq('id', row['id']).execute()
                    success += 1
                except Exception as e2:
                    errors.append({'id': row['id'], 'error': str(e2)[:200]})
                    log(f"  ❌ [{table}] {row['id']} 실패: {str(e2)[:150]}")

        # 진행률 표시
        if (i + batch_size) % 500 < batch_size or (i + batch_size) >= total:
            log(f"  [{table}] {min(success, total)}/{total} 임베딩 업데이트 완료")

    return {'total': total, 'success': success, 'errors': errors}


# ═══════════════════════════════════════════════════════
# 5. 메인 처리 함수
# ═══════════════════════════════════════════════════════

def process_entities(embed_batch_size: int, dry_run: bool) -> dict:
    """Phase 1: 엔티티 임베딩 생성"""
    log("=" * 60)
    log("Phase 1: graph_entities 임베딩 생성")
    log("=" * 60)

    rows = fetch_entities_needing_embedding()
    total = len(rows)
    log(f"  대상: {total}건 (embedding IS NULL)")

    if total == 0:
        log("  → 처리할 건 없음 (이미 완료)")
        return {'total': 0, 'success': 0, 'errors': [], 'skipped': 0}

    # 텍스트 빌드
    texts = [build_entity_embedding_text(r) for r in rows]
    ids = [r['id'] for r in rows]

    if dry_run:
        log(f"\n  [DRY RUN] 임베딩 텍스트 샘플 (처음 10건):")
        for i in range(min(10, total)):
            log(f"    {ids[i]}: {texts[i][:100]}")
        return {'total': total, 'success': 0, 'errors': [], 'skipped': 0, 'dry_run': True}

    # 배치 임베딩 + UPDATE
    success = 0
    skipped = 0
    errors = []
    updates_buffer = []

    for batch_start in range(0, total, embed_batch_size):
        batch_end = min(batch_start + embed_batch_size, total)
        batch_texts = texts[batch_start:batch_end]
        batch_ids = ids[batch_start:batch_end]

        vectors = embed_batch_with_retry(batch_texts)

        if vectors is None:
            skipped += len(batch_texts)
            errors.append({'batch_start': batch_start, 'reason': 'API call failed'})
            continue

        # 유효성 검증 + 업데이트 버퍼 추가
        for j, vec in enumerate(vectors):
            if validate_embedding(vec, batch_start + j):
                updates_buffer.append({'id': batch_ids[j], 'embedding': vec})
            else:
                skipped += 1

        # 1000건마다 Supabase UPDATE
        if len(updates_buffer) >= 1000:
            result = update_embeddings('graph_entities', updates_buffer)
            success += result['success']
            errors.extend(result['errors'])
            updates_buffer = []
            log(f"  [graph_entities] 진행: {success + skipped}/{total} "
                f"(성공:{success}, 스킵:{skipped})")

    # 잔여 업데이트
    if updates_buffer:
        result = update_embeddings('graph_entities', updates_buffer)
        success += result['success']
        errors.extend(result['errors'])

    log(f"  Phase 1 완료: 성공={success}, 스킵={skipped}, 에러={len(errors)}")
    return {'total': total, 'success': success, 'errors': errors, 'skipped': skipped}


def process_chunks(embed_batch_size: int, dry_run: bool) -> dict:
    """Phase 2: 청크 임베딩 생성"""
    log("=" * 60)
    log("Phase 2: graph_chunks 임베딩 생성")
    log("=" * 60)

    rows = fetch_chunks_needing_embedding()
    total = len(rows)
    log(f"  대상: {total}건 (embedding IS NULL)")

    if total == 0:
        log("  → 처리할 건 없음 (이미 완료)")
        return {'total': 0, 'success': 0, 'errors': [], 'skipped': 0}

    texts = [build_chunk_embedding_text(r) for r in rows]
    ids = [r['id'] for r in rows]

    if dry_run:
        log(f"\n  [DRY RUN] 임베딩 텍스트 샘플 (처음 10건):")
        for i in range(min(10, total)):
            log(f"    {ids[i]}: {texts[i][:120]}")
        return {'total': total, 'success': 0, 'errors': [], 'skipped': 0, 'dry_run': True}

    success = 0
    skipped = 0
    errors = []
    updates_buffer = []

    for batch_start in range(0, total, embed_batch_size):
        batch_end = min(batch_start + embed_batch_size, total)
        batch_texts = texts[batch_start:batch_end]
        batch_ids = ids[batch_start:batch_end]

        vectors = embed_batch_with_retry(batch_texts)

        if vectors is None:
            skipped += len(batch_texts)
            errors.append({'batch_start': batch_start, 'reason': 'API call failed'})
            continue

        for j, vec in enumerate(vectors):
            if validate_embedding(vec, batch_start + j):
                updates_buffer.append({'id': batch_ids[j], 'embedding': vec})
            else:
                skipped += 1

        if len(updates_buffer) >= 1000:
            result = update_embeddings('graph_chunks', updates_buffer)
            success += result['success']
            errors.extend(result['errors'])
            updates_buffer = []
            log(f"  [graph_chunks] 진행: {success + skipped}/{total} "
                f"(성공:{success}, 스킵:{skipped})")

    if updates_buffer:
        result = update_embeddings('graph_chunks', updates_buffer)
        success += result['success']
        errors.extend(result['errors'])

    log(f"  Phase 2 완료: 성공={success}, 스킵={skipped}, 에러={len(errors)}")
    return {'total': total, 'success': success, 'errors': errors, 'skipped': skipped}


# ═══════════════════════════════════════════════════════
# 6. 메인 엔트리포인트
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Step 2.7: 임베딩 생성기")
    parser.add_argument('--target', choices=['entities', 'chunks', 'all'],
                        default='all', help='처리 대상 (기본: all)')
    parser.add_argument('--batch-size', type=int, default=100,
                        help='Gemini API 배치 크기 (기본: 100, 최대: 100)')
    parser.add_argument('--dry-run', action='store_true',
                        help='임베딩 텍스트 미리보기만 (API 호출 안 함)')
    args = parser.parse_args()

    # 배치 크기 제한
    embed_batch_size = min(args.batch_size, 100)

    log(f"Step 2.7 임베딩 생성기 시작")
    log(f"  모델: {EMBEDDING_MODEL}")
    log(f"  차원: {EMBEDDING_DIM}")
    log(f"  대상: {args.target}")
    log(f"  배치: {embed_batch_size}")
    log(f"  모드: {'DRY RUN' if args.dry_run else 'LIVE'}")
    log(f"  로그: {LOG_FILE}")
    log("")

    start_time = time.time()
    results = {}

    if args.target in ('entities', 'all'):
        results['entities'] = process_entities(embed_batch_size, args.dry_run)

    if args.target in ('chunks', 'all'):
        results['chunks'] = process_chunks(embed_batch_size, args.dry_run)

    elapsed = time.time() - start_time

    # ─── 요약 ─────────────────────────────────────────
    log("")
    log("=" * 60)
    log(f"최종 요약 (소요: {elapsed:.1f}초)")
    log("=" * 60)

    total_success = 0
    total_skipped = 0
    total_errors = 0

    for target, result in results.items():
        log(f"  {target}: total={result['total']}, "
            f"success={result['success']}, skipped={result.get('skipped', 0)}, "
            f"errors={len(result.get('errors', []))}")
        total_success += result['success']
        total_skipped += result.get('skipped', 0)
        total_errors += len(result.get('errors', []))

    log(f"\n  합계: 성공={total_success}, 스킵={total_skipped}, 에러={total_errors}")

    # ─── 결과 JSON 저장 ───────────────────────────────
    summary = {
        'timestamp': datetime.now().isoformat(),
        'model': EMBEDDING_MODEL,
        'dimension': EMBEDDING_DIM,
        'batch_size': embed_batch_size,
        'dry_run': args.dry_run,
        'elapsed_seconds': round(elapsed, 1),
        'results': {k: {**v, 'errors': [str(e) for e in v.get('errors', [])]}
                    for k, v in results.items()},
        'totals': {
            'success': total_success,
            'skipped': total_skipped,
            'errors': total_errors,
        }
    }

    summary_file = LOG_DIR / f"step7_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log(f"\n  요약 JSON: {summary_file}")

    if total_errors > 0:
        log(f"\n  ⚠️ {total_errors}건 에러 발생. 스크립트 재실행으로 자동 재처리됩니다.")
        sys.exit(1)


if __name__ == '__main__':
    main()
