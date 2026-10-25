"""Reproduce the pinned, self-hosted Vela browser bundle (no npm/build step)."""
import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path
from urllib.request import urlopen

VERSION = '0.8.3'
INTEGRITY = 'sha512-ohKlUFSnOO6P0kGR5w7cU2QbkIFhW/IYjEh/YXusle0qOwXmiSG3p1Sa4tmiXwLUGuCBsKGcQrKoudsGp/5Hpw=='


def main():
    destination = Path(__file__).resolve().parents[1] / 'app/dashboard/vendor' / ('vela-' + VERSION)
    with urlopen('https://registry.npmjs.org/@luxalgo/vela/-/vela-' + VERSION + '.tgz', timeout=40) as response:
        archive = response.read()
    actual = 'sha512-' + base64.b64encode(hashlib.sha512(archive).digest()).decode()
    if actual != INTEGRITY:
        raise ValueError('Published archive does not match the frozen SHA-512 integrity')
    destination.mkdir(parents=True, exist_ok=True)
    # Copy only named files; do not extract arbitrary archive paths.
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as package:
        for target, source in [('vela.global.min.js', 'package/dist/vela.global.min.js'),
                               ('LICENSE', 'package/LICENSE'), ('NOTICE', 'package/NOTICE')]:
            with package.extractfile(source) as member:
                (destination / target).write_bytes(member.read())
    manifest = {'name': '@luxalgo/vela', 'version': VERSION, 'license': 'Apache-2.0',
                'repository': 'https://github.com/LuxAlgo/Vela', 'npm_integrity': INTEGRITY,
                'bundle_sha256': hashlib.sha256((destination / 'vela.global.min.js').read_bytes()).hexdigest(),
                'source': 'Published npm package, integrity verified before copying; no Pine scripting addon bundled.'}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'version': VERSION, 'bundle_sha256': manifest['bundle_sha256']}))


if __name__ == '__main__':
    main()
