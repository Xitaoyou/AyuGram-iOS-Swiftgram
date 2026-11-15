"""Compile and run production Swift scheduling helpers without an iOS build.

Requires a Swift toolchain on macOS. This uses the real RangeSet module,
MetaDisposable, scheduling helpers and XCTest files, not Python replicas.
"""
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def declaration(source, marker):
    start = source.index(marker)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def main():
    network = ROOT / 'submodules/TelegramCore/Sources/Network'
    fetch = (network / 'FetchV2.swift').read_text(encoding='utf-8')
    manager = (network / 'MultiplexedRequestManager.swift').read_text(encoding='utf-8')
    disposables = (ROOT / 'submodules/SSignalKit/SwiftSignalKit/Source/Disposable.swift').read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory)
        subprocess.run([
            'swiftc', '-swift-version', '5', '-emit-module', '-emit-library',
            '-module-name', 'RangeSet',
            *map(str, sorted((ROOT / 'submodules/Utils/RangeSet/Sources').glob('*.swift'))),
            '-o', str(output / 'libRangeSet.dylib'),
            '-emit-module-path', str(output / 'RangeSet.swiftmodule'),
        ], check=True)
        helpers = output / 'Helpers.swift'
        helpers.write_text('\n'.join([
            'import Foundation\nimport RangeSet',
            fetch[fetch.index('private let possiblePartLengths:'):fetch.index('// The resources this process')],
            declaration(manager, 'struct DownloadRequestBudget'),
            declaration(disposables, 'public protocol Disposable'),
            declaration(disposables, 'public final class MetaDisposable'),
        ]), encoding='utf-8')
        sources = [str(helpers)]
        suites = []
        for name in ['DownloadRequestBudgetTests', 'FetchV2PrioritySelectionTests']:
            source = (ROOT / f'submodules/TelegramCore/Tests/{name}.swift').read_text(encoding='utf-8')
            source = source.replace('@testable import TelegramCore', '')
            destination = output / f'{name}.swift'
            destination.write_text(source, encoding='utf-8')
            sources.append(str(destination))
            methods = re.findall(r'    func (test\w+)\(', source)
            suites.append('testCase([' + ', '.join(f'("{method}", {name}.{method})' for method in methods) + '])')
        runner = output / 'main.swift'
        runner.write_text('''import XCTest
final class Probe: Disposable {
    var cancelled = false
    func dispose() { self.cancelled = true }
}
final class PreviewHolderTests: XCTestCase {
    func testLeaveAndReturnKeepsHolderReusable() {
        let holder = MetaDisposable()
        let first = Probe()
        holder.set(first)
        holder.set(nil)
        XCTAssertTrue(first.cancelled)
        let second = Probe()
        holder.set(second)
        XCTAssertFalse(second.cancelled)
        holder.dispose()
        XCTAssertTrue(second.cancelled)
        let third = Probe()
        holder.set(third)
        XCTAssertTrue(third.cancelled)
    }
}
XCTMain([''' + ', '.join(suites) + ''', testCase([
    ("testLeaveAndReturnKeepsHolderReusable", PreviewHolderTests.testLeaveAndReturnKeepsHolderReusable)
])])
''', encoding='utf-8')
        binary = output / 'DownloadSchedulingTests'
        subprocess.run([
            'swiftc', '-swift-version', '5', '-warnings-as-errors',
            '-I', str(output), '-L', str(output), '-lRangeSet',
            '-Xlinker', '-rpath', '-Xlinker', str(output),
            *sources, str(runner), '-o', str(binary),
        ], check=True)
        subprocess.run([str(binary)], check=True)
        # Parse complete changed implementation files too. This is syntax
        # validation; only the iOS build can typecheck UIKit/engine wiring.
        for path in [network / 'MultiplexedRequestManager.swift', network / 'FetchV2.swift',
                     ROOT / 'submodules/GalleryUI/Sources/Items/UniversalVideoGalleryItem.swift']:
            subprocess.run(['swiftc', '-frontend', '-parse', str(path)], check=True)


if __name__ == '__main__':
    main()
