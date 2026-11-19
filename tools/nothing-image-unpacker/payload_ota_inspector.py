#!/usr/bin/env python3
"""Download and inspect a Full OTA payload using payload-dumper-go. No device writes."""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys, urllib.request
from pathlib import Path

MAX_DOWNLOAD = 8 * 1024**3
UA = "NOVA-Full-OTA-Inspector/0.1"

def download(url: str, dst: Path):
    if not url.lower().startswith("https://"):
        raise RuntimeError("OTA URL must use HTTPS.")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    h=hashlib.sha256(); total=0
    with urllib.request.urlopen(req, timeout=60) as response, dst.open("wb") as f:
        length=response.headers.get("Content-Length")
        if length and int(length)>MAX_DOWNLOAD: raise RuntimeError("OTA exceeds 8 GiB download limit.")
        while True:
            b=response.read(1024*1024)
            if not b: break
            total+=len(b)
            if total>MAX_DOWNLOAD: raise RuntimeError("OTA exceeds 8 GiB download limit.")
            h.update(b); f.write(b)
    return {"url":url,"bytes":total,"sha256":h.hexdigest()}

def run(cmd):
    p=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    print(p.stdout)
    if p.returncode: raise RuntimeError(f"Command failed (exit {p.returncode}): {' '.join(cmd)}")
    return p.stdout

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--url",required=True)
    ap.add_argument("--label",default="Galaxian-FullOTA")
    ap.add_argument("--payload-dumper",required=True)
    ap.add_argument("--output",default="out")
    args=ap.parse_args()
    label=re.sub(r"[^A-Za-z0-9._-]+","_",args.label).strip("._-")[:80] or "FullOTA"
    root=Path(args.output).resolve()/label
    root.mkdir(parents=True,exist_ok=True)
    ota=root/"full_ota.zip"
    info=download(args.url,ota)
    dumper=args.payload_dumper
    listing=run([dumper,"-l",str(ota)])
    (root/"payload-partitions-list.txt").write_text(listing,encoding="utf-8")
    # Prefer recovery-related and common Android logical partitions.
    wanted=["boot","init_boot","vendor_boot","recovery","vendor","odm","system","system_ext","product","vendor_dlkm","system_dlkm"]
    found=[]
    for name in wanted:
        if re.search(r"(?m)^\\s*"+re.escape(name)+r"\\s*(?:\\(|$)",listing):
            found.append(name)
    if not found:
        # Some tool versions print a compact comma-separated list.
        found=[n for n in wanted if re.search(r"\\b"+re.escape(n)+r"\\b",listing)]
    if not found: raise RuntimeError("Could not identify requested partitions from payload listing.")
    images=root/"partition-images"
    images.mkdir(exist_ok=True)
    run([dumper,"-o",str(images),"-p",",".join(found),str(ota)])
    inventory=[]
    for p in sorted(images.rglob("*")):
        if p.is_file():
            inventory.append({"path":str(p.relative_to(root)),"size":p.stat().st_size})
    report={"tool":"NOVA Full OTA Inspector","version":"0.1.0","label":label,"download":info,
            "partition_names_requested":found,"partition_listing":listing,"image_inventory":inventory,
            "note":"Images are extracted for offline inspection only; no device is modified."}
    (root/"summary.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\\n",encoding="utf-8")
    print(json.dumps({"output":str(root),"partitions":found,"images":len(inventory)},indent=2))
if __name__=="__main__":
    try: main()
    except Exception as e:
        print("ERROR:",e,file=sys.stderr); sys.exit(2)
