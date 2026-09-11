"""Queda de privilégio para subprocessos (app-claude, app-fetch).

O orquestrador roda como `app` (uid 10001) com CAP_SETUID/CAP_SETGID ambientes (entrypoint via `setpriv`).
No filho (preexec_fn): troca uid/gid e ZERA todas as capabilities (ambient + permitted/effective/inheritable) —
senão o filho herdaria setuid/setgid e poderia voltar para `app`. Depois liga no_new_privs.
Falha alta: se o usuário existe (produção) e a troca falhar, levanta — controle de segurança nunca falha calado.
No Mac/testes o usuário não existe → no-op (devolve False), a menos que strict=True.
"""
from __future__ import annotations
import ctypes, os, pwd

PR_SET_NO_NEW_PRIVS = 38
PR_CAP_AMBIENT = 47
PR_CAP_AMBIENT_CLEAR_ALL = 4
_LINUX_CAPABILITY_VERSION_3 = 0x20080522


class _CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapData(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]


def _clear_all_caps() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)
    hdr = _CapHeader(_LINUX_CAPABILITY_VERSION_3, 0); data = (_CapData * 2)()
    if libc.capset(ctypes.byref(hdr), data) != 0:
        e = ctypes.get_errno(); raise OSError(e, f"capset: {os.strerror(e)}")
    libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)


def drop_to(user: str, strict: bool | None = None) -> bool:
    """Troca o processo atual para `user` e zera capabilities. strict=None → estrito se o usuário existir."""
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        if strict: raise RuntimeError(f"usuário {user!r} não existe: sandbox de uid indisponível")
        return False
    os.setgroups([]); os.setgid(pw.pw_gid); os.setuid(pw.pw_uid)          # PermissionError propaga (falha alta)
    if os.getuid() != pw.pw_uid or os.getgid() != pw.pw_gid:
        raise RuntimeError(f"troca para {user} não pegou (uid={os.getuid()})")
    if os.name == "posix" and os.uname().sysname == "Linux":
        _clear_all_caps()
    return True


def caps_of_self() -> dict:
    out = {}
    try:
        for line in open("/proc/self/status"):
            if line.startswith(("CapPrm", "CapEff", "CapAmb", "CapInh")): k, v = line.split(":"); out[k] = v.strip()
    except FileNotFoundError:
        pass
    return out
