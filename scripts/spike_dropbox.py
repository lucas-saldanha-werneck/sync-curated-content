"""Spike: prova que WriteMode.update(rev) errado da conflito e add em existente da conflito.

Adaptacao do brief original (Task 0): nao existe um app Dropbox dedicado nesta VPS
(sem DROPBOX_REFRESH_TOKEN/APP_KEY/APP_SECRET). O token vem do rclone.conf usado pelo
remote "dropbox" (secao [dropbox], chave "token" = JSON com access_token/refresh_token/
expiry). Se o token estiver perto de expirar, chamamos o rclone (que sabe renovar e
reescrever o arquivo) antes de ler de novo. Depois disso usamos
dropbox.Dropbox(oauth2_access_token=...) com o access_token puro (SDK 12.1.0,
assinatura verificada em runtime: oauth2_access_token e' kwarg valido).

So imprime OK/FAIL e, no maximo, tamanho/prefixo do token -- nunca o valor completo.
"""
import configparser
import datetime
import json
import re
import subprocess
import sys

import dropbox
from dropbox.files import WriteMode

RCLONE_CONF = "/opt/sync-raindrop/rclone.conf"
DROPBOX_SECTION = "dropbox"


def _parse_iso(s: str) -> datetime.datetime:
    # rclone grava expiry com precisao de nanossegundos (9 casas decimais); o
    # datetime.fromisoformat do Python 3.10 so aceita 0, 3 ou 6 casas. Trunca p/ 6.
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    return datetime.datetime.fromisoformat(s)


def _read_token() -> dict:
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(RCLONE_CONF)
    raw = cp.get(DROPBOX_SECTION, "token")
    return json.loads(raw)


def get_access_token() -> str:
    token = _read_token()
    expiry = _parse_iso(token["expiry"])
    now = datetime.datetime.now(expiry.tzinfo)
    if expiry - now < datetime.timedelta(minutes=5):
        # rclone renova e reescreve o rclone.conf sozinho em qualquer chamada.
        subprocess.run(
            ["rclone", "--config", RCLONE_CONF, "lsd", "dropbox:"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        token = _read_token()
    return token["access_token"]


access_token = get_access_token()
print(f"OK token: len={len(access_token)} prefix={access_token[:6]}")

dbx = dropbox.Dropbox(oauth2_access_token=access_token)

p = "/Projects/vault-sandbox/_spike/cas.txt"
m1 = dbx.files_upload(b"v1", p, mode=WriteMode.overwrite)          # so o spike usa overwrite, em pasta _spike
m2 = dbx.files_upload(b"v2", p, mode=WriteMode.update(m1.rev), strict_conflict=True)
try:
    dbx.files_upload(b"v3", p, mode=WriteMode.update(m1.rev), strict_conflict=True)   # rev velho
    print("FAIL cas: sobrescreveu com rev velho"); sys.exit(1)
except dropbox.exceptions.ApiError as e:
    assert e.error.is_path() and e.error.get_path().reason.is_conflict(), e
    print("OK cas: rev velho -> conflict")
try:
    dbx.files_upload(b"outro", p, mode=WriteMode.add, strict_conflict=True)
    print("FAIL add: sobrescreveu existente"); sys.exit(1)
except dropbox.exceptions.ApiError as e:
    assert e.error.get_path().reason.is_conflict(), e
    print("OK add: existente -> conflict")
got, res = dbx.files_download(p); assert res.content == b"v2" and got.rev == m2.rev
print("OK download rev bate")
