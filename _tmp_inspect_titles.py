import json
from pathlib import Path

base = Path('/Users/mohit.sharma/Documents/GitHub/Agentic-Cloud-Architect/Projects/Azure-Project1/Documentation')

sf_raw = json.loads((base / 'structured-findings.json').read_text())
findings = sf_raw.get('findings', [])
if findings:
    f = findings[0]
    print('SF-JSON keys:', list(f.keys()))
    print('SF-JSON title:', repr(str(f.get('title',''))[:150]))
    print('SF-JSON message:', repr(str(f.get('message',''))[:200]))
    print()

fir = json.loads((base / 'final_intelligent_report.json').read_text())
print('FIR keys:', list(fir.keys()))
print()

pi = fir.get('priority_improvements', [])
if pi:
    print('PI[0] keys:', list(pi[0].keys()))
    print('PI[0] title:', repr(str(pi[0].get('title',''))[:200]))
    print('PI[0] detail:', repr(str(pi[0].get('detail',''))[:200]))
    print()

qf = fir.get('quick_fixes', [])
if qf:
    print('QF[0] keys:', list(qf[0].keys()))
    print('QF[0] title:', repr(str(qf[0].get('title',''))[:200]))
    print()

issues = fir.get('issues', [])
if issues:
    print('Issues[0] keys:', list(issues[0].keys()))
    print('Issues[0] title:', repr(str(issues[0].get('title',''))[:200]))
    print('Issues[0] impact:', repr(str(issues[0].get('impact',''))[:200]))
    print()

sc = fir.get('scenario_findings', [])
if sc:
    print('ScenarioFindings[0] keys:', list(sc[0].keys()))
    print('ScenarioFindings[0] title:', repr(str(sc[0].get('title',''))[:200]))
    gaps = sc[0].get('gaps', {})
    print('ScenarioFindings[0] gaps keys:', list(gaps.keys()) if isinstance(gaps, dict) else type(gaps))
    print()

# Also show pillar weaknesses titles
pillars = fir.get('pillars', {})
for pname, pdata in list(pillars.items())[:1]:
    weaknesses = pdata.get('weaknesses', []) if isinstance(pdata, dict) else []
    if weaknesses:
        print(f'Pillar {pname} weakness[0]:', repr(str(weaknesses[0])[:200]))
        print()
    recs = pdata.get('recommendations', []) if isinstance(pdata, dict) else []
    if recs:
        print(f'Pillar {pname} rec[0]:', repr(str(recs[0])[:200]))
