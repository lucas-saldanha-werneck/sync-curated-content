import pytest
from lib.privs import drop_to


def test_drop_to_missing_user_is_noop_unless_strict():
    # Mac/testes: usuário não existe → não troca (False); em produção (strict) a ausência é erro alto, nunca silêncio
    assert drop_to("sr-no-such-user-xyz") is False
    with pytest.raises(RuntimeError):
        drop_to("sr-no-such-user-xyz", strict=True)
