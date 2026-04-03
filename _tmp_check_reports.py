from settings_server import collect_project_entries, run_final_report_stage
from pathlib import Path

pid = collect_project_entries()[0]['id']
fr = run_final_report_stage(pid)
print('fr_ok', fr.get('ok'))
print('artifact', fr.get('artifactPath'))
print('full_artifact', fr.get('fullArtifactPath'))

base = Path('/workspace/Projects/Azure-Project1/Documentation')
display = base / 'final-report.md'
full = base / 'final-report-full.md'

print()
print('--- Display (Tips) report ---')
if display.exists():
    d_text = display.read_text(encoding='utf-8')
    print('lines:', len(d_text.splitlines()))
    print('### headings:', d_text.count('\n### '))
    issues_section = d_text[d_text.find('## Issues'):d_text.find('## Recommended')]
    print('issues entries (### count in section):', issues_section.count('\n### '))
else:
    print('NOT FOUND')

print()
print('--- Full (Download) report ---')
if full.exists():
    f_text = full.read_text(encoding='utf-8')
    print('lines:', len(f_text.splitlines()))
    print('### headings:', f_text.count('\n### '))
    issues_section_f = f_text[f_text.find('## Issues'):f_text.find('## Recommended')]
    print('issues entries (### count in section):', issues_section_f.count('\n### '))
else:
    print('NOT FOUND')
