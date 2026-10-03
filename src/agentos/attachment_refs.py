"""Attachment material references shared across gateway, transcript, and runtime."""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path
from typing import Any

ATTACHMENT_REF_KIND = "attachment_ref"
TRANSCRIPT_MATERIAL_STORE = "transcript"


class AttachmentMaterialBudgetError(ValueError):
    """Raised when material bytes cannot be written within the configured budget."""


#: How much of a suffix is still a suffix. Past this the dot is part of the
#: name, not an extension, and keeping it would spend the whole budget on it.
MAX_FILENAME_SUFFIX = 16


def truncate_filename(name: str, limit: int) -> str:
    """Shorten *name* to *limit* characters, keeping its extension.

    A plain ``name[:limit]`` cuts from the left, so the extension is the
    first thing lost: a 162-character ``...pdf`` came back ``....p`` and a
    200-character one came back with no suffix at all. The bytes are fine
    and the name is not -- the OS picks the application from the suffix, and
    ``gateway.attachments`` hands this straight to ``Content-Disposition``,
    so the file would not open by double-click (#3569).

    The stem is trimmed instead and the suffix kept whole. A "suffix" longer
    than :data:`MAX_FILENAME_SUFFIX`, or one containing a space, is not an
    extension -- it is a name with a dot in it -- and gets no protection, so
    a pathological 300-character tail cannot eat the budget.
    """
    if limit <= 0:
        return ""
    if len(name) <= limit:
        return name
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem or len(suffix) > MAX_FILENAME_SUFFIX or " " in suffix:
        return name[:limit]
    tail = f".{suffix}"
    if len(tail) >= limit:
        return name[:limit]
    return stem[: limit - len(tail)] + tail


def is_attachment_ref(attachment: Any) -> bool:
    return isinstance(attachment, dict) and attachment.get("kind") == ATTACHMENT_REF_KIND


def transcript_material_dir(media_root: Path, session_id: str) -> Path:
    return Path(media_root) / "transcripts" / session_id


def _validate_sha256(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("attachment ref sha256 is invalid")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("attachment ref sha256 is invalid") from exc
    return value.lower()


def transcript_material_path(media_root: Path, session_id: str, sha256: str) -> Path:
    sha = _validate_sha256(sha256)
    return transcript_material_dir(media_root, session_id) / sha


def _media_disk_usage_bytes(media_root: Path) -> int:
    root = Path(media_root) / "transcripts"
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{secrets.token_hex(4)}")
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_transcript_material(
    *,
    media_root: Path,
    session_id: str,
    payload: bytes,
    disk_budget_bytes: int | None = None,
) -> tuple[str, Path, bool]:
    """Write payload into the transcript material store and return ``(sha, path, wrote)``."""

    sha = hashlib.sha256(payload).hexdigest()
    path = transcript_material_path(media_root, session_id, sha)
    if path.exists():
        return sha, path, False

    if disk_budget_bytes is not None:
        current = _media_disk_usage_bytes(media_root)
        if current + len(payload) > disk_budget_bytes:
            raise AttachmentMaterialBudgetError(
                "attachment material exceeds transcript disk budget "
                f"({current} + {len(payload)} > {disk_budget_bytes})"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(path, payload)
    return sha, path, True


def make_attachment_ref(
    *,
    sha256: str,
    name: str,
    mime: str,
    size: int,
    session_id: str,
    source: str,
) -> dict[str, Any]:
    sha = _validate_sha256(sha256)
    return {
        "kind": ATTACHMENT_REF_KIND,
        "type": mime,
        "mime": mime,
        "name": name,
        "size": size,
        "sha256": sha,
        "material_id": sha,
        "store": TRANSCRIPT_MATERIAL_STORE,
        "scope": session_id,
        "source": source,
        "_was_staged": True,
    }


def attachment_ref_marker(
    ref: dict[str, Any],
    *,
    prefix: str = "historical attachment omitted",
) -> str:
    mime = ref.get("mime") or ref.get("type") or "attachment"
    name = ref.get("name") if isinstance(ref.get("name"), str) else "attachment"
    return f"[{prefix}: {name} ({mime})]"


def read_attachment_ref_bytes(ref: dict[str, Any], *, media_root: Path) -> bytes:
    if not is_attachment_ref(ref):
        raise ValueError("attachment is not a material ref")
    store = ref.get("store")
    if store != TRANSCRIPT_MATERIAL_STORE:
        raise ValueError(f"unsupported attachment material store {store!r}")
    scope = ref.get("scope")
    if not isinstance(scope, str) or not scope:
        raise ValueError("attachment ref scope is required")
    sha = _validate_sha256(ref.get("sha256") or ref.get("material_id"))
    path = transcript_material_path(media_root, scope, sha)
    payload = path.read_bytes()
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != sha:
        raise ValueError("attachment material hash mismatch")
    size = ref.get("size")
    if isinstance(size, int) and size >= 0 and len(payload) != size:
        raise ValueError("attachment material size mismatch")
    return payload
