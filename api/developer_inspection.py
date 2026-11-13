"""Bounded inspection of shipped source. No model commands, imports or writes."""
import ast
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

ROOTS = {'api', 'src', 'app', 'scripts', 'protocol-images', 'docs'}
TOP = {'package.json', 'package-lock.json', 'tsconfig.json', 'eslint.config.mjs', 'vite.config.ts', 'Caddyfile', 'README.md'}
EXTENSIONS = {'.py', '.ts', '.tsx', '.js', '.mjs', '.css', '.sh', '.json', '.md'}
EXCLUDED = {'node_modules', 'vendor', 'venv', '__pycache__', 'dist', 'outputs', 'tmp'}
MAX_FILE = 2 * 1024 * 1024


class Query(BaseModel):
    model_config = {'extra': 'forbid'}
    command: Literal['inventory', 'read', 'search', 'symbols', 'dependencies'] = 'inventory'
    path: str = Field(default='', max_length=240)
    query: str = Field(default='', max_length=120)
    line: int = Field(default=1, strict=True, ge=1, le=100000)
    lines: int = Field(default=80, strict=True, ge=1, le=160)
    offset: int = Field(default=0, strict=True, ge=0, le=3000)
    limit: int = Field(default=20, strict=True, ge=1, le=20)


def allowed(path, *, directory=False):
    if not path or '\\' in path or ':' in path or '\x00' in path: return False
    parts = path.split('/')
    if any(not part or part.startswith('.') or part in EXCLUDED for part in parts): return False
    if parts[0] not in ROOTS and path not in TOP: return False
    if not directory and Path(path).suffix == '.json' and path not in TOP and parts[-1] not in {'manifest.json', 'telegram_catalog.json', 'reality-targets.json'} and not path.startswith('api/resources/'):
        return False
    return directory or path in TOP or parts[-1] == 'requirements.txt' or Path(path).suffix in EXTENSIONS


def read_bytes(root, path):
    if not allowed(path): raise HTTPException(422, 'Доступны только исходники приложения; runtime, окружение и произвольные пути закрыты')
    try:
        if os.name == 'posix':
            # Resolve each component through a directory descriptor: a concurrent
            # symlink replacement cannot redirect this reader outside the source.
            with ExitStack() as stack:
                fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                stack.callback(os.close, fd)
                parts = path.split('/')
                for part in parts[:-1]:
                    fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    stack.callback(os.close, fd)
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                stack.callback(os.close, fd)
                details = os.fstat(fd)
                if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_FILE or details.st_nlink > 1: raise ValueError()
                with os.fdopen(os.dup(fd), 'rb') as stream: raw = stream.read(MAX_FILE + 1)
        else:
            candidate = root
            for part in path.split('/'):
                candidate /= part
                if candidate.is_symlink(): raise ValueError()
            if not candidate.resolve().is_relative_to(root) or not candidate.is_file() or candidate.stat().st_size > MAX_FILE or candidate.stat().st_nlink > 1: raise ValueError()
            with candidate.open('rb') as stream: raw = stream.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE: raise ValueError()
        return raw
    except (OSError, ValueError):
        raise HTTPException(422, 'Файл отсутствует, превышает лимит или не является безопасным исходником') from None


def paths(root, prefix=''):
    if prefix and not allowed(prefix, directory=True): raise HTTPException(422, 'Недопустимая область исходников')
    result, truncated = [], False
    for base in sorted(ROOTS):
        if prefix and prefix.split('/')[0] != base: continue
        folder = root / base
        if folder.is_symlink() or not folder.is_dir(): continue
        for directory, directories, files in os.walk(folder, followlinks=False):
            if not Path(directory).resolve().is_relative_to(root):
                directories[:] = []; continue
            directories[:] = sorted(name for name in directories if not name.startswith('.') and name not in EXCLUDED and not (Path(directory) / name).is_symlink())
            for name in sorted(files):
                candidate = Path(directory) / name
                relative = candidate.relative_to(root).as_posix()
                if not allowed(relative) or candidate.is_symlink() or prefix and relative != prefix and not relative.startswith(prefix + '/'): continue
                try:
                    if candidate.stat().st_nlink > 1: continue
                except OSError: continue
                result.append(relative)
                if len(result) >= 3000: return result, True
    for name in sorted(TOP):
        if (root / name).is_file() and not (root / name).is_symlink() and (not prefix or name == prefix): result.append(name)
    return sorted(result), truncated


def revision(api):
    try: value = api.installed_build_commit()
    except (OSError, AttributeError): return None
    return value if isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) else None


