param([ValidateSet('run', 'test', 'lint', 'check', 'demo')][string]$Task = 'run')
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
switch ($Task) {
    'run'   { & $py -m streamlit run app.py --server.runOnSave true }
    'test'  { & $py -m pytest -q -x }
    'lint'  { & $py -m ruff check . --fix }
    'check' { & $py -m ruff check . --fix; if ($LASTEXITCODE -eq 0) { & $py -m pytest -q -x } }
    'demo'  { $env:DEMO_MODE = '1'; & $py -m streamlit run app.py }
}
exit $LASTEXITCODE
