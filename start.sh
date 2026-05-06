#!/bin/bash
echo "=== AVVIO APP ==="
python -c "import app; print('Import OK')" 2>&1
echo "=== AVVIO UVICORN ==="
uvicorn app:app --host 0.0.0.0 --port $PORT --log-level debug 2>&1