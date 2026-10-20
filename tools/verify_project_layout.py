"""离线检查目录整理结果；不导入主程序，不加载硬件 DLL，不连接设备。"""
from pathlib import Path
import argparse
import ast
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ('core', 'devices', 'data', 'ui', 'tools', 'tests')
SKIP = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.vs', '.idea',
        'build', 'dist', 'logs', 'test_logs', 'reports', 'node_modules'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    manifest = json.loads((ROOT / 'doc/organization/migration_manifest.json').read_text(encoding='utf-8'))
    errors = []
    changed = manifest.get('updated_files', {})
    for item in manifest['files']:
        if item['action'] == 'cleanup':
            path = Path(item['backup'])
            if not path.is_file() or digest(path) != item['sha256']:
                errors.append('清理项备份缺失或不同：' + item['path'])
            if item.get('duplicate_of'):
                other = ROOT / item['duplicate_of']
                if not other.is_file() or digest(other) != item['sha256']:
                    errors.append('去重后的保留文件不同：' + item['duplicate_of'])
        else:
            path = ROOT / item['destination']
            expected = changed.get(item['destination'], {}).get('sha256', item['sha256'])
            if not path.is_file() or digest(path) != expected:
                errors.append('保留/迁移文件缺失或内容发生变化：' + item['destination'])

    syntax_count = 0
    for directory in SOURCE_DIRS:
        for path in (ROOT / directory).rglob('*.py'):
            try:
                ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
                syntax_count += 1
            except SyntaxError as exc:
                errors.append(str(exc))
    for name in ('main.py', 'launch_app.py'):
        ast.parse((ROOT / name).read_text(encoding='utf-8-sig'), filename=name)
        syntax_count += 1

    resource_count = 0
    for path in (ROOT / 'packaging').glob('*.spec'):
        class Bundle:
            pure = scripts = binaries = datas = []

        def analysis(scripts, **kwargs):
            nonlocal resource_count
            for source in scripts + [row[0] for row in kwargs.get('binaries', []) + kwargs.get('datas', [])]:
                resource_count += 1
                if not Path(source).exists():
                    errors.append('打包资源不存在：' + str(source))
            return Bundle()

        def executable(*args, **kwargs):
            nonlocal resource_count
            if kwargs.get('icon'):
                resource_count += 1
                if not Path(kwargs['icon']).is_file():
                    errors.append('打包图标不存在：' + str(kwargs['icon']))
            return Bundle()

        context = dict(SPECPATH=str(path.parent), Analysis=analysis, PYZ=lambda *a, **k: Bundle(),
                       EXE=executable, COLLECT=lambda *a, **k: Bundle())
        exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), context)

    required = ['vendor/PCAN-Basic/x64/PCANBasic.dll',
                'vendor/zlgcan(20260806)/zlgcan_x64/zlgcan.dll',
                'vendor/zlgcan_python_260529/zlgcan.py',
                'vendor/力捷峰U盘/Demo/OpteeQDLLx64.dll',
                'vendor/力捷峰U盘/Demo/libcrypto-3.dll', 'config/故障码.txt']
    for name in required:
        if not (ROOT / name).is_file():
            errors.append('运行资源缺失：' + name)

    files = [p for p in ROOT.rglob('*') if p.is_file() and not any(part in SKIP for part in p.relative_to(ROOT).parts)]
    recipe_names = [item for item in manifest['files'] if item['path'].startswith('recipes/')]
    return {'ok': not errors, 'original_files_verified': len(manifest['files']),
            'cleanup_files': sum(x['action'] == 'cleanup' for x in manifest['files']),
            'moved_files': sum(x['action'] == 'move' for x in manifest['files']),
            'python_files_syntax_checked': syntax_count, 'spec_resource_paths_checked': resource_count,
            'original_recipe_files_unchanged': all(digest(ROOT / x['destination']) == x['sha256'] for x in recipe_names),
            'project_bytes_excluding_git_and_generated': sum(p.stat().st_size for p in files),
            'errors': errors,
            'scope': '离线文件/哈希/语法/打包路径检查，不代表硬件或已打包EXE运行验收。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='可选：保存本次检查 JSON，不影响已有测试记录')
    args = parser.parse_args()
    result = verify()
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload + '\n', encoding='utf-8')
    print(payload)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
