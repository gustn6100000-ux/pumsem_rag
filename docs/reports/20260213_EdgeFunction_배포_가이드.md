# Supabase Edge Function 배포 가이드

> **문서 버전**: v1.0  
> **작성일**: 2026-02-13  
> **대상 함수**: `rag-chat`  
> **프로젝트 ID**: `bfomacoarwtqzjfxszdr`

---

## 1. 배포 환경 정보

| 항목                     | 값                                                                            |
| ------------------------ | ----------------------------------------------------------------------------- |
| **프로젝트**             | Supabase `bfomacoarwtqzjfxszdr`                                               |
| **함수명**               | `rag-chat`                                                                    |
| **소스 원본 경로**       | `G:\내 드라이브\Antigravity\python_code\supabase\functions\rag-chat\index.ts` |
| **배포용 스테이징 경로** | `C:\Users\lhs\sb_deploy\supabase\functions\rag-chat\index.ts`                 |
| **JWT 검증**             | 비활성화 (`--no-verify-jwt`)                                                  |
| **현재 버전**            | v44 → v45 (Phase 4A 반영)                                                     |

---

## 2. 한글 경로 문제 및 해결

### 2.1 문제

Google Drive File Stream의 마운트 경로에 한글(`내 드라이브`)이 포함되어 있어, **Supabase CLI(`npx supabase`)가 entrypoint 파일을 인식하지 못함**.

```
Error: entrypoint path does not exist (supabase/functions/rag-chat/index.ts)
```

### 2.2 원인

- Supabase CLI 내부 bundler(esbuild)가 한글 포함 경로를 파싱할 때 실패
- Google Drive File Stream은 8.3 short filename(짧은 경로)을 지원하지 않음
- `subst` 드라이브 매핑은 Google Drive 가상 파일시스템에서 동작하지 않음
- MCP `deploy_edge_function`은 94KB 파일 직접 전달에 부적합

### 2.3 해결: 스테이징 경로 전략

**영문 전용 로컬 경로**에 파일을 복사한 후 배포:

```
소스 원본 (한글 경로) → 스테이징 (영문 경로) → Supabase 배포
G:\내 드라이브\...       C:\Users\lhs\sb_deploy\    → supabase deploy
```

---

## 3. 배포 절차 (Step-by-Step)

### Step 1: 소스 파일을 스테이징 경로에 복사

```powershell
# 디렉토리 생성 (최초 1회)
New-Item -ItemType Directory -Path "C:\Users\lhs\sb_deploy\supabase\functions\rag-chat" -Force

# 파일 복사
Copy-Item `
  "G:\내 드라이브\Antigravity\python_code\supabase\functions\rag-chat\index.ts" `
  "C:\Users\lhs\sb_deploy\supabase\functions\rag-chat\index.ts" `
  -Force
```

### Step 2: 파일 복사 검증

```powershell
# 파일 크기 확인 (원본과 동일해야 함)
(Get-Item "C:\Users\lhs\sb_deploy\supabase\functions\rag-chat\index.ts").Length
```

### Step 3: 배포 실행

```powershell
# CWD를 스테이징 경로의 상위로 변경 후 실행
Set-Location "C:\Users\lhs\sb_deploy"
npx supabase functions deploy rag-chat --project-ref bfomacoarwtqzjfxszdr --no-verify-jwt
```

또는 **원래 CWD를 유지한 채** 실행:

```powershell
# 워크스페이스 CWD에서 Set-Location으로 전환
Set-Location "C:\Users\lhs\sb_deploy"; `
npx supabase functions deploy rag-chat --project-ref bfomacoarwtqzjfxszdr --no-verify-jwt
```

### Step 4: 배포 확인

성공 시 출력:

```
Bundling Function: rag-chat
Deploying Function: rag-chat (script size: ~94kB)
Deployed Functions on project bfomacoarwtqzjfxszdr
```

Dashboard에서 확인: [https://supabase.com/dashboard/project/bfomacoarwtqzjfxszdr/functions](https://supabase.com/dashboard/project/bfomacoarwtqzjfxszdr/functions)

---

## 4. 통합 배포 명령어 (One-liner)

```powershell
# 복사 + 배포를 한 줄로 실행
Copy-Item "G:\내 드라이브\Antigravity\python_code\supabase\functions\rag-chat\index.ts" `
  "C:\Users\lhs\sb_deploy\supabase\functions\rag-chat\index.ts" -Force; `
Set-Location "C:\Users\lhs\sb_deploy"; `
npx supabase functions deploy rag-chat --project-ref bfomacoarwtqzjfxszdr --no-verify-jwt
```

---

## 5. 다른 Edge Function 배포 시

같은 패턴 적용:

```powershell
# 예: regenerate-embeddings 함수 배포
New-Item -ItemType Directory -Path "C:\Users\lhs\sb_deploy\supabase\functions\regenerate-embeddings" -Force

Copy-Item "G:\내 드라이브\...\regenerate-embeddings\index.ts" `
  "C:\Users\lhs\sb_deploy\supabase\functions\regenerate-embeddings\index.ts" -Force

Set-Location "C:\Users\lhs\sb_deploy"; `
npx supabase functions deploy regenerate-embeddings --project-ref bfomacoarwtqzjfxszdr
```

---

## 6. 배포 이력

| 날짜       | 버전 | 변경 내용                                     | 크기    |
| ---------- | ---- | --------------------------------------------- | ------- |
| 2026-02-12 | v43  | Phase 3-C chunk 본문 검색 추가                | ~90KB   |
| 2026-02-12 | v44  | 키워드 폴백 검색 개선                         | ~92KB   |
| 2026-02-13 | v45  | Phase 4A: 중복 제거, 메시지 개선, _debug 제거 | 94.15KB |

---

## 7. 트러블슈팅

### 문제: `entrypoint path does not exist`
- **원인**: CWD에서 `supabase/functions/rag-chat/index.ts` 상대경로를 찾지 못함
- **해결**: 스테이징 경로(`C:\Users\lhs\sb_deploy`)에서 실행

### 문제: `npx supabase` 미설치
- **해결**: `npx -y supabase@latest functions deploy ...` (자동 설치)

### 문제: 배포 후 함수 동작 안 함
- **확인**: Edge Function 로그 조회
  ```powershell
  npx supabase functions logs rag-chat --project-ref bfomacoarwtqzjfxszdr
  ```
- 또는 Supabase MCP:
  ```
  mcp_supabase-mcp-server_get_logs(project_id, service="edge-function")
  ```

### 문제: CORS 에러
- `index.ts`의 `ALLOWED_ORIGINS` 배열에 프론트엔드 도메인 추가 필요

---

## 8. 주의사항

1. **원본은 항상 Google Drive에 보관**: `G:\내 드라이브\Antigravity\python_code\supabase\functions\rag-chat\index.ts`가 소스 오브 트루스
2. **스테이징 경로는 임시**: `C:\Users\lhs\sb_deploy\`는 배포 전용. 이 경로에서 직접 편집하지 않기
3. **`--no-verify-jwt` 유의**: 현재 함수는 자체 API Key(`x-api-key`) 검증 로직을 사용하므로 JWT 비활성화. 변경 시 프론트엔드 인증 로직도 수정 필요
4. **환경 변수**: `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `RAG_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`가 Supabase Dashboard → Edge Functions → Secrets에 설정되어 있어야 함
