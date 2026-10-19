"""Build .NET Framework bridge workers for x86 and x64 vendor DLLs."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def build(root=ROOT):
    source = root/'tools/seedkey_host.cs'
    compiler = Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    for architecture in ('x86','x64'):
        output = root/f'resources/seedkey/SeedKeyHost-{architecture}.exe'
        if output.is_file() and output.stat().st_mtime >= source.stat().st_mtime:
            continue
        if not compiler.is_file():
            raise RuntimeError('构建 SeedKey 辅助程序需要 Windows .NET Framework C# 编译器')
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(compiler),'/nologo','/target:exe','/optimize+',f'/platform:{architecture}',
                        f'/out:{output}',str(source)],check=True,creationflags=0x08000000)


if __name__ == '__main__':
    build()
