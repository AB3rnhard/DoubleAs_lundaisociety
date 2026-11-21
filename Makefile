run:   ; streamlit run app.py --server.runOnSave true
test:  ; pytest -q -x
lint:  ; ruff check . --fix
check: lint test
demo:  ; DEMO_MODE=1 streamlit run app.py
