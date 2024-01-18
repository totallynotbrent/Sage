@echo off
set "PROJECT=%CD%"

git config --global --add safe.directory "%PROJECT:\=/%" >nul 2>&1

set "OPENCODE_SERVER_PASSWORD=Brent8"

opencode web --hostname 0.0.0.0 --port 4097