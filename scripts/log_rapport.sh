#!/bin/bash
# Diagnostikkrapport for Kåre LLM-problemer
# Kjør: bash /kaare/scripts/log_rapport.sh

echo "======================================================"
echo " KÅRE DIAGNOSTIKK  $(date '+%Y-%m-%d %H:%M:%S')"
echo "======================================================"

echo ""
echo "--- FALLBACK-FLAGG ---"
if [ -f /kaare/runtime/kare_9b_fallback.json ]; then
    echo "AKTIV: $(cat /kaare/runtime/kare_9b_fallback.json)"
else
    echo "Ingen fallback aktiv"
fi

echo ""
echo "--- LLM-KONFIG (default) ---"
python3 -c "
import yaml
cfg = yaml.safe_load(open('/kaare/configs/llm.yaml'))['default']
print(f\"  think:       {cfg.get('think', 'ikke satt')}\")
print(f\"  stream:      {cfg.get('stream', 'ikke satt')}\")
print(f\"  num_predict: {cfg.get('options', {}).get('num_predict', 'ikke satt')}\")
print(f\"  num_ctx:     {cfg.get('options', {}).get('num_ctx', 'ikke satt')}\")
print(f\"  temperature: {cfg.get('options', {}).get('temperature', 'ikke satt')}\")
print(f\"  base_url:    {cfg.get('base_url', '')}\")
"

echo ""
echo "--- SISTE 10 LLM-KALL ---"
if [ -f /kaare/logs/llm_calls.log ]; then
    tail -10 /kaare/logs/llm_calls.log | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        e = json.loads(line)
        think = 'THINK' if e.get('has_think') else 'no-think'
        tools = 'tools' if e.get('has_tools') else '      '
        rec   = ' RECOVERED' if e.get('recovered') else ''
        print(f\"  {e['ts'][11:19]}  {e['instance']:<12}  {e['latency_ms']:>6}ms  {think}  {tools}{rec}\")
    except: pass
"
else
    echo "  Ingen llm_calls.log funnet"
fi

echo ""
echo "--- SISTE 20 KAARE-LOGGLINJER ---"
journalctl -u kaare.service -n 20 --no-pager 2>/dev/null | grep -v "^--" || echo "  Ingen systemd-logg"

echo ""
echo "--- SISTE 5 ROUTER-KALL (stdout fra kaare) ---"
journalctl -u kaare.service -n 100 --no-pager 2>/dev/null \
    | grep -E "\[ROUTER\]|\[LTM\]|\[RAG\]" | tail -20 || echo "  Ingen ROUTER-logg"

echo ""
echo "--- GPU-LÅS STATUS ---"
if [ -f /kaare/runtime/gpu.lock ]; then
    flock -n /kaare/runtime/gpu.lock echo "  GPU ledig" 2>/dev/null || echo "  GPU LÅS HOLDES (kare er opptatt)"
else
    echo "  gpu.lock finnes ikke — kare_is_busy() returnerer alltid True!"
fi

echo ""
echo "--- OLLAMA-STATUS (port 11441) ---"
curl -s --max-time 3 http://127.0.0.1:11441/api/tags 2>/dev/null \
    | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    models = [m['name'] for m in d.get('models', [])]
    print('  Tilgjengelige modeller:', ', '.join(models) if models else 'ingen')
except:
    print('  Ikke svar fra port 11441')
"

echo ""
echo "======================================================"
