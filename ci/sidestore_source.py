import argparse
import base64
import datetime
import json
import plistlib
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import quote


def make_source(ipa, repo, build):
    with zipfile.ZipFile(ipa) as package:
        paths = [name for name in package.namelist() if name.startswith('Payload/') and name.endswith('.app/Info.plist') and name.count('/') == 2]
        if len(paths) != 1:
            raise ValueError('IPA must contain exactly one top-level app')
        info = plistlib.loads(package.read(paths[0]))
    if str(info['CFBundleVersion']) != build:
        raise ValueError('IPA build number does not match release')
    icon = 'https://raw.githubusercontent.com/%s/standard/Telegram/Watch/App/Assets.xcassets/AppIcon.appiconset/Swiftgram.png' % repo
    url = 'https://github.com/%s/releases/download/build-%s/%s' % (repo, build, quote(ipa.name))
    entry = {'version': info['CFBundleShortVersionString'], 'buildVersion': build,
             'date': datetime.datetime.now(datetime.timezone.utc).date().isoformat(),
             'localizedDescription': 'AyugramXi build ' + build, 'downloadURL': url,
             'size': ipa.stat().st_size, 'sha256': sha256(ipa),
             'minOSVersion': info.get('MinimumOSVersion', '15.0')}
    return {'name': 'AyugramXi', 'identifier': 'io.github.xitaoyou.ayugramxi.source', 'iconURL': icon,
            'apps': [{'name': 'AyugramXi', 'bundleIdentifier': info['CFBundleIdentifier'],
                      'developerName': 'Xitaoyou and AyuGram contributors', 'localizedDescription': 'AyugramXi for iOS',
                      'iconURL': icon, 'versions': [entry]}], 'news': []}


def sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def publish(repo, source):
    endpoint = 'repos/%s/contents/sidestore-source.json' % repo
    content = base64.b64encode((json.dumps(source, ensure_ascii=False, indent=2) + '\n').encode()).decode()
    new_build = int(source['apps'][0]['versions'][0]['buildVersion'])
    for _ in range(4):
        get = subprocess.run(['gh', 'api', endpoint + '?ref=standard'], capture_output=True, text=True)
        payload = {'message': 'Update AyugramXi SideStore source', 'branch': 'standard', 'content': content}
        if get.returncode == 0:
            previous = json.loads(get.stdout)
            old = json.loads(base64.b64decode(previous['content']))
            old_build = int(old['apps'][0]['versions'][0]['buildVersion'])
            if old_build > new_build:
                print('A newer build is already in the source; leaving it unchanged.')
                return
            payload['sha'] = previous['sha']
        elif '404' not in get.stderr:
            raise RuntimeError('Unable to read the existing source: ' + get.stderr)
        put = subprocess.run(['gh', 'api', '--method', 'PUT', endpoint, '--input', '-'], input=json.dumps(payload), capture_output=True, text=True)
        if put.returncode == 0:
            print('SideStore source updated for build', new_build)
            return
        if '409' not in put.stderr and '422' not in put.stderr:
            raise RuntimeError('Unable to update source: ' + put.stderr)
    raise RuntimeError('Source changed concurrently; retry the workflow')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('ipa', type=Path)
    parser.add_argument('repository')
    parser.add_argument('build')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if not args.build.isdigit() or args.ipa.name != 'AyuGram-standard-%s.ipa' % args.build:
        raise ValueError('Expected the matching standard build IPA')
    source = make_source(args.ipa, args.repository, args.build)
    if args.publish:
        publish(args.repository, source)
    else:
        print(json.dumps(source, indent=2))


if __name__ == '__main__':
    main()
