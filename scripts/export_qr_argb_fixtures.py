"""Export exact browser PNG pixels for Android JVM tests without Java desktop APIs.

Offline authoring utility only: bundled Python/Pillow; no product dependency.
Never rescales, pads, thresholds, enhances, or rewrites a PNG.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct
from PIL import Image

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('resources', type=Path)
    args = parser.parse_args()
    root = args.resources.resolve(strict=True)
    provenance = json.loads((root / 'provenance.json').read_text())
    manifest = {'format': 'ASG1: big-endian magic,width,height then row-major uint32 ARGB; gzip mtime0', 'files': {}}
    for name, expected_sha in sorted(provenance['png_sha256'].items()):
        png = root / name
        source = png.read_bytes()
        assert hashlib.sha256(source).hexdigest() == expected_sha
        with Image.open(png) as image:
            assert image.mode in {'RGB', 'RGBA'}
            width, height = image.size
            assert 0 < width <= 2048 and 0 < height <= 2048
            assert width * height <= 4_194_304
            values = bytearray()
            for channels in image.getdata():
                red, green, blue = channels[:3]
                alpha = channels[3] if image.mode == 'RGBA' else 255
                values.extend(struct.pack('>I', (alpha << 24) | (red << 16) | (green << 8) | blue))
        stream = struct.pack('>III', 0x41534731, width, height) + values
        compressed = gzip.compress(stream, mtime=0)
        target = root / (png.stem + '.argb.gz')
        with target.open('xb') as output:
            output.write(compressed)
        manifest['files'][name] = {'width': width, 'height': height, 'png_sha256': expected_sha,
            'pixel_argb_sha256': hashlib.sha256(values).hexdigest(),
            'raw_stream_sha256': hashlib.sha256(stream).hexdigest(),
            'gzip_sha256': hashlib.sha256(compressed).hexdigest()}
    with (root / 'argb-manifest.json').open('x', encoding='utf-8', newline='\n') as output:
        json.dump(manifest, output, indent=2)
    print(json.dumps({'files': len(manifest['files']), 'original_pngs_unchanged': True}))

if __name__ == '__main__':
    main()
