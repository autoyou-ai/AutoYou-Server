import sys
import shutil

def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: copy_payload_fast.py <src> <dst>")
    src = sys.argv[1]
    dst = sys.argv[2]
    print(f"Fast copying {src} -> {dst}...", flush=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print(f"Copied payload from {src} to {dst}", flush=True)

if __name__ == "__main__":
    main()
