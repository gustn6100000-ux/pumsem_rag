"""Phase 1 전처리 파이프라인 실행 스크립트

사용법:
    python run_pipeline.py              # 전체 파일 실행
    python run_pipeline.py --pilot      # 파일럿 2개 파일만
    python run_pipeline.py --step 1     # 특정 Step만 실행
"""
import sys
import os
import time
import argparse
from pathlib import Path

# Windows 콘솔 UTF-8 출력
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 모듈 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import OUTPUT_DIR
from step1_section_splitter import run_step1
from step2_table_parser import run_step2
from step3_text_cleaner import run_step3
from step4_chunker import run_step4
from step5_validator import run_step5


def run_pipeline(pilot_only: bool = False, step: int = None):
    """전체 파이프라인 실행"""
    start_time = time.time()

    print("╔" + "═" * 58 + "╗")
    print("║  Phase 1: 전처리 파이프라인                              ║")
    print("║  건설공사 표준품셈 GraphRAG                              ║")
    mode = "파일럿 모드 (2개 파일)" if pilot_only else "전체 모드 (41개 파일)"
    print(f"║  모드: {mode:<49}║")
    print("╚" + "═" * 58 + "╝")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    steps = {
        1: ("Step 1: 섹션 분할", lambda: run_step1(pilot_only=pilot_only)),
        2: ("Step 2: 테이블 파싱", run_step2),
        3: ("Step 3: 텍스트 정제", run_step3),
        4: ("Step 4: 청크 생성", run_step4),
        5: ("Step 5: 품질 검증", run_step5),
    }

    if step:
        # 특정 Step만 실행
        if step not in steps:
            print(f"오류: Step {step}은 존재하지 않습니다. (1-5)")
            return
        name, func = steps[step]
        print(f"\n▶ {name}만 실행")
        func()
    else:
        # 전체 파이프라인 실행
        for step_num, (name, func) in steps.items():
            step_start = time.time()
            func()
            elapsed = time.time() - step_start
            print(f"  ⏱ {name} 완료: {elapsed:.1f}초")

    total_time = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"파이프라인 완료! 총 소요시간: {total_time:.1f}초")
    print(f"출력 디렉토리: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 1 전처리 파이프라인")
    parser.add_argument("--pilot", action="store_true", help="파일럿 모드 (2개 파일만)")
    parser.add_argument("--step", type=int, help="특정 Step만 실행 (1-5)")
    args = parser.parse_args()

    run_pipeline(pilot_only=args.pilot, step=args.step)
