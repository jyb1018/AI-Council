#!/usr/bin/env python3
"""Project-local, opt-in submodule lifecycle. Python 3.10+ and Git; no model calls."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit

HOME = '.universal-harness'
MODULE = HOME + '/module'
CONFIG = HOME + '/config.json'
LOCK = HOME + '/MODULE.json'
STATE = HOME + '/STATE.json'
JOURNAL = HOME + '/.transaction.json'
MUTEX = HOME + '/.lifecycle-lock'
EXTRAS = {
    HOME + '/harness': 'scripts/harness',
    HOME + '/harness.cmd': 'scripts/harness.cmd',
    HOME + '/lifecycle.py': 'scripts/harness.py',
}
IGNORE = HOME + '/.gitignore'
IGNORE_BYTES = b'/.transaction.json\n/.lifecycle-lock/\n'
HEX = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
TAG = re.compile(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
MAX_BYTES = 32 * 1024 * 1024


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encode(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=True, indent=2, sort_keys=True) + '\n').encode()


def path_at(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or '\\' in relative or ':' in relative
            or relative.startswith('/') or any(p in ('', '.', '..') for p in relative.split('/'))):
        raise ValueError('Unsafe relative path')
    result = root
    for part in relative.split('/'):
        if part.endswith(('.', ' ')) or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part):
            raise ValueError('Non-portable destination name')
        result /= part
        if result.is_symlink():
            raise ValueError('Symlink destination refused: ' + relative)
    return result


def read_optional(root: Path, relative: str) -> bytes | None:
    path = path_at(root, relative)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Not a bounded regular file: ' + relative)
    return path.read_bytes()


def object_file(root: Path, relative: str) -> dict | None:
    raw = read_optional(root, relative)
    if raw is None:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('Expected JSON object: ' + relative)
    return data


def atomic_write(path: Path, raw: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.uh-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(argv: list[str], cwd: Path, timeout: int = 60) -> bytes:
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    # Do not inherit another repository/index through the caller's environment.
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'):
        env.pop(key, None)
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=timeout)
    if result.returncode:
        message = result.stderr.decode('utf-8', errors='replace')[-3000:]
        raise ValueError('Command failed: ' + argv[0] + '\n' + message)
    if len(result.stdout) > MAX_BYTES:
        raise ValueError('Command output exceeded lifecycle budget')
    return result.stdout


def git(root: Path, *args: str) -> bytes:
    return run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                '-c', 'core.hooksPath=' + os.devnull, '-c', 'submodule.recurse=false',
                '-c', 'protocol.ext.allow=never', *args], root)


def oid(value: str) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError('Expected a full immutable commit ID')
    return value


def source_url(value: str) -> str:
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        # Local test repositories may have spaces; no shell is ever used.
        if not (isinstance(value, str) and Path(value).is_absolute() and '\n' not in value):
            raise ValueError('Invalid source URL')
    if Path(value).is_absolute():
        return value
    parsed = urlsplit(value)
    if (parsed.scheme in ('https', 'ssh') and parsed.hostname and not parsed.password
            and not parsed.query and not parsed.fragment):
        if parsed.scheme == 'https' and parsed.username:
            raise ValueError('Do not store credentials in config.json')
        return value
    if re.fullmatch(r'git@[a-zA-Z0-9.-]+:[a-zA-Z0-9_./-]+', value):
        return value
    raise ValueError('Use an absolute local path, HTTPS, or SSH source without credentials')


def allowed_payload(relative: str) -> bool:
    if re.fullmatch(r'\.agents/skills/uh-[a-z0-9-]+/SKILL\.md', relative):
        return True
    reserved = {p.casefold() for p in (CONFIG, LOCK, STATE, JOURNAL, IGNORE,
                                       HOME + '/AGENTS.proposed.md', HOME + '/module', *EXTRAS)}
    return (bool(re.fullmatch(r'\.universal-harness/[A-Za-z0-9][A-Za-z0-9_.-]*', relative))
            and relative.casefold() not in reserved and not relative.endswith('.'))


def allowed_change(relative: str) -> bool:
    return relative in {'AGENTS.md', CONFIG, LOCK, STATE, IGNORE, *EXTRAS} or allowed_payload(relative)


@contextmanager
def mutation_guard(project: Path):
    directory = path_at(project, MUTEX)
    try:
        directory.mkdir()
    except FileExistsError as exc:
        raise ValueError('Another lifecycle command or stale lock exists. Confirm no process is running '
                         'before manually removing .universal-harness/.lifecycle-lock.') from exc
    try:
        yield
    finally:
        directory.rmdir()


# A trusted entry point invokes the candidate installer's READ-ONLY planner in a
# separate interpreter. This is execution of the explicitly selected upstream
# source, not a sandbox; check/pull/diff/status never execute that candidate.
PLAN_DRIVER = r'''
import base64, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import install
args = json.loads(sys.argv[2])
roots = args['global_roots']
if roots is not None:
    roots = [pathlib.Path(p) for p in roots]
changes, state = install.plan_install(pathlib.Path(args['project']), args['profile'],
                                     True, roots, args['adopt'])
print(json.dumps({'changes': {k: base64.b64encode(v).decode() for k,v in changes.items()},
                  'state': state}))
'''


class Lifecycle:
    def __init__(self, project: Path):
        project = project.expanduser().absolute()
        if any(p.is_symlink() for p in (project, *project.parents)):
            raise ValueError('Symlink project root refused')
        self.project = project.resolve(strict=True)
        if self.project == Path(self.project.anchor):
            raise ValueError('Filesystem root is not a project')
        actual = Path(git(self.project, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
        if actual != self.project:
            raise ValueError('--project must name the parent Git repository root')
        self.module = path_at(self.project, MODULE)

    def module_info(self) -> dict:
        entries = git(self.project, 'ls-files', '--stage', '-z', '--', MODULE).split(b'\0')
        entries = [e for e in entries if e]
        if len(entries) != 1:
            raise ValueError('Add the harness as a Git submodule at ' + MODULE + ' first')
        metadata, name = entries[0].split(b'\t', 1)
        mode, revision, stage = metadata.decode().split()
        if mode != '160000' or stage != '0' or name.decode() != MODULE:
            raise ValueError('Expected one unconflicted submodule gitlink')
        if not (self.module / '.git').exists():
            raise ValueError('Submodule is uninitialized. Run git submodule update --init -- ' + MODULE)
        actual = Path(git(self.module, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
        if actual != self.module:
            raise ValueError('Module path is not an independent initialized Git repository')
        url = source_url(git(self.module, 'remote', 'get-url', 'origin').decode().strip())
        # The committed submodule URL and effective origin must agree. Do not sync
        # remotes, select a replacement mirror, or rewrite .gitmodules implicitly.
        paths = git(self.project, 'config', '-f', '.gitmodules', '--get-regexp', r'^submodule\..*\.path$')
        keys = [line.decode().split(' ', 1)[0][:-5] + '.url' for line in paths.splitlines()
                if line.decode().split(' ', 1)[-1] == MODULE]
        if len(keys) != 1 or git(self.project, 'config', '-f', '.gitmodules', '--get', keys[0]).decode().strip() != url:
            raise ValueError('Submodule URL/origin mismatch; reconcile explicitly')
        return {'source_revision': oid(git(self.module, 'rev-parse', 'HEAD').decode().strip()),
                'gitlink_revision': oid(revision), 'source': url,
                'dirty': bool(git(self.module, 'status', '--porcelain', '--untracked-files=all'))}

    def config(self, info: dict | None = None) -> dict:
        data = object_file(self.project, CONFIG)
        if data is None:
            raise ValueError('No lifecycle config; run the module lifecycle install command first')
        if (data.get('schema_version') != 1 or data.get('channel') not in ('stable', 'edge', 'pinned')
                or data.get('profile') not in ('generic', 'sol', 'astra')
                or set(data) - {'schema_version', 'source', 'channel', 'ref', 'profile'}):
            raise ValueError('Invalid lifecycle config')
        source_url(data.get('source'))
        if data['channel'] == 'pinned':
            oid(data.get('ref'))
        elif data.get('ref') is not None:
            raise ValueError('config.ref is only for a pinned full commit')
        if info is not None and data['source'] != info['source']:
            raise ValueError('Configured source does not match the submodule origin')
        return data

    def lock(self) -> dict | None:
        data = object_file(self.project, LOCK)
        if data is None:
            return None
        if (data.get('schema_version') != 1 or not HASH.fullmatch(str(data.get('installer_state_sha256', '')))
                or not isinstance(data.get('files'), dict) or set(data['files']) != {*EXTRAS, IGNORE}):
            raise ValueError('Invalid lifecycle lock')
        oid(data.get('applied_revision'))
        source_url(data.get('source'))
        for name, item in data['files'].items():
            if (not isinstance(item, dict) or not HASH.fullmatch(str(item.get('sha256', '')))
                    or type(item.get('executable')) is not bool):
                raise ValueError('Invalid lifecycle file ownership: ' + name)
        return data

    def integrity(self) -> list[str]:
        lock = self.lock()
        problems = []
        state_raw = read_optional(self.project, STATE)
        state = object_file(self.project, STATE)
        if lock:
            if state_raw is None or digest(state_raw) != lock['installer_state_sha256']:
                problems.append('STATE.json differs from the applied lifecycle lock; reconcile standalone installs')
            for name, item in lock['files'].items():
                raw = read_optional(self.project, name)
                if raw is None or digest(raw) != item['sha256']:
                    problems.append('Modified/missing lifecycle file: ' + name)
                elif os.name != 'nt' and item['executable'] and not (path_at(self.project, name).stat().st_mode & stat.S_IXUSR):
                    problems.append('Wrapper executable bit missing: ' + name)
        if state:
            if state.get('schema_version') not in (2, 3) or not isinstance(state.get('files'), dict):
                raise ValueError('Invalid installer state')
            for name, expected in state['files'].items():
                # The old proposal and whole-file AGENTS are handled by the
                # installer's migration logic, never garbage-collected here.
                if name in ('AGENTS.md', HOME + '/AGENTS.proposed.md'):
                    continue
                if not allowed_payload(name) or not HASH.fullmatch(str(expected)):
                    raise ValueError('Unsafe/invalid installer ownership: ' + name)
                raw = read_optional(self.project, name)
                if raw is None or digest(raw) != expected:
                    problems.append('Modified/missing managed file: ' + name)
            if state['schema_version'] == 3:
                raw = read_optional(self.project, 'AGENTS.md') or b''
                begin, end = b'<!-- UNIVERSAL-HARNESS:BEGIN -->', b'<!-- UNIVERSAL-HARNESS:END -->'
                a, b = raw.find(begin), raw.find(end)
                if (raw.count(begin) != 1 or raw.count(end) != 1 or a >= b
                        or digest(raw[a:b + len(end)]) != state.get('agents', {}).get('sha256')):
                    problems.append('AGENTS.md managed block differs; project-owned text is not checked')
        return problems

    def ready(self) -> tuple[dict, dict]:
        if path_at(self.project, JOURNAL).exists():
            raise ValueError('Interrupted transaction exists; inspect status and run recover before updating')
        info = self.module_info()
        if info['dirty']:
            raise ValueError('Harness submodule has local changes; no checkout/reset is allowed')
        config = self.config(info)
        lock = self.lock()
        if lock and lock['source'] != config['source']:
            raise ValueError('Applied source changed; source migration needs explicit reconciliation')
        return info, config

    def status(self) -> dict:
        result = {'network': False, 'recovery_required': path_at(self.project, JOURNAL).exists(),
                  'active_lock': path_at(self.project, MUTEX).exists(),
                  'project_instructions': 'project-owned; not whole-file hashed'}
        lock = self.lock()
        result['applied_revision'] = lock['applied_revision'] if lock else None
        try:
            result.update(self.module_info())
            result['initialized'] = True
        except ValueError as exc:
            result.update(initialized=False, module_error=str(exc))
        if object_file(self.project, CONFIG) is not None:
            result['config'] = self.config(result if result['initialized'] else None)
        result['integrity_problems'] = self.integrity()
        result['source_not_applied'] = result.get('source_revision') != result['applied_revision']
        result['gitlink_not_applied'] = result.get('gitlink_revision') != result['applied_revision']
        result['remote_update'] = 'not_checked; use check explicitly'
        return result

    def select(self, config: dict, channel: str | None = None, ref: str | None = None) -> dict:
        channel = channel or config['channel']
        if ref and HEX.fullmatch(ref):
            return {'revision': oid(ref), 'ref': ref, 'channel': 'explicit', 'network': False}
        if ref:
            if not ref.startswith(('refs/heads/', 'refs/tags/')):
                raise ValueError('--ref needs a full commit or fully qualified refs/heads/... or refs/tags/...')
            git(self.module, 'check-ref-format', ref)
            pattern = ref
        elif channel == 'pinned':
            return {'revision': oid(config.get('ref')), 'ref': config['ref'], 'channel': channel, 'network': False}
        else:
            pattern = 'refs/heads/main' if channel == 'edge' else 'refs/tags/v*'
        refs = {}
        for line in git(self.module, 'ls-remote', 'origin', pattern, pattern + '^{}').decode().splitlines():
            value, name = line.split('\t')
            refs[name] = oid(value)
        if channel == 'stable' and ref is None:
            tags = [name for name in refs if name.startswith('refs/tags/') and TAG.fullmatch(name[10:])]
            if not tags:
                raise ValueError('No stable vMAJOR.MINOR.PATCH tags; choose edge explicitly, never silently use main')
            pattern = max(tags, key=lambda x: tuple(map(int, TAG.fullmatch(x[10:]).groups())))
        if pattern not in refs:
            raise ValueError('Requested remote ref does not exist: ' + pattern)
        return {'revision': refs.get(pattern + '^{}', refs[pattern]), 'ref': pattern,
                'channel': 'explicit' if ref else channel, 'network': True}

    def fetch_candidate(self, selected: dict) -> str:
        # FETCH_HEAD is a temporary transport result, never an ambiguous ref used
        # for installation. Detect a branch/tag moving between discovery and fetch.
        git(self.module, 'fetch', '--no-tags', '--no-recurse-submodules', 'origin', selected['ref'])
        actual = git(self.module, 'rev-parse', '--verify', 'FETCH_HEAD^{commit}').decode().strip()
        if actual != selected['revision']:
            raise ValueError('Remote ref moved during fetch; inspect and retry, no checkout or apply performed')
        return oid(actual)

    def checkout(self, revision: str) -> None:
        git(self.module, 'checkout', '--no-overwrite-ignore', '--detach', oid(revision))

    def pull(self, channel: str | None = None, ref: str | None = None) -> dict:
        with mutation_guard(self.project):
            info, config = self.ready()
            selected = self.select(config, channel, ref)
            revision = self.fetch_candidate(selected)
            # Fetch does not grant permission to discard newly introduced edits.
            if self.module_info() != info:
                raise ValueError('Submodule changed concurrently before checkout')
            self.checkout(revision)
            return {'source_revision': revision, 'applied_revision': (self.lock() or {}).get('applied_revision'),
                    'applied': False, 'gitlink_staged': False, 'selection': selected}

    @contextmanager
    def snapshot(self, revision: str):
        archive = git(self.module, 'archive', '--format=tar', oid(revision))
        with tempfile.TemporaryDirectory(prefix='uh-source-') as tmp:
            root = Path(tmp)
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                total = 0
                for item in bundle:
                    name = item.name.rstrip('/')
                    destination = path_at(root, name)
                    if item.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                    elif item.isfile():
                        total += item.size
                        if total > MAX_BYTES:
                            raise ValueError('Candidate package exceeds snapshot budget')
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.extractfile(item) as source:
                            destination.write_bytes(source.read())
                    else:
                        raise ValueError('Candidate archive contains a symlink or special file')
            yield root

    def plan(self, root: Path, config: dict, revision: str, adopt: bool,
             global_roots: list[str] | None) -> tuple[dict, dict, dict]:
        before = self.integrity()
        if adopt:
            before = [p for p in before if not p.startswith('AGENTS.md managed block')]
        if before:
            raise ValueError('\n'.join(before))
        try:
            compile((root / 'scripts/harness.py').read_bytes(), 'candidate lifecycle.py', 'exec')
        except SyntaxError as exc:
            raise ValueError('Candidate lifecycle runtime has invalid Python syntax') from exc
        args = {'project': str(self.project), 'profile': config['profile'],
                'adopt': adopt, 'global_roots': global_roots}
        result = json.loads(run([sys.executable, '-I', '-B', '-c', PLAN_DRIVER,
                                 str(root / 'scripts'), json.dumps(args)], root))
        state = result['state']
        if state.get('schema_version') != 3 or not isinstance(state.get('files'), dict):
            raise ValueError('Unsupported candidate installer plan; migrate lifecycle protocol first')
        desired = {}
        names = list(state['files'])
        if len({n.casefold() for n in names}) != len(names):
            raise ValueError('Case-colliding candidate paths')
        for name, value in result['changes'].items():
            if name not in ('AGENTS.md', STATE) and not allowed_payload(name):
                raise ValueError('Candidate plan writes outside the managed payload: ' + name)
            desired[name] = (base64.b64decode(value, validate=True), 0o644)
        for name, value in state['files'].items():
            if not allowed_payload(name) or not HASH.fullmatch(str(value)):
                raise ValueError('Invalid candidate ownership: ' + name)
        old_state = object_file(self.project, STATE) or {}
        for name in old_state.get('files', {}):
            if allowed_payload(name) and name not in state['files']:
                desired[name] = (None, 0o644)  # Only clean, previously owned retired files.
        extra_files = {}
        old_extra = (self.lock() or {}).get('files', {})
        for name, source in {**EXTRAS, IGNORE: None}.items():
            raw = (root / source).read_bytes() if source else IGNORE_BYTES
            current = read_optional(self.project, name)
            if current is not None and name not in old_extra:
                raise ValueError('Unmanaged lifecycle file collision: ' + name)
            mode = 0o755 if name == HOME + '/harness' else 0o644
            desired[name] = (raw, mode)
            extra_files[name] = {'sha256': digest(raw), 'executable': mode == 0o755}
        state_bytes = desired.get(STATE, (read_optional(self.project, STATE), 0o644))[0]
        if state_bytes is None or json.loads(state_bytes) != state:
            raise ValueError('Candidate plan state mismatch')
        lock = {'schema_version': 1, 'source': config['source'], 'applied_revision': revision,
                'profile': config['profile'], 'installer_state_sha256': digest(state_bytes),
                'files': extra_files}
        desired[LOCK] = (encode(lock), 0o644)
        if read_optional(self.project, CONFIG) is None:
            desired[CONFIG] = (encode(config), 0o644)
        changes = {}
        expected = {}
        for name, (raw, mode) in desired.items():
            current = read_optional(self.project, name)
            path = path_at(self.project, name)
            old_mode = stat.S_IMODE(path.stat().st_mode) if current is not None else None
            # Preserve existing permission bits, except the POSIX wrapper needs +x.
            if old_mode is not None:
                mode = old_mode | 0o111 if name == HOME + '/harness' else old_mode
            if current != raw or (os.name != 'nt' and raw is not None and old_mode != mode):
                changes[name] = (raw, mode)
                expected[name] = (current, old_mode)
        return changes, expected, state

    def verify(self, state: dict) -> None:
        if self.integrity():
            raise ValueError('Post-apply hash verification failed: ' + '; '.join(self.integrity()))
        if object_file(self.project, STATE) != state:
            raise ValueError('Post-apply installer state mismatch')

    def apply(self, changes: dict, expected: dict, state: dict, info: dict, revision: str) -> None:
        records = {}
        for name, (raw, mode) in changes.items():
            old, old_mode = expected[name]
            if read_optional(self.project, name) != old:
                raise ValueError('Concurrent change before transaction: ' + name)
            records[name] = {'before': None if old is None else base64.b64encode(old).decode(),
                             'mode': old_mode, 'after': None if raw is None else digest(raw),
                             'after_mode': None if raw is None else mode}
        if self.module_info() != info:
            raise ValueError('Submodule changed before transaction')
        journal = {'schema_version': 1, 'old_revision': info['source_revision'],
                   'new_revision': revision, 'files': records}
        atomic_write(path_at(self.project, JOURNAL), encode(journal), 0o600)
        try:
            if revision != info['source_revision']:
                self.checkout(revision)
            # The metadata files are published last; the journal covers both.
            order = sorted(changes, key=lambda n: (n in (STATE, LOCK), n == LOCK, n))
            for name in order:
                raw, mode = changes[name]
                if read_optional(self.project, name) != expected[name][0]:
                    raise ValueError('Concurrent edit during transaction: ' + name)
                path = path_at(self.project, name)
                if raw is None:
                    if path.exists():
                        path.unlink()
                else:
                    atomic_write(path, raw, mode)
            self.verify(state)
            if self.module_info()['source_revision'] != revision:
                raise ValueError('Module changed during verification')
        except (Exception, KeyboardInterrupt):
            self.recover(guard=False)
            raise
        path_at(self.project, JOURNAL).unlink()

    def recover(self, guard: bool = True) -> dict:
        if guard:
            with mutation_guard(self.project):
                return self.recover(guard=False)
        journal = object_file(self.project, JOURNAL)
        if journal is None:
            return {'recovered': False, 'reason': 'no pending transaction'}
        if journal.get('schema_version') != 1 or not isinstance(journal.get('files'), dict):
            raise ValueError('Invalid recovery journal; manual inspection required')
        old_revision, new_revision = oid(journal.get('old_revision')), oid(journal.get('new_revision'))
        restored = {}
        # Preflight everything before restoring anything. Never overwrite edits
        # made after an interrupted update, even when that prevents rollback.
        for name, record in journal['files'].items():
            if not allowed_change(name) or not isinstance(record, dict):
                raise ValueError('Unsafe recovery record')
            old = None if record.get('before') is None else base64.b64decode(record['before'], validate=True)
            after = record.get('after')
            if after is not None and not HASH.fullmatch(str(after)):
                raise ValueError('Invalid recovery fingerprint')
            raw = read_optional(self.project, name)
            fingerprint = None if raw is None else digest(raw)
            path = path_at(self.project, name)
            current_mode = stat.S_IMODE(path.stat().st_mode) if raw is not None else None
            same_before = raw == old and (os.name == 'nt' or current_mode == record.get('mode'))
            same_after = fingerprint == after and (os.name == 'nt' or current_mode == record.get('after_mode'))
            if not same_before and not same_after:
                raise ValueError('Recovery blocked by a subsequent edit; journal retained: ' + name)
            mode = record.get('mode')
            if old is not None and (type(mode) is not int or not 0 <= mode <= 0o777):
                raise ValueError('Invalid recovery permissions')
            restored[name] = (old, mode)
        info = self.module_info()
        if info['dirty'] or info['source_revision'] not in (old_revision, new_revision):
            raise ValueError('Recovery blocked by a changed submodule; journal retained')
        if info['source_revision'] != old_revision:
            self.checkout(old_revision)
        for name, (old, mode) in restored.items():
            path = path_at(self.project, name)
            if old is None:
                if path.exists():
                    path.unlink()
                # Only remove now-empty generated ancestors, never arbitrary data.
                parent = path.parent
                while parent not in (self.project, self.module, self.project / HOME):
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
            else:
                atomic_write(path, old, mode)
        path_at(self.project, JOURNAL).unlink()
        return {'recovered': True, 'source_revision': old_revision}

    def install_or_upgrade(self, *, initial: bool = False, profile: str | None = None,
                           channel: str | None = None, ref: str | None = None,
                           offline: bool = False, dry_run: bool = False, adopt: bool = False,
                           global_roots: list[str] | None = None) -> dict:
        # Dry run is a no-fetch/no-checkout preview of the CURRENT source. Remote
        # discovery belongs to check; pull explicitly makes a candidate available.
        if offline and (channel or ref):
            raise ValueError('offline uses the current checkout; pull the requested ref first')
        if dry_run and (ref or (channel and not initial)):
            raise ValueError('dry-run previews the current checkout; pull an explicit ref first')
        if initial:
            info = self.module_info()
            if info['dirty']:
                raise ValueError('Harness submodule has local changes')
            existing = object_file(self.project, CONFIG)
            config = self.config(info) if existing else {
                'schema_version': 1, 'source': info['source'], 'profile': profile or 'generic',
                'channel': channel or 'stable'}
            if not existing and ref and (config['channel'] != 'pinned' or ref != info['source_revision']):
                raise ValueError('Initial install uses the current checkout; pinned --ref must match it')
            if not existing and config['channel'] == 'pinned':
                config['ref'] = oid(ref or info['source_revision'])
            if existing and ((profile and profile != config['profile']) or channel or ref):
                raise ValueError('Existing config is project-owned; edit config.json explicitly')
        else:
            info, config = self.ready()
            if self.lock() is None:
                raise ValueError('Run lifecycle install once before upgrade')
        if path_at(self.project, JOURNAL).exists():
            raise ValueError('Interrupted transaction exists; run recover first')
        def perform():
            revision = info['source_revision']
            selection = {'revision': revision, 'channel': 'current_checkout', 'network': False}
            if not initial and not offline and not dry_run:
                selection = self.select(config, channel, ref)
                revision = self.fetch_candidate(selection)
            with self.snapshot(revision) as root:
                if selection['channel'] == 'stable':
                    version = json.loads((root / 'harness.json').read_bytes())['version']
                    if selection['ref'] != 'refs/tags/v' + version:
                        raise ValueError('Stable tag does not match manifest version')
                    previous = (object_file(self.project, STATE) or {}).get('version', '')
                    old_tag, new_tag = TAG.fullmatch('v' + previous), TAG.fullmatch('v' + version)
                    if old_tag and new_tag and tuple(map(int, new_tag.groups())) < tuple(map(int, old_tag.groups())):
                        raise ValueError('Stable target is older than the applied version; use an explicit --ref to downgrade')
                changes, expected, state = self.plan(root, config, revision, adopt, global_roots)
                report = {'dry_run': dry_run, 'selection': selection, 'applied_revision': revision,
                          'profile': config['profile'], 'changes': sorted(changes),
                          'gitlink_staged': False, 'model_behavioral_evaluation': 'NOT RUN'}
                if not dry_run:
                    self.apply(changes, expected, state, info, revision)
                    report['applied'] = True
                else:
                    report['applied'] = False
                return report
        if dry_run:
            return perform()
        with mutation_guard(self.project):
            return perform()

    def diff(self) -> str:
        info = self.module_info()
        lock = self.lock()
        if lock is None:
            raise ValueError('No applied revision; install first')
        base = oid(lock['applied_revision'])
        output = git(self.module, 'diff', '--no-ext-diff', '--no-textconv', '--stat',
                     base, info['source_revision'], '--').decode('utf-8', errors='replace')
        return (f"Applied {base}\nSource  {info['source_revision']}\n" + output
                + '\nSource diff only; local edits and migrations require upgrade --dry-run.\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd(), help='Parent repository root (wrapper supplies this)')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status', help='Offline source/applied/integrity status; no update checks')
    commands.add_parser('diff', help='Offline applied-to-source Git diff summary')
    commands.add_parser('recover', help='Restore a pending journal without overwriting subsequent edits')
    for name in ('check', 'pull', 'install', 'upgrade'):
        p = commands.add_parser(name)
        p.add_argument('--channel', choices=('stable', 'edge', 'pinned'))
        p.add_argument('--ref', help='Full commit or refs/heads/... or refs/tags/...; command-scoped override')
        if name in ('install', 'upgrade'):
            p.add_argument('--dry-run', action='store_true', help='Preview current checkout, with no fetch or writes')
            p.add_argument('--adopt-agents-block', action='store_true')
            p.add_argument('--global-skill-root', action='append', help='Active global roots; same meaning as install.py')
        if name == 'install':
            p.add_argument('--profile', choices=('generic', 'sol', 'astra'))
        if name == 'upgrade':
            p.add_argument('--offline', action='store_true', help='Apply current module without checking upstream')
    p = commands.add_parser('doctor', help='Run the applied read-only tooling doctor')
    p.add_argument('--strict', action='store_true')
    p.add_argument('--global-skill-root', action='append')
    args = parser.parse_args(argv)
    try:
        lifecycle = Lifecycle(args.project)
        if args.command == 'status':
            result = lifecycle.status()
        elif args.command == 'diff':
            print(lifecycle.diff())
            return 0
        elif args.command == 'recover':
            result = lifecycle.recover()
        elif args.command in ('check', 'pull'):
            if args.command == 'pull':
                result = lifecycle.pull(args.channel, args.ref)
            else:
                info = lifecycle.module_info()
                config = lifecycle.config(info)
                result = lifecycle.select(config, args.channel, args.ref)
                result['source_revision'] = info['source_revision']
                result['applied_revision'] = (lifecycle.lock() or {}).get('applied_revision')
                result['update_available'] = result['revision'] != result['applied_revision']
        elif args.command == 'doctor':
            script = path_at(lifecycle.project, HOME + '/tooling.py')
            if not script.is_file():
                raise ValueError('Applied tooling doctor not found')
            command = [sys.executable, str(script), 'doctor', str(lifecycle.project)]
            if args.strict:
                command.append('--strict')
            for root in args.global_skill_root or []:
                command += ['--global-skill-root', root]
            # Preserve doctor JSON and its strict exit status.
            return subprocess.call(command, cwd=lifecycle.project)
        else:
            result = lifecycle.install_or_upgrade(
                initial=args.command == 'install', profile=getattr(args, 'profile', None),
                channel=args.channel, ref=args.ref, offline=getattr(args, 'offline', False),
                dry_run=args.dry_run, adopt=args.adopt_agents_block,
                global_roots=args.global_skill_root)
        print(encode(result).decode(), end='')
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired, tarfile.TarError) as exc:
        print('HARNESS FAILED: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
