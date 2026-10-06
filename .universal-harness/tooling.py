#!/usr/bin/env python3
"""Read-only skill inventory and advisory capability routing. Python 3.10+."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
MAX_BYTES = 65536
MAX_DIRS = 2000
SKIP_DIRS = {'.git', '.venv', 'node_modules', '__pycache__'}
SUPPORT_FILES = {
    '.universal-harness/tooling.py': 'scripts/tooling.py',
    '.universal-harness/tooling.json': 'integrations/tooling.json',
    '.universal-harness/TOOLING.md': 'docs/TOOLING.md',
}


def default_catalog() -> Path:
    here = Path(__file__).resolve().parent
    installed = here / 'tooling.json'
    return installed if installed.is_file() else here.parent / 'integrations/tooling.json'


def load_catalog(path: Path) -> dict:
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Tool catalog exceeds size limit')
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise ValueError('Unsupported tool catalog schema')
    providers, capabilities = data.get('providers'), data.get('capabilities')
    if not isinstance(providers, dict) or not providers or not isinstance(capabilities, dict) or not capabilities:
        raise ValueError('Tool catalog needs providers and capabilities')
    for name, item in providers.items():
        if not NAME.fullmatch(name) or not isinstance(item, dict):
            raise ValueError('Invalid provider')
        if item.get('kind') not in {'skill', 'cli', 'mcp', 'plugin', 'builtin'}:
            raise ValueError(f'Invalid provider kind: {name}')
        for field in ('skills', 'executables'):
            values = item.get(field)
            if not isinstance(values, list) or not all(
                    isinstance(v, str) and NAME.fullmatch(v) for v in values) or len(values) != len(set(values)):
                raise ValueError(f'Invalid {field}: {name}')
        if not isinstance(item.get('network'), bool):
            raise ValueError(f'Missing network classification: {name}')
    for name, item in capabilities.items():
        if not NAME.fullmatch(name) or not isinstance(item, dict):
            raise ValueError('Invalid capability')
        choices = item.get('providers')
        if not isinstance(choices, list) or not choices or any(
                not isinstance(v, str) or v not in providers for v in choices) or len(choices) != len(set(choices)):
            raise ValueError(f'Invalid capability providers: {name}')
        if not isinstance(item.get('fallback'), str) or not item['fallback'].strip():
            raise ValueError(f'Missing fallback: {name}')
        if not isinstance(item.get('checks'), list) or not item['checks'] or not all(
                isinstance(v, str) and v.strip() for v in item['checks']):
            raise ValueError(f'Missing capability checks: {name}')
    return data


def validate_package(root: Path) -> list[str]:
    """Validate v3 distribution contracts, not provider or model behavior."""
    errors = []
    expected = SUPPORT_FILES
    try:
        meta = json.loads((root / 'harness.json').read_text(encoding='utf-8'))
        if meta.get('support_files') != expected:
            errors.append('Tooling support file mapping mismatch')
        for source in expected.values():
            if not (root / source).is_file():
                errors.append('Missing tooling support source: ' + source)
        if meta.get('tooling_evaluation_cases') != 'evals/tooling-cases.json':
            errors.append('Tooling eval manifest path mismatch')
        if 'uh-tooling' not in meta['skills']:
            errors.append('Missing uh-tooling skill in manifest')
        if meta.get('runtime_configuration_managed') is not False or meta.get('upstream_plugins_vendored') is not False:
            errors.append('Tooling must not own global runtime or upstream plugins')
        catalog = load_catalog(root / 'integrations/tooling.json')
        required = {'discover', 'author-skill', 'library-docs', 'symbols', 'architecture',
                    'browser-check', 'browser-debug', 'repository', 'independent-review',
                    'security-review', 'production-debug'}
        if not required.issubset(catalog['capabilities']):
            errors.append('Missing tooling capability routes')
        cases = json.loads((root / 'evals/tooling-cases.json').read_text(encoding='utf-8'))
        if cases.get('schema_version') != 2 or cases.get('kind') != 'behavioral_scenarios_not_execution_results':
            errors.append('Invalid tooling eval schema/evidence kind')
        ids = [case['id'] for case in cases['cases']]
        required_ids = {'tooling-absent', 'tooling-identical-duplicate', 'tooling-variant-duplicate',
                        'tooling-instructions-only', 'tooling-mcp-inactive', 'tooling-stale-graph',
                        'tooling-external-egress', 'tooling-browser-evidence', 'tooling-auth-recovery',
                        'tooling-resume', 'tooling-untrusted-output', 'tooling-no-ceremony'}
        if len(ids) != len(set(ids)) or not required_ids.issubset(ids):
            errors.append('Missing or duplicated tooling eval IDs')
        for case in cases['cases']:
            if not isinstance(case['prompt'], str) or not case['prompt'].strip():
                errors.append('Invalid tooling eval prompt')
            for key in ('required', 'forbidden'):
                if not isinstance(case[key], list) or not case[key] or not all(
                        isinstance(v, str) and v.strip() for v in case[key]):
                    errors.append('Invalid tooling eval acceptance: ' + key)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append('Tooling package metadata error: ' + str(exc))
    return errors


def skill_roots(project: Path, global_roots: list[Path] | None = None) -> list[tuple[str, Path]]:
    """CWD through nearest Git/worktree root; non-Git projects stay at CWD."""
    project = project.expanduser().resolve(strict=True)
    if not project.is_dir():
        raise ValueError('Project must be an existing directory')
    ancestors = [project, *project.parents]
    boundary = next((p for p in ancestors if (p / '.git').exists()), project)
    roots = []
    for path in ancestors:
        roots.append(('repo', path / '.agents/skills'))
        if path == boundary:
            break
    if global_roots is None:
        home = Path.home()
        codex_home = Path(os.environ.get('CODEX_HOME', str(home / '.codex'))).expanduser()
        global_roots = [home / '.agents/skills', codex_home / 'skills']
        if os.name != 'nt':
            global_roots.append(Path('/etc/codex/skills'))
    # Deduplicate repeated path arguments, NOT distinct symlink exposures.
    seen = set()
    for path in global_roots:
        roots.append(('global', path.expanduser().absolute()))
    unique = []
    for scope, path in roots:
        if str(path) not in seen:
            seen.add(str(path))
            unique.append((scope, path))
    return unique


def skill_name(raw: bytes) -> str:
    """Check basic required fields, not arbitrary YAML or host activation.

    Support common scalar/block descriptions without adding a YAML dependency.
    Unsupported required-field syntax is reported as an incomplete inspection.
    """
    lines = raw.decode('utf-8-sig').splitlines()
    if not lines or lines[0] != '---':
        raise ValueError('Missing frontmatter')
    try:
        end = lines.index('---', 1)
    except ValueError as exc:
        raise ValueError('Unclosed frontmatter') from exc
    names = [line for line in lines[1:end] if re.match(r'^name\s*:', line)]
    if len(names) != 1:
        raise ValueError('Expected one frontmatter name')
    match = re.fullmatch(r'''name[ \t]*:[ \t]+(["']?)([a-z0-9][a-z0-9-]{0,63})\1[ \t]*(?:#.*)?''', names[0])
    if not match:
        raise ValueError('Unsupported skill name syntax')
    descriptions = [(i, line) for i, line in enumerate(lines[1:end], 1)
                    if re.match(r'^description\s*:', line)]
    if len(descriptions) != 1:
        raise ValueError('Expected one frontmatter description')
    index, line = descriptions[0]
    description = re.fullmatch(r'description[ \t]*:[ \t]+(.+)', line)
    if not description:
        raise ValueError('Missing or unsupported skill description')
    value = description[1].strip()
    if not value:
        raise ValueError('Empty skill description')
    if value[0] in '|>':
        if not re.fullmatch(r'[|>][+-]?(?:\s+#.*)?', value):
            raise ValueError('Unsupported block description syntax')
        block = []
        for continuation in lines[index + 1:end]:
            if continuation and not continuation.startswith(' '):
                break
            block.append(continuation.strip())
        if not any(block):
            raise ValueError('Empty skill description')
    elif value[0] in "\"'":
        # Preserve YAML single-quote escaping; double-quote content remains
        # opaque, since validating every YAML escape is the host's job.
        quoted = re.fullmatch(r'''(["'])(.*)\1(?:\s+#.*)?''', value)
        if not quoted or not quoted[2].strip():
            raise ValueError('Empty or unsupported quoted description')
    elif (value[0] in '[{&*!@`#' or value.lower() in {'null', '~', 'true', 'false'}
          or re.fullmatch(r'[-+]?\d+(?:\.\d+)?', value)):
        raise ValueError('Description must be a nonempty string scalar')
    return match[2]


def scan_skills(roots: list[tuple[str, Path]]) -> dict:
    """Read only bounded SKILL.md data. Never import/execute a discovered skill."""
    skills, warnings = [], []
    for scope, root in roots:
        if not root.exists() and not root.is_symlink():
            continue
        if not root.is_dir():
            warnings.append(f'Unreadable skill root: {root}')
            continue
        visited = set()
        def walk_error(exc):
            warnings.append(f'Skill scan failed: {exc.filename} ({type(exc).__name__})')
        for visits, (folder, directories, files) in enumerate(
                os.walk(root, followlinks=True, onerror=walk_error), 1):
            # Count exposed directories, including aliases to a visited target.
            if visits > MAX_DIRS:
                warnings.append(f'Skill scan limit reached: {root}')
                break
            path = Path(folder)
            try:
                real = path.resolve(strict=True)
            except (OSError, RuntimeError):
                warnings.append(f'Unresolvable skill directory: {path}')
                directories[:] = []
                continue
            if real in visited:
                directories[:] = []
                # Still inspect SKILL.md below: alias exposure can be ambiguous.
            else:
                visited.add(real)
                directories[:] = sorted(d for d in directories if d not in SKIP_DIRS)
            # os.walk lists dangling directory symlinks as files.
            for filename in files:
                link = path / filename
                if link.is_symlink() and not link.exists():
                    warnings.append(f'Broken skill-tree link: {link}')
            if 'SKILL.md' not in files:
                continue
            skill = path / 'SKILL.md'
            try:
                # Refuse FIFOs/devices; avoid opening an unexpected special file.
                if not skill.is_file():
                    raise ValueError('Not a regular file')
                with skill.open('rb') as stream:
                    raw = stream.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError('Skill exceeds scan size limit')
                name = skill_name(raw)
                skills.append({'name': name, 'scope': scope, 'path': str(skill),
                               'resolved_path': str(skill.resolve(strict=True)),
                               'sha256': hashlib.sha256(raw).hexdigest()})
            except (OSError, ValueError, UnicodeError, RuntimeError) as exc:
                warnings.append(f'Skill not inspected: {skill} ({type(exc).__name__})')
    by_name = {}
    for item in skills:
        by_name.setdefault(item['name'], []).append(item)
    duplicates = []
    for name, items in sorted(by_name.items()):
        if len(items) > 1:
            same_target = len({i['resolved_path'] for i in items}) == 1
            same_text = len({i['sha256'] for i in items}) == 1
            duplicates.append({'name': name, 'kind': 'alias' if same_target else
                               'same_skill_md' if same_text else 'different_skill_md',
                               'paths': [i['path'] for i in items]})
    return {'skills': sorted(skills, key=lambda i: (i['name'], i['path'])),
            'duplicates': duplicates, 'warnings': sorted(set(warnings))}


def inventory(project: Path, catalog: dict, global_roots: list[Path] | None = None,
              available: tuple[str, ...] = ()) -> dict:
    unknown = set(available) - set(catalog['providers'])
    if unknown:
        raise ValueError('Unknown host-reported provider: ' + ', '.join(sorted(unknown)))
    roots = skill_roots(project, global_roots)
    scan = scan_skills(roots)
    names = {i['name'] for i in scan['skills']}
    ambiguous = {i['name'] for i in scan['duplicates']}
    providers = {}
    for name, item in catalog['providers'].items():
        skills = sorted(names.intersection(item['skills']))
        executables = {exe: shutil.which(exe) for exe in item['executables']}
        cli_found = any(executables.values())
        if ambiguous.intersection(item['skills']):
            status = 'ambiguous_skill'
        elif name in available:
            status = 'host_reported'
        elif item['kind'] == 'cli' and cli_found:
            status = 'cli_on_path'
        elif item['kind'] == 'skill' and skills and cli_found:
            status = 'skill_and_cli'
        elif skills:
            status = 'instructions_only'
        elif cli_found:
            status = 'runtime_unverified'
        else:
            status = 'not_observed'
        providers[name] = {'status': status, 'skills': skills, 'executables': executables,
                           'readiness': 'unverified'}
    return {'schema_version': 1, 'kind': 'read_only_tooling_inventory',
            'project': str(project.expanduser().resolve()),
            'roots': [{'scope': scope, 'path': str(path)} for scope, path in roots],
            **scan, 'providers': providers,
            'limitations': ['Filesystem presence is not host activation or authentication.',
                           'Only basic name/description fields are checked, not full YAML or dependencies.',
                           'Plugin catalogs and disabled-skill settings are not inspected.',
                           'Same SKILL.md hash does not establish identical supporting files.',
                           'No command, network request, config edit, install or deletion was performed.']}


def route(capability: str, catalog: dict, report: dict, offline: bool = False) -> dict:
    if capability not in catalog['capabilities']:
        raise ValueError('Unknown capability: ' + capability)
    spec = catalog['capabilities'][capability]
    candidates = []
    for name in spec['providers']:
        if offline and catalog['providers'][name]['network']:
            continue
        if not report['warnings'] and report['providers'][name]['status'] in {
                'host_reported', 'cli_on_path', 'skill_and_cli'}:
            candidates.append(name)
    return {'capability': capability, 'candidate': candidates[0] if candidates else None,
            'alternatives': candidates[1:], 'fallback': spec['fallback'],
            'checks_before_use': spec['checks'], 'offline': offline,
            'readiness': 'unverified', 'authorization': 'not_granted_by_report',
            'note': 'Advisory only. Confirm current host availability, target and permission; '
                    'fallback is not equivalent proof that the preferred tool ran.'}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('doctor', 'route'))
    parser.add_argument('project', type=Path)
    parser.add_argument('--catalog', type=Path, default=default_catalog())
    roots = parser.add_mutually_exclusive_group()
    roots.add_argument('--global-skill-root', action='append', type=Path,
                       help='Override default global skill roots; repeat for each root')
    roots.add_argument('--no-global-skills', action='store_true')
    parser.add_argument('--available', action='append', default=[],
                        help='Provider ID observed in THIS host session; repeat. Not a health check')
    parser.add_argument('--capability', help='Required for route; see catalog capability IDs')
    parser.add_argument('--offline', action='store_true', help='Exclude network-classified route candidates')
    parser.add_argument('--strict', action='store_true', help='Exit 1 for duplicate names or incomplete scan')
    parser.add_argument('--summary', action='store_true',
                        help='Print counts and the requested route without the full skill inventory')
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.catalog)
        report = inventory(args.project, catalog,
                           [] if args.no_global_skills else args.global_skill_root,
                           tuple(args.available))
        if args.command == 'route':
            if not args.capability:
                parser.error('route requires --capability')
            report['route'] = route(args.capability, catalog, report, args.offline)
        exit_code = int(args.strict and bool(report['duplicates'] or report['warnings']))
        if args.summary:
            summary = {'schema_version': 1, 'kind': 'read_only_tooling_summary',
                       'project': report['project'],
                       'counts': {key: len(report[key]) for key in ('skills', 'duplicates', 'warnings')},
                       'readiness': 'unverified', 'limitations': report['limitations'],
                       'note': 'Run doctor without --summary for individual paths and diagnostics.'}
            if 'route' in report:
                summary['route'] = report['route']
            report = summary
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return exit_code
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f'TOOLING FAILED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
