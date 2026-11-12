#!/usr/bin/env python3
"""rclone_only_remote.py SRC DST [REMOTE] — grava em DST um rclone.conf só com o remote REMOTE (padrão: dropbox).

O container só usa esse remote. Antes, o deploy copiava o rclone.conf inteiro do root (todos os remotes).
Se DST já existe, ele vale mais que SRC (o rclone do container renova o token e regrava DST): só tira os
outros remotes dele. Escrita atômica (temporário na mesma pasta + rename), modo 600.
"""
import os, re, sys, tempfile


def sections(text: str) -> dict:
    out, name, buf = {}, None, []
    for line in text.splitlines(keepends=True):
        m = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if m:
            if name is not None:
                out[name] = "".join(buf)
            name, buf = m.group(1).strip(), [line]
        elif name is not None:
            buf.append(line)
    if name is not None:
        out[name] = "".join(buf)
    return out


def main(src: str, dst: str, remote: str = "dropbox") -> int:
    base = dst if os.path.exists(dst) else src
    with open(base, encoding="utf-8") as f:
        secs = sections(f.read())
    if remote not in secs:
        print(f"rclone: remote [{remote}] não está em {base}", file=sys.stderr)
        return 1
    if base == dst and list(secs) == [remote]:
        print(f"rclone: {dst} já só tem [{remote}]")
        return 0
    body = secs[remote].rstrip("\n") + "\n"
    d = os.path.dirname(os.path.abspath(dst))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".rclone.conf.")
    try:
        os.fchmod(fd, 0o600)                 # pelo descritor: nunca segue um symlink plantado no lugar do temporário
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, dst)
    except BaseException:
        os.unlink(tmp)
        raise
    print(f"rclone: {dst} agora só tem [{remote}] (tirei: {', '.join(n for n in secs if n != remote) or 'nada'})")
    return 0


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
