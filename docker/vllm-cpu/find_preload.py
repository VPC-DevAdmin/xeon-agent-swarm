"""
Locate libtcmalloc and libiomp5 in the container's library paths and write
their locations to /etc/vllm-preload.env so the entrypoint can set LD_PRELOAD.
"""
import glob
import re
import subprocess


def find_lib(*names: str) -> str:
    # Try ldconfig first — fastest and most reliable on Debian/Ubuntu
    try:
        out = subprocess.check_output(["ldconfig", "-p"], text=True)
        for name in names:
            m = re.search(rf"^\s+{name}\s.*=>\s+(\S+)", out, re.M)
            if m:
                return m.group(1)
    except Exception:
        pass

    # Fallback: glob common library directories
    search_dirs = [
        "/usr/lib/**",
        "/usr/local/lib/**",
        "/usr/lib/x86_64-linux-gnu",
    ]
    for name in names:
        for pattern in search_dirs:
            for path in glob.glob(f"{pattern}/{name}", recursive=True):
                return path

    return ""


tcmalloc = find_lib("libtcmalloc_minimal.so.4", "libtcmalloc_minimal.so")
iomp = find_lib("libiomp5.so")

with open("/etc/vllm-preload.env", "w") as f:
    f.write(f"TCMALLOC_PATH={tcmalloc}\n")
    f.write(f"IOMP_PATH={iomp}\n")

not_found = "(not found)"
print(f"tcmalloc: {tcmalloc if tcmalloc else not_found}")
print(f"iomp:     {iomp if iomp else not_found}")
