"""
PDF 추출기 GUI 래퍼 v1.0
- step1_extract_gemini_v33.py의 GUI 프론트엔드
- tkinter 기반 파일 큐 관리 및 순차 자동 처리

사용법:
    python step1_gui.py
"""

import os
import sys
import json
import queue
import platform
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from enum import Enum


# --- 큐 항목 상태 ---
class QueueStatus(Enum):
    WAITING = "대기중"
    PROCESSING = "처리중"
    COMPLETED = "완료"
    ERROR = "오류"
    SKIPPED = "건너뜀"


@dataclass
class PDFQueueItem:
    """큐 항목 데이터 클래스"""
    filepath: str
    status: QueueStatus = QueueStatus.WAITING
    error_message: str = ""

    @property
    def filename(self) -> str:
        return Path(self.filepath).name


# --- stdout 리다이렉터 (버퍼링 방식) ---
# [Fix #7] 매 print마다 after(0) 대신 100ms 주기 버퍼 플러시로 GUI 부하 감소
class StdoutRedirector:
    """print 출력을 GUI Text 위젯으로 리다이렉트"""

    def __init__(self, text_widget: tk.Text, root: tk.Tk):
        self.text_widget = text_widget
        self.root = root
        self._original_stdout = sys.stdout
        self._buffer = queue.Queue()
        self._destroyed = False
        self._schedule_flush()

    def write(self, text: str):
        # 콘솔 stdout은 cp949 등 제한된 인코딩 → 이모지 출력 시 오류 방지
        try:
            self._original_stdout.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self._original_stdout, 'encoding', 'ascii') or 'ascii'
            safe_text = text.encode(encoding, errors='replace').decode(encoding)
            self._original_stdout.write(safe_text)
        if not self._destroyed:
            self._buffer.put(text)

    def _schedule_flush(self):
        if self._destroyed:
            return
        try:
            self._flush_buffer()
            self.root.after(100, self._schedule_flush)
        except tk.TclError:
            self._destroyed = True

    def _flush_buffer(self):
        texts = []
        while not self._buffer.empty():
            try:
                texts.append(self._buffer.get_nowait())
            except queue.Empty:
                break
        if texts:
            combined = ''.join(texts)
            self.text_widget.configure(state="normal")
            self.text_widget.insert(tk.END, combined)
            self.text_widget.see(tk.END)
            self.text_widget.configure(state="disabled")

    def flush(self):
        self._original_stdout.flush()

    def restore(self):
        self._destroyed = True
        sys.stdout = self._original_stdout


