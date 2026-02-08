@echo off
REM Simple helper to copy wiki files into a cloned GitHub wiki repository.
REM Usage: push_wiki.bat <path-to-cloned-wiki-repo>

if "%1"=="" (
  echo Usage: push_wiki.bat ^<path-to-cloned-wiki-repo^>
  exit /b 1
)

set WIKI_CLONE=%~1
xcopy /E /Y "%~dp0wiki\*" "%WIKI_CLONE%\"
cd /d "%WIKI_CLONE%"

git add .
git commit -m "Update wiki pages"

echo Now run: git push origin main
