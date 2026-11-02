#!/usr/bin/env python3
"""Probe the FCC public-files (OPIF) API from a political-file folder URL.

Works backwards from a folder page such as
  https://publicfiles.fcc.gov/tv-profile/WWLP/political-files/2026/local/
    springfield-school-committee/rosa-valentin/dd5a8122-6e9f-55cf-3d75-23d2d565b910
and reports, step by step:
  1. what the FCC's published API spec lists (so we use documented endpoints)
  2. the station's facility record (entity ID)
  3. the folder's contents: each file's name, upload date, and IDs
  4. whether the newest file's PDF link downloads with a plain, identified
     request, and what comes back (status, content type, size, %PDF header)

It makes a handful of requests, one second apart, with a User-Agent that says
who we are. It does not retry blocked requests or try to look like a browser.
A 403 on the download is a finding to report, not something to work around.

Usage (Python 3.9+, standard library only):
  python scripts/opif_probe.py URL --contact you@example.com [--out opif-probe]

Everything fetched is saved under --out so we can design the parser from it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

BASE = "https://publicfiles.fcc.gov"
SPEC_URL = f"{BASE}/api/manager/json/apis.json"
PAUSE_S = 1.0

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def parse_profile_url(url: str) -> dict:
    """Split a public-files folder URL into service, call sign, path, and folder ID."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) < 3 or not parts[0].endswith("-profile"):
        raise ValueError("expected a URL like https://publicfiles.fcc.gov/tv-profile/CALL/political-files/...")
    folder_id = parts[-1] if UUID_RE.match(parts[-1]) else None
    return {
        "service": parts[0].removesuffix("-profile"),  # tv, fm, am, cable, ...
        "call_sign": parts[1].upper(),
        "path": parts[2:-1] if folder_id else parts[2:],
        "folder_id": folder_id,
    }


class Client:
    def __init__(self, contact: str, out: Path):
        self.ua = f"win-grp-opif-probe/0.1 (research; contact {contact})"
        self.out = out
        self.log: list[dict] = []

    def get(self, url: str, save_as: str | None = None, method: str = "GET") -> tuple[int, dict, bytes]:
        time.sleep(PAUSE_S)
        req = urllib.request.Request(url, method=method, headers={"User-Agent": self.ua, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, headers, body = r.status, dict(r.headers), r.read() if method == "GET" else b""
        except urllib.error.HTTPError as e:
            status, headers, body = e.code, dict(e.headers or {}), e.read() or b""
        except urllib.error.URLError as e:
            status, headers, body = 0, {}, str(e.reason).encode()
        entry = {
            "url": url, "method": method, "status": status,
            "content_type": headers.get("Content-Type", ""),
            "bytes": len(body),
            "server": headers.get("Server", ""),
            "akamai_ref": "Reference #" in body[:4000].decode("latin-1", "replace"),
        }
        if save_as and body:
            (self.out / save_as).write_bytes(body)
            entry["saved"] = save_as
        self.log.append(entry)
        print(f"  {status or 'ERR'} {method} {url} [{entry['content_type'] or '-'}; {len(body)} bytes]")
        return status, headers, body


def as_json(body: bytes):
    try:
        return json.loads(body)
    except ValueError:
        return None


def find_dicts(obj, pred):
    """Yield every dict anywhere inside obj for which pred(d) is true."""
    if isinstance(obj, dict):
        if pred(obj):
            yield obj
        for v in obj.values():
            yield from find_dicts(v, pred)
    elif isinstance(obj, list):
        for v in obj:
            yield from find_dicts(v, pred)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--contact", required=True, help="email to put in the User-Agent")
    ap.add_argument("--out", type=Path, default=Path("opif-probe"))
    args = ap.parse_args()

    target = parse_profile_url(args.url)
    args.out.mkdir(parents=True, exist_ok=True)
    c = Client(args.contact, args.out)
    report: dict = {"target": target}
    print(f"Target: {target}")

    print("\n1. Published API spec")
    st, _, body = c.get(SPEC_URL, "apis.json")
    spec = as_json(body) if st == 200 else None
    paths = sorted((spec or {}).get("paths", {}).keys()) if isinstance(spec, dict) else []
    report["spec_paths"] = paths
    for p in paths:
        print(f"     {p}")

    print("\n2. Facility record")
    facility_urls = [
        f"{BASE}/api/service/facility/search/{target['call_sign']}.json",
        f"{BASE}/api/service/{target['service']}/facility/search/{target['call_sign']}.json",
    ]
    entity_id = None
    for u in facility_urls:
        st, _, body = c.get(u, "facility.json")
        data = as_json(body) if st == 200 else None
        if data:
            hits = list(find_dicts(data, lambda d: str(d.get("callSign", "")).upper().startswith(target["call_sign"])))
            if hits:
                entity_id = hits[0].get("id") or hits[0].get("entityId") or hits[0].get("facilityId")
                report["facility"] = hits[0]
                print(f"     entity ID: {entity_id}")
                break
    report["entity_id"] = entity_id

    print("\n3. Folder contents")
    if not target["folder_id"]:
        print("     URL has no folder ID; stopping.")
        return finish(c, report, args.out)
    folder_urls = [f"{BASE}/api/manager/folder/id/{target['folder_id']}.json"]
    if entity_id:
        folder_urls.append(f"{BASE}/api/manager/folder/parentFolders/{entity_id}/{target['folder_id']}.json")
    files = []
    for u in folder_urls:
        st, _, body = c.get(u, "folder.json")
        data = as_json(body) if st == 200 else None
        if data:
            files = list(find_dicts(data, lambda d: any(k in d for k in ("file_id", "fileId", "file_manager_id", "fileManagerId"))))
            if files:
                break
    report["files"] = files
    for f in files:
        name = f.get("file_name") or f.get("fileName") or f.get("name")
        when = f.get("create_ts") or f.get("createTs") or f.get("last_update_ts") or f.get("uploadDate")
        print(f"     {when}  {name}")
    if not files:
        print("     No files found in the folder response. Check folder.json by hand.")
        return finish(c, report, args.out)

    print("\n4. Newest file's PDF")
    def ts(f):
        return str(f.get("create_ts") or f.get("createTs") or f.get("last_update_ts") or "")
    newest = max(files, key=ts)
    report["newest"] = newest
    links = [v for v in newest.values() if isinstance(v, str) and v.startswith("http")]
    fid = newest.get("file_manager_id") or newest.get("fileManagerId") or newest.get("file_id") or newest.get("fileId")
    if fid:
        links.append(f"{BASE}/api/manager/download/{target['folder_id']}/{fid}.pdf")
    tried = set()
    for u in links:
        if u in tried:
            continue
        tried.add(u)
        st, headers, body = c.get(u, "newest.pdf")
        ok = st == 200 and body[:4] == b"%PDF"
        report.setdefault("download_attempts", []).append({"url": u, "status": st, "is_pdf": ok})
        if ok:
            print(f"     Downloaded a real PDF ({len(body)} bytes) -> {args.out / 'newest.pdf'}")
            break
        if st == 403:
            print("     403: blocked. Not retrying; this is the finding.")
            break
    return finish(c, report, args.out)


def finish(c: Client, report: dict, out: Path) -> int:
    report["requests"] = c.log
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nSaved everything to {out}/ (send report.json, facility.json, and folder.json back).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