def inspect(api, query):
    root = api.INSTALL_DIR.resolve()
    before = revision(api)
    result = {'tool': 'developer_inspect', 'command': query.command, 'provenance': {'location': 'installed_application_source', 'commit': before},
              'coverage': {'read_only': True, 'model_code_execution': False, 'untrusted_content': True, 'redaction_applied': True}}
    if query.command == 'inventory':
        files, truncated = paths(root, query.path)
        result['files'] = files[query.offset:query.offset + query.limit]
        result['coverage'].update(scanned_files=len(files), scan_truncated=truncated, next_offset=query.offset + query.limit if len(files) > query.offset + query.limit else None)
    elif query.command in ('read', 'symbols'):
        raw = read_bytes(root, query.path)
        text = raw.decode('utf-8-sig', errors='replace')
        lines = text.splitlines()
        result['provenance'].update(path=query.path, sha256=hashlib.sha256(raw).hexdigest())
        if query.command == 'read':
            selected = lines[query.line - 1:query.line - 1 + query.lines]
            rendered, used, truncated = [], 0, False
            for index, line in enumerate(selected):
                numbered = f'{query.line + index}: {line}'
                if used + len(numbered) > 12000:
                    if not rendered: rendered.append(numbered[:12000])
                    truncated = True; break
                rendered.append(numbered); used += len(numbered) + 1
            result.update(text='\n'.join(rendered), line=query.line, total_lines=len(lines))
            result['coverage'].update(next_line=query.line + len(rendered) if query.line + len(rendered) <= len(lines) else None, text_truncated=truncated)
        else:
            if not query.path.endswith('.py'): raise HTTPException(422, 'Структура функций доступна для Python; для TS/JS используйте поиск и чтение')
            try:
                tree = ast.parse(text)
                symbols = [{'name': node.name, 'kind': type(node).__name__, 'line': node.lineno, 'end_line': node.end_lineno}
                           for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                symbols.sort(key=lambda row: row['line'])
                result.update(syntax='parsed', symbols=symbols[query.offset:query.offset + query.limit])
                result['coverage']['next_offset'] = query.offset + query.limit if len(symbols) > query.offset + query.limit else None
            except SyntaxError as error: result.update(syntax='error', error={'line': error.lineno, 'offset': error.offset, 'message': error.msg})
    elif query.command == 'search':
        if not query.query.strip(): raise HTTPException(422, 'Укажите непустую строку поиска')
        files, truncated = paths(root, query.path)
        matches, skipped, budget, scanned = [], 0, 4 * 1024 * 1024, 0
        needle = query.query.casefold()
        for path in files[query.offset:]:
            try: raw = read_bytes(root, path)
            except HTTPException: skipped += 1; continue
            if len(raw) > budget: truncated = True; break
            budget -= len(raw); scanned += 1
            for number, line in enumerate(raw.decode('utf-8-sig', errors='replace').splitlines(), 1):
                if needle in line.casefold(): matches.append({'path': path, 'line': number, 'text': line[:600]})
                if len(matches) >= query.limit: break
            if len(matches) >= query.limit: truncated = True; break
        result['matches'] = matches
        result['coverage'].update(scanned_files=scanned, skipped_files=skipped, truncated=truncated,
                                  offset_unit='files; scope search to a returned path for additional matches',
                                  next_offset=query.offset + scanned + skipped if query.offset + scanned + skipped < len(files) else None)
    else:
        result['observed'] = [{key: row.get(key) for key in ('id', 'name', 'version')} for row in api.application_dependency_versions()[:20]]
        package = json.loads(read_bytes(root, 'package.json'))
        declared = {**package.get('dependencies', {}), **package.get('devDependencies', {})}
        result['node_dependencies'] = {}
        for name, requested in list(declared.items())[query.offset:query.offset + query.limit]:
            if not re.fullmatch(r'(?:@[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9_-][a-zA-Z0-9_.-]*', name) or '..' in name: continue
            result['node_dependencies'][name] = {'requested': str(requested)[:100], 'installed': api.installed_node_package_version(name) or None}
        result['coverage']['dependencies'] = 'observed application runtime and selected node declarations; missing version is unknown, no CVE or functional claim'
        result['coverage']['next_offset'] = query.offset + query.limit if len(declared) > query.offset + query.limit else None
        packages = ('systemd', 'openssl', 'libc6', 'iproute2', 'ca-certificates', 'openssh-server')
        result['os_packages'] = {}
        try:
            output = api.run('dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n', *packages, timeout=4)
            for line in output.splitlines()[:20]:
                name, _, version = line.partition('\t')
                if name.split(':')[0] in packages and re.fullmatch(r'[a-zA-Z0-9.+:~_-]{1,100}', version): result['os_packages'][name] = version
            result['coverage']['os_packages'] = 'selected_installed_dpkg_packages; absent_packages_are_unknown'
        except (OSError, ValueError, AttributeError): result['coverage']['os_packages'] = 'unavailable'
    after = revision(api)
    result['coverage']['commit_unchanged'] = before == after if before is not None and after is not None else None
    import assistant_diagnostics
    return assistant_diagnostics.sanitize(api, result, max_chars=20000, max_string=12000)


def call(api, arguments, actor):
    import assistant_control
    import assistant_roles
    settings = assistant_control.read(api)
    for permission in ('diagnostics', 'server_read', 'terminal_read'): assistant_roles.require(settings, actor, permission)
    if arguments.get('method', 'GET') != 'GET': raise HTTPException(422, 'Инспекция разработчика поддерживает только чтение')
    try: query = Query.model_validate(arguments.get('body') or {})
    except ValidationError: raise HTTPException(422, 'Неверные параметры инспекции; команды shell и изменения недоступны') from None
    try: return inspect(api, query)
    except (OSError, ValueError, TypeError, RecursionError): raise HTTPException(503, 'Исходники или метаданные установленных зависимостей недоступны') from None
