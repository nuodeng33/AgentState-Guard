"""Verify the exact audited Python reader sources inside a packaged sidecar.

PyInstaller is imported only when opening a real archive. This verifier never
executes the sidecar or extracted source and never writes into the package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from collections.abc import Callable, Mapping
from pathlib import Path

REQUIRED_READERS = (
    "agentguard/discovery/application_probe.py",
    "agentguard/discovery/session_metadata.py",
    "agentguard/discovery/probes/container_processes.py",
    "agentguard/recovery/protection_helper.py",
)
SCHEMA = "packaged-readers-1"


class PackagedReadersError(RuntimeError):
    def __init__(self, reason_code: str, reader_path: str | None = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.reader_path = reader_path


def _check_duplicate(names):
    seen = set()
    required = set(REQUIRED_READERS)
    for name in names:
        canonical = name.replace("\\", "/").casefold()
        if canonical in required:
            if canonical in seen:
                raise PackagedReadersError("PACKAGED_READER_DUPLICATE", canonical)
            seen.add(canonical)


def validate_carchive_toc(data: bytes, entry_format: str = "!IIIIBc") -> None:
    """Reject duplicate required files before CArchiveReader collapses its TOC to a dict."""
    header = struct.calcsize(entry_format)
    names = []
    cursor = 0
    try:
        while cursor < len(data):
            if cursor + header > len(data):
                raise ValueError()
            length, _offset, _size, _raw_size, _compressed, kind = struct.unpack(
                entry_format, data[cursor:cursor + header])
            if length <= header or cursor + length > len(data):
                raise ValueError()
            name = data[cursor + header:cursor + length].rstrip(b"\0").decode("utf-8")
            if kind != b"o":
                names.append(name)
            cursor += length
    except (ValueError, struct.error, TypeError) as error:
        raise PackagedReadersError("PACKAGED_READERS_ARCHIVE_INVALID") from error
    _check_duplicate(names)


def _open_archive(executable: str):
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as error:
        raise PackagedReadersError("PACKAGED_READERS_DEPENDENCY_UNAVAILABLE") from error

    class AuditedArchive(CArchiveReader):
        @classmethod
        def _parse_toc(cls, data):
            validate_carchive_toc(data, cls._TOC_ENTRY_FORMAT)
            return super()._parse_toc(data)

    return AuditedArchive(executable)


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_packaged_readers(
    executable: Path,
    source_root: Path,
    *,
    reader_factory: Callable | None = None,
) -> dict:
    executable, source_root = Path(executable), Path(source_root)
    try:
        executable = executable.resolve(strict=True)
        source_root = source_root.resolve(strict=True)
        if not executable.is_file() or executable.stat().st_size == 0 or not source_root.is_dir():
            raise OSError()
    except OSError as error:
        raise PackagedReadersError("PACKAGED_READERS_INPUT_INVALID") from error
    try:
        archive = (reader_factory or _open_archive)(str(executable))
        names = list(archive.toc.keys() if isinstance(archive.toc, Mapping) else archive.toc)
        if any(not isinstance(name, str) for name in names):
            raise ValueError()
        _check_duplicate(names)
    except PackagedReadersError:
        raise
    except Exception as error:  # External archive parser fails closed.
        raise PackagedReadersError("PACKAGED_READERS_ARCHIVE_INVALID") from error
    normalized = {name.replace("\\", "/"): name for name in names}
    verified = []
    for name in REQUIRED_READERS:
        if name not in normalized:
            raise PackagedReadersError("PACKAGED_READER_MISSING", name)
        source = source_root / name
        try:
            if source.is_symlink() or not source.resolve(strict=True).is_relative_to(source_root):
                raise OSError()
            expected = source.read_bytes()
            if not expected:
                raise OSError()
        except OSError as error:
            raise PackagedReadersError("PACKAGED_READER_SOURCE_INVALID", name) from error
        try:
            packaged = archive.extract(normalized[name])
            if not isinstance(packaged, bytes):
                raise TypeError()
        except Exception as error:  # External archive payload boundary.
            raise PackagedReadersError("PACKAGED_READER_UNREADABLE", name) from error
        expected_digest = hashlib.sha256(expected).hexdigest()
        if hashlib.sha256(packaged).hexdigest() != expected_digest:
            raise PackagedReadersError("PACKAGED_READER_HASH_MISMATCH", name)
        verified.append({"path": name, "sha256": expected_digest, "size_bytes": len(expected)})
    return {"schema_version": SCHEMA, "result": "PASS", "executable": str(executable),
            "executable_sha256": _file_sha256(executable), "source_root": str(source_root), "readers": verified}


def main(argv=None, *, reader_factory=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_packaged_readers(args.exe, args.source_root, reader_factory=reader_factory)
    except PackagedReadersError as error:
        result = {"schema_version": SCHEMA, "result": "FAIL", "reason_code": error.reason_code}
        if error.reader_path is not None:
            result["reader_path"] = error.reader_path
    except OSError:
        result = {"schema_version": SCHEMA, "result": "FAIL", "reason_code": "PACKAGED_READERS_IO_UNAVAILABLE"}
    if args.report:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        except OSError:
            result = {"schema_version": SCHEMA, "result": "FAIL", "reason_code": "PACKAGED_READERS_REPORT_UNAVAILABLE"}
    print(json.dumps(result))
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