# --- 메인 GUI 클래스 ---
class PDFProcessorGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PDF 추출기 GUI v1.0")
        self.root.geometry("800x700")
        self.root.minsize(700, 600)

        # 상태 변수
        self.queue: list[PDFQueueItem] = []
        self.worker_thread: threading.Thread | None = None
        self.is_running = False
        self.pause_event = threading.Event()
        self.pause_event.set()  # 초기: 일시정지 아님
        self.stop_requested = False

        # stdout 리다이렉터 (나중에 설정)
        self.redirector: StdoutRedirector | None = None

        self._build_ui()
        self._setup_stdout_redirect()
        self._update_button_states()

        # 윈도우 닫기 이벤트
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ── UI 구성 ──────────────────────────────────────────

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self._build_settings_section(main_frame)
        self._build_queue_section(main_frame)
        self._build_control_section(main_frame)
        self._build_log_section(main_frame)
        self._build_bottom_section(main_frame)

    def _build_settings_section(self, parent):
        frame = ttk.LabelFrame(parent, text="공통 설정", padding=8)
        frame.pack(fill=tk.X, pady=(0, 5))

        # 모드 선택
        mode_frame = ttk.Frame(frame)
        mode_frame.pack(fill=tk.X, pady=(0, 5))

        self.mode_var = tk.StringVar(value="hybrid")
        ttk.Radiobutton(mode_frame, text="하이브리드 모드", variable=self.mode_var, value="hybrid").pack(side=tk.LEFT, padx=(0, 15))
        ttk.Radiobutton(mode_frame, text="텍스트 전용 모드", variable=self.mode_var, value="text_only").pack(side=tk.LEFT)

        # 페이지 범위
        page_frame = ttk.Frame(frame)
        page_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(page_frame, text="페이지 범위:").pack(side=tk.LEFT)
        self.page_entry = ttk.Entry(page_frame, width=25)
        self.page_entry.pack(side=tk.LEFT, padx=(5, 10))
        ttk.Label(page_frame, text="(예: 16-30, 1,3,5-10, 20-)", foreground="gray").pack(side=tk.LEFT)

        # 목차 파일
        toc_frame = ttk.Frame(frame)
        toc_frame.pack(fill=tk.X)

        ttk.Label(toc_frame, text="목차 파일: ").pack(side=tk.LEFT)
        self.toc_entry = ttk.Entry(toc_frame, width=45)
        self.toc_entry.pack(side=tk.LEFT, padx=(5, 5), fill=tk.X, expand=True)
        ttk.Button(toc_frame, text="찾아보기...", command=self._browse_toc).pack(side=tk.LEFT)

    def _build_queue_section(self, parent):
        frame = ttk.LabelFrame(parent, text="PDF 파일 큐", padding=8)
        frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        # 버튼 행 — [Fix #4] 인스턴스 변수로 보관 (처리 중 비활성화용)
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(0, 5))

        self.add_btn = ttk.Button(btn_frame, text="파일 추가", command=self._add_files)
        self.add_btn.pack(side=tk.LEFT, padx=(0, 5))
        self.remove_btn = ttk.Button(btn_frame, text="선택 삭제", command=self._remove_selected)
        self.remove_btn.pack(side=tk.LEFT, padx=(0, 5))
        self.clear_btn = ttk.Button(btn_frame, text="전체 삭제", command=self._clear_queue)
        self.clear_btn.pack(side=tk.LEFT)

        # Treeview (큐 목록)
        columns = ("no", "filename", "status")
        self.queue_tree = ttk.Treeview(frame, columns=columns, show="headings", height=6, selectmode="extended")
        self.queue_tree.heading("no", text="#")
        self.queue_tree.heading("filename", text="파일명")
        self.queue_tree.heading("status", text="상태")
        self.queue_tree.column("no", width=40, stretch=False, anchor="center")
        self.queue_tree.column("filename", width=500, stretch=True)
        self.queue_tree.column("status", width=80, stretch=False, anchor="center")

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=scrollbar.set)

        self.queue_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_control_section(self, parent):
        frame = ttk.LabelFrame(parent, text="제어", padding=8)
        frame.pack(fill=tk.X, pady=(0, 5))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(0, 5))

        self.start_btn = ttk.Button(btn_frame, text="처리 시작", command=self._start_processing)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.pause_btn = ttk.Button(btn_frame, text="일시정지", command=self._toggle_pause)
        self.pause_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.stop_btn = ttk.Button(btn_frame, text="중단", command=self._stop_processing)
        self.stop_btn.pack(side=tk.LEFT)

        self.status_label = ttk.Label(frame, text="대기 중")
        self.status_label.pack(fill=tk.X, pady=(0, 3))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X)

    def _build_log_section(self, parent):
        frame = ttk.LabelFrame(parent, text="실시간 로그", padding=8)
        frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        self.log_text = tk.Text(frame, height=10, state="disabled", wrap=tk.WORD, font=("Consolas", 9))
        log_scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scrollbar.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_bottom_section(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X)

        ttk.Button(frame, text="출력 폴더 열기", command=self._open_output_folder).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(frame, text="로그 저장", command=self._save_log).pack(side=tk.LEFT)

    # ── stdout 리다이렉트 ─────────────────────────────────

    def _setup_stdout_redirect(self):
        self.redirector = StdoutRedirector(self.log_text, self.root)
        sys.stdout = self.redirector

    # ── 파일 큐 관리 ─────────────────────────────────────

    def _add_files(self):
        files = filedialog.askopenfilenames(
            title="PDF 파일 선택",
            filetypes=[("PDF 파일", "*.pdf"), ("모든 파일", "*.*")]
        )
        if files:
            for f in files:
                # 중복 방지
                if not any(item.filepath == f for item in self.queue):
                    self.queue.append(PDFQueueItem(filepath=f))
            self._refresh_queue_tree()
            self._update_button_states()

    def _remove_selected(self):
        # [Fix #4] 처리 중에는 삭제 차단
        if self.is_running:
            messagebox.showwarning("경고", "처리 중에는 삭제할 수 없습니다.")
            return

        selected = self.queue_tree.selection()
        if not selected:
            return

        indices_to_remove = []
        for item_id in selected:
            values = self.queue_tree.item(item_id, "values")
            idx = int(values[0]) - 1
            indices_to_remove.append(idx)

        for idx in sorted(indices_to_remove, reverse=True):
            del self.queue[idx]

        self._refresh_queue_tree()
        self._update_button_states()

    def _clear_queue(self):
        if self.is_running:
            messagebox.showwarning("경고", "처리 중에는 전체 삭제할 수 없습니다.")
            return
        self.queue.clear()
        self._refresh_queue_tree()
        self._update_button_states()

    def _refresh_queue_tree(self):
        self.queue_tree.delete(*self.queue_tree.get_children())
        for i, item in enumerate(self.queue):
            tag = ""
            if item.status == QueueStatus.COMPLETED:
                tag = "completed"
            elif item.status == QueueStatus.PROCESSING:
                tag = "processing"
            elif item.status == QueueStatus.ERROR:
                tag = "error"

            self.queue_tree.insert("", tk.END, values=(i + 1, item.filename, item.status.value), tags=(tag,))

        self.queue_tree.tag_configure("completed", foreground="green")
        self.queue_tree.tag_configure("processing", foreground="blue")
        self.queue_tree.tag_configure("error", foreground="red")

    # ── 목차 파일 찾기 ────────────────────────────────────

    def _browse_toc(self):
        path = filedialog.askopenfilename(
            title="목차 파일 선택",
            filetypes=[("마크다운/텍스트", "*.md *.txt"), ("JSON", "*.json"), ("모든 파일", "*.*")]
        )
        if path:
            self.toc_entry.delete(0, tk.END)
            self.toc_entry.insert(0, path)

    # ── 제어 기능 ─────────────────────────────────────────

    def _update_button_states(self):
        has_waiting = any(item.status == QueueStatus.WAITING for item in self.queue)

        if self.is_running:
            self.start_btn.configure(state="disabled")
            self.pause_btn.configure(state="normal")
            self.stop_btn.configure(state="normal")
            # [Fix #4] 처리 중 큐 변경 버튼 비활성화
            self.remove_btn.configure(state="disabled")
            self.clear_btn.configure(state="disabled")
        else:
            self.start_btn.configure(state="normal" if has_waiting else "disabled")
            self.pause_btn.configure(state="disabled")
            self.stop_btn.configure(state="disabled")
            self.remove_btn.configure(state="normal")
            self.clear_btn.configure(state="normal")

    def _start_processing(self):
        waiting = [item for item in self.queue if item.status == QueueStatus.WAITING]
        if not waiting:
            messagebox.showinfo("알림", "처리할 파일이 없습니다.")
            return

        self.is_running = True
        self.stop_requested = False
        self.pause_event.set()
        self._update_button_states()

        # [Fix #3] GUI 스레드에서 위젯 값 미리 읽기 → config dict로 워커에 전달
        worker_config = {
            'text_only': self.mode_var.get() == "text_only",
            'page_spec': self.page_entry.get().strip(),
            'toc_path': self.toc_entry.get().strip(),
        }

        self.worker_thread = threading.Thread(
            target=self._worker_loop, args=(worker_config,), daemon=True
        )
        self.worker_thread.start()

    def _toggle_pause(self):
        if self.pause_event.is_set():
            # 일시정지
            self.pause_event.clear()
            self.pause_btn.configure(text="재개")
            self._update_status("일시정지됨")
            print("\n⏸️ 일시정지됨. [재개] 버튼을 누르면 계속합니다.\n")
        else:
            # 재개
            self.pause_event.set()
            self.pause_btn.configure(text="일시정지")
            print("\n▶️ 재개됨.\n")

    def _stop_processing(self):
        if not self.is_running:
            return
        self.stop_requested = True
        self.pause_event.set()  # 일시정지 상태면 해제
        self._update_status("중단 요청됨 (현재 파일 처리 완료 후 중단)")
        print("\n🛑 중단 요청됨. 현재 파일 처리 완료 후 중단됩니다.\n")

    # ── 워커 스레드 ───────────────────────────────────────

    def _worker_loop(self, config: dict):
        """큐의 대기중 항목을 순차 처리하는 워커 스레드"""
        # [Fix #1] except Exception — ValueError(API 키 미설정) 등 모듈 초기화 오류도 포착
        try:
            from step1_extract_gemini_v33 import (
                process_pdf, process_pdf_text_only, parse_page_spec, tracker
            )
            from toc_parser import parse_toc_file
        except Exception as e:
            print(f"\n❌ 모듈 로드 실패: {e}")
            print("   - step1_extract_gemini_v33.py / toc_parser.py가 같은 폴더에 있는지 확인")
            print("   - .env 파일에 GEMINI_API_KEY가 설정되어 있는지 확인")
            self.root.after(0, self._on_worker_done)
            return

        # [Fix #3] config에서 값 읽기 (GUI 스레드에서 미리 읽은 값)
        text_only = config['text_only']
        page_spec = config['page_spec']
        toc_path = config['toc_path']

        # 목차 파일 로드
        section_map = None
        if toc_path:
            if not os.path.exists(toc_path):
                print(f"❌ 목차 파일을 찾을 수 없습니다: {toc_path}")
            else:
                try:
                    if toc_path.endswith('.json'):
                        print(f"📖 목차 JSON 파일 로드 중: {toc_path}")
                        with open(toc_path, 'r', encoding='utf-8') as f:
                            toc_data = json.load(f)
                        section_map = toc_data.get('section_map', {})
                        print(f"    ✅ JSON에서 {len(section_map)}개 섹션 정보 로드 완료")
                    else:
                        print(f"📖 목차 파일 파싱 중: {toc_path}")
                        section_map = parse_toc_file(toc_path)
                        print(f"    ✅ {len(section_map)}개 페이지에 대한 목차 정보 파싱 완료")
                except Exception as e:
                    print(f"❌ 목차 파일 로드 실패: {e}")

        # [Fix #4] 큐 스냅샷 — 워커 시작 시점의 대기 항목만 처리
        items_to_process = [item for item in self.queue if item.status == QueueStatus.WAITING]
        total_waiting = len(items_to_process)
        processed_count = 0

        for item in items_to_process:
            if self.stop_requested:
                item.status = QueueStatus.SKIPPED
                continue

            # 일시정지 대기
            self.pause_event.wait()
            if self.stop_requested:
                item.status = QueueStatus.SKIPPED
                continue

            # 처리 시작
            item.status = QueueStatus.PROCESSING
            processed_count += 1
            self.root.after(0, self._refresh_queue_tree)
            self.root.after(0, self._update_status,
                            f"현재: {item.filename} ({processed_count}/{total_waiting})")
            self.root.after(0, self._update_progress, processed_count - 1, total_waiting)

            print(f"\n{'='*50}")
            print(f"📂 파일 {processed_count}/{total_waiting}: {item.filename}")
            print(f"{'='*50}\n")

            try:
                self._process_single_pdf(
                    item, text_only, section_map, page_spec,
                    process_pdf, process_pdf_text_only, parse_page_spec, tracker
                )
                item.status = QueueStatus.COMPLETED
                print(f"\n✅ {item.filename} 처리 완료!\n")
            except Exception as e:
                item.status = QueueStatus.ERROR
                item.error_message = str(e)
                print(f"\n❌ {item.filename} 처리 실패: {e}\n")

            self.root.after(0, self._refresh_queue_tree)
            self.root.after(0, self._update_progress, processed_count, total_waiting)

        # [Fix #9] 중복 SKIPPED 루프 제거됨 — 스냅샷 루프에서 이미 처리

        self.root.after(0, self._on_worker_done)

    def _process_single_pdf(self, item, text_only, section_map, page_spec,
                            process_pdf_fn, process_pdf_text_only_fn, parse_page_spec_fn, tracker):
        """단일 PDF 파일 처리 (워커 스레드에서 호출)"""
        pdf_path = item.filepath

        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {pdf_path}")

        # [Fix #10] page_spec이 있을 때만 pdfplumber로 총 페이지 수 확인 (불필요한 이중 open 방지)
        page_indices = None
        if page_spec:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                total_pages = len(pdf.pages)
            page_indices = parse_page_spec_fn(page_spec, total_pages)
            if not page_indices:
                raise ValueError(f"유효한 페이지가 없습니다: {page_spec} (총 {total_pages}페이지)")
            print(f"📋 페이지 지정: {page_spec} → {len(page_indices)}페이지 처리 예정")

        # [Fix #2] tracker 스냅샷 — 파일별 사용량 계산용
        prev_calls = tracker.call_count
        prev_input = tracker.total_input_tokens
        prev_output = tracker.total_output_tokens

        # PDF 처리
        if text_only:
            print(f"🚀 텍스트 전용 모드 시작")
            md = process_pdf_text_only_fn(pdf_path, section_map=section_map, page_indices=page_indices)
        else:
            print(f"🚀 하이브리드 모드 시작")
            md = process_pdf_fn(pdf_path, section_map=section_map, page_indices=page_indices)

        if md:
            # [Fix #2] 파일별 사용량 델타 계산
            file_usage = {
                'calls': tracker.call_count - prev_calls,
                'input_tokens': tracker.total_input_tokens - prev_input,
                'output_tokens': tracker.total_output_tokens - prev_output,
            }
            self._save_output(pdf_path, md, page_indices, file_usage)
        else:
            print("⚠️ 추출 결과가 없습니다.")

    def _save_output(self, pdf_path, md, page_indices, file_usage: dict):
        """결과 파일 저장"""
        pdf_stem = Path(pdf_path).stem
        date_str = datetime.now().strftime("%Y%m%d")

        if page_indices:
            page_range_str = f"_p{min(page_indices)+1}-{max(page_indices)+1}"
        else:
            page_range_str = ""

        script_dir = Path(__file__).parent
        output_dir = script_dir / "download_file"
        output_dir.mkdir(parents=True, exist_ok=True)

        base_name = f"{date_str}_{pdf_stem}{page_range_str}"
        output_path = output_dir / f"{base_name}.md"

        counter = 1
        while output_path.exists():
            output_path = output_dir / f"{base_name}_{counter}.md"
            counter += 1

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md)

        print()
        print("=" * 50)
        print("✅ 추출 완료!")
        print("=" * 50)
        print(f"📄 출력 파일: {output_path}")
        print(f"📊 파일 크기: {len(md):,} bytes")
        print()

        # [Fix #2] 파일별 Gemini 사용량 출력 (누적값이 아닌 델타)
        if file_usage['calls'] > 0:
            total_tokens = file_usage['input_tokens'] + file_usage['output_tokens']
            est_cost = (
                (file_usage['input_tokens'] / 1_000_000 * 0.50)
                + (file_usage['output_tokens'] / 1_000_000 * 1.50)
            )
            print(
                f"📈 Gemini 사용량 (이 파일):\n"
                f"   - API 호출: {file_usage['calls']}회\n"
                f"   - 입력 토큰: {file_usage['input_tokens']:,}\n"
                f"   - 출력 토큰: {file_usage['output_tokens']:,}\n"
                f"   - 총 토큰: {total_tokens:,}\n"
                f"   - 예상 비용 (유료 시): ${est_cost:.4f} (약 {int(est_cost * 1400)}원)"
            )

    # ── GUI 업데이트 (메인 스레드) ─────────────────────────

    def _update_status(self, text: str):
        self.status_label.configure(text=text)

    def _update_progress(self, current: int, total: int):
        if total > 0:
            pct = (current / total) * 100
            self.progress_var.set(pct)
        else:
            self.progress_var.set(0)

    def _on_worker_done(self):
        self.is_running = False
        self.pause_btn.configure(text="일시정지")
        self._update_button_states()
        self._refresh_queue_tree()

        completed = sum(1 for item in self.queue if item.status == QueueStatus.COMPLETED)
        errors = sum(1 for item in self.queue if item.status == QueueStatus.ERROR)
        skipped = sum(1 for item in self.queue if item.status == QueueStatus.SKIPPED)

        if self.stop_requested:
            self._update_status(f"중단됨 — 완료: {completed}, 오류: {errors}, 건너뜀: {skipped}")
            print(f"\n🛑 처리 중단. 완료: {completed}, 오류: {errors}, 건너뜀: {skipped}")
        else:
            self._update_status(f"모두 완료 — 완료: {completed}, 오류: {errors}")
            self.progress_var.set(100)
            print(f"\n🎉 모든 파일 처리 완료! 완료: {completed}, 오류: {errors}")

    # ── 하단 기능 ─────────────────────────────────────────

    def _open_output_folder(self):
        output_dir = Path(__file__).parent / "download_file"
        output_dir.mkdir(parents=True, exist_ok=True)
        # [Fix #8] 크로스 플랫폼 폴더 열기
        system = platform.system()
        if system == "Windows":
            os.startfile(str(output_dir))
        elif system == "Darwin":
            subprocess.Popen(["open", str(output_dir)])
        else:
            subprocess.Popen(["xdg-open", str(output_dir)])

    def _save_log(self):
        log_content = self.log_text.get("1.0", tk.END).strip()
        if not log_content:
            messagebox.showinfo("알림", "저장할 로그가 없습니다.")
            return

        path = filedialog.asksaveasfilename(
            title="로그 저장",
            defaultextension=".txt",
            filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")],
            initialfile=f"pdf_gui_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(log_content)
            print(f"📝 로그 저장 완료: {path}")

    # ── 윈도우 닫기 ──────────────────────────────────────

    def _on_closing(self):
        if self.is_running:
            result = messagebox.askyesnocancel(
                "처리 중",
                "PDF 처리가 진행 중입니다.\n\n"
                "[예] 현재 파일 완료 후 종료\n"
                "[아니요] 즉시 종료\n"
                "[취소] 돌아가기"
            )
            if result is None:
                return  # 취소
            elif result:
                # 예: 현재 파일 완료 후 종료
                self.stop_requested = True
                self.pause_event.set()
                # [Fix #6] root.after 폴링으로 종료 대기 (30초 타임아웃, GUI 응답 유지)
                self._wait_and_close_tick(0)
                return
            # 아니요: 즉시 종료 → 아래로 진행

        self._cleanup_and_destroy()

    def _wait_and_close_tick(self, elapsed: int):
        """[Fix #6] 주기적 폴링으로 워커 완료 대기 (GUI 응답 유지, 30초 타임아웃)"""
        if self.worker_thread and self.worker_thread.is_alive():
            if elapsed >= 30:
                print("\n⚠️ 대기 시간 초과(30초). 강제 종료합니다.")
                self._cleanup_and_destroy()
                return
            self._update_status(f"종료 대기 중... ({elapsed}초)")
            self.root.after(1000, self._wait_and_close_tick, elapsed + 1)
        else:
            self._cleanup_and_destroy()

    def _cleanup_and_destroy(self):
        if self.redirector:
            self.redirector.restore()
        self.root.destroy()


def main():
    # 스크립트 디렉토리를 작업 디렉토리로 설정 (모듈 임포트용)
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    # [Fix #5] try/finally로 stdout 복원 보장 (비정상 종료 시에도)
    original_stdout = sys.stdout
    root = tk.Tk()
    try:
        app = PDFProcessorGUI(root)
        root.mainloop()
    finally:
        sys.stdout = original_stdout


if __name__ == "__main__":
    main()
