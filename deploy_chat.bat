@echo off
chcp 65001 >nul
echo ===================================================
echo  [1/2] edge-function 소스를 배포 폴더로 동기화 중...
echo ===================================================

robocopy "edge-function" "supabase\functions\rag-chat" /E /IS /IT /NFL /NDL /NJH /NJS

if %ERRORLEVEL% GEQ 8 (
    echo [에러] 파일 동기화에 실패했습니다. (Exit Code: %ERRORLEVEL%)
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ===================================================
echo  [2/2] Supabase Edge Function 배포 중...
echo ===================================================
call npx supabase functions deploy rag-chat --project-ref bfomacoarwtqzjfxszdr --no-verify-jwt

if %ERRORLEVEL% NEQ 0 (
    echo [에러] 배포에 실패했습니다.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo SSOT 동기화 및 배포가 완료되었습니다!
pause
