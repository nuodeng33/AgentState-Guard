"""Disposable first-use UI acceptance; no API interception or model execution."""
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from verify_local_workflow import ROOT, CoreServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--program', required=True, type=Path, help='Existing real Codex executable to register; credentials are never copied')
    parser.add_argument('--report-dir', required=True, type=Path)
    args = parser.parse_args()
    program = args.program.resolve(strict=True)
    if not program.is_file():
        parser.error('--program must be a file')
    directory = args.report_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    summary = {'result': 'RUNNING', 'scope': 'actual Core/browser enrollment and policy persistence; no model execution'}
    try:
        with tempfile.TemporaryDirectory(prefix='asg-project-onboarding-') as temporary:
            root = Path(temporary)
            for name in ('state', 'project', 'store'):
                (root / name).mkdir()
            (root / 'project' / 'hello.txt').write_text('original onboarding file\n', encoding='utf-8')
            fixture_path = root / 'fixture.json'
            fixture = {'program': str(program), 'project': str(root / 'project'), 'store': str(root / 'store')}
            for phase in ('exercise', 'reopen'):
                with CoreServer(root / 'state') as server:
                    fixture.update(url=server.url, phase=phase)
                    fixture_path.write_text(json.dumps(fixture), encoding='utf-8')
                    output = directory / phase
                    output.mkdir(exist_ok=True)
                    with (output / 'browser.log').open('w', encoding='utf-8') as log:
                        completed = subprocess.run(['node', str(ROOT / 'scripts/verify_project_onboarding_browser.cjs'), str(fixture_path), str(output)], stdout=log, stderr=subprocess.STDOUT, timeout=180, check=False)
                    report = json.loads((output / 'onboarding-browser.json').read_text(encoding='utf-8'))
                    if completed.returncode or report.get('result') != 'PASS' or not report.get('checks') or any(value is not True for value in report['checks'].values()):
                        raise RuntimeError(f'{phase}: {report.get("reason", "UI checks failed")}')
                    summary[phase] = report
                    if phase == 'exercise':
                        saved = json.loads((output / 'reopen-fixture.json').read_text(encoding='utf-8'))
                        fixture.update(work_id=saved['work_id'], scope_id=saved['scope_id'])
            summary['original_file_unchanged'] = (root / 'project' / 'hello.txt').read_text(encoding='utf-8') == 'original onboarding file\n'
            if summary['original_file_unchanged'] is not True:
                raise RuntimeError('Original project file changed')
        summary['result'] = 'PASS'
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        summary.update(result='FAIL', reason=str(error)[:1000])
    finally:
        if summary['result'] == 'RUNNING':
            summary.update(result='FAIL', reason='RUN_INTERRUPTED')
        (directory / 'report.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary))
    return 0 if summary['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
