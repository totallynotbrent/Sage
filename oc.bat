@echo off
set "PROJECT=%CD%"

git config --global --add safe.directory "%PROJECT:\=/%" >nul 2>&1

opencode web --hostname 0.0.0.0 --port 4097