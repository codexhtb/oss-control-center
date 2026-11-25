"""Synthetic only: no source passwords, replicas or real credentials in fixtures."""

from types import SimpleNamespace

import pytest

from app.modules.billing.netup.cabinet import CabinetUnavailable, load_cabinet, password_enabled


class Cursor:
    def __init__(self, *, owners=None, encrypted=0, tls="TLS-test", server_id=2, read_only=1):
        self.owners = [{"id": 101}] if owners is None else owners
        self.encrypted, self.tls = encrypted, tls
        self.sql = []
        self.server_id, self.read_only = server_id, read_only

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchall(self):
        return self.owners

    def fetchone(self):
        if self.sql[-1][0].startswith("SHOW"):
            return {"Value": self.tls}
        if self.sql[-1][0].startswith("SELECT @@"):
            return {"server_id": self.server_id, "read_only": self.read_only}
        return {"login": "synthetic-login", "password": "synthetic-secret",
                "encrypted": self.encrypted}


class Connection:
    def __init__(self, cursor):
        self.q, self.closed, self.rolled_back = cursor, False, False

    def cursor(self):
        return self.q

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def config(q):
    c = Connection(q)
    return SimpleNamespace(connect=lambda: c, tls_ca="test-ca", max_execution_ms=3000), c


def test_login_does_not_select_secret_and_extra_account_resolves_user():
    q = Cursor()
    cfg, connection = config(q)
    result = load_cabinet(cfg, 303, 101)
    assert result.login == "synthetic-login" and result.password is None
    assert all("password" not in sql.lower() for sql, _ in q.sql)
    assert q.sql[-2][1] == (303,) and q.sql[-1][1] == (101,)
    assert any("READ ONLY" in sql for sql, _ in q.sql)
    assert connection.closed and connection.rolled_back


@pytest.mark.parametrize("owners", [[], [{"id": 102}], [{"id": 101}, {"id": 102}]])
def test_deleted_missing_changed_or_ambiguous_owner_never_reads_password(owners):
    q = Cursor(owners=owners)
    cfg, connection = config(q)
    with pytest.raises(CabinetUnavailable):
        load_cabinet(cfg, 202, 101, password=True)
    assert all("SELECT login,password" not in sql for sql, _ in q.sql)
    assert connection.closed and connection.rolled_back


@pytest.mark.parametrize("tls,encrypted", [("", 0), ("TLS-test", 1), ("TLS-test", 7)])
def test_no_tls_or_unknown_storage_never_delivers_a_secret(tls, encrypted):
    cfg, connection = config(Cursor(tls=tls, encrypted=encrypted))
    with pytest.raises(CabinetUnavailable):
        load_cabinet(cfg, 202, 101, password=True)
    assert connection.closed and connection.rolled_back


def test_secret_is_masked_and_not_part_of_billing_provider():
    cfg, _ = config(Cursor())
    result = load_cabinet(cfg, 202, 101, password=True)
    assert result.password.get_secret_value() == "synthetic-secret"
    assert "synthetic-secret" not in repr(result)
    assert "synthetic-secret" not in repr(result.password)


@pytest.mark.parametrize("server_id,read_only,allowed", [
    (2, 1, True), (1, 1, False), (2, 0, False),
])
def test_management_password_requires_approved_read_only_replica(server_id, read_only, allowed):
    q = Cursor(tls="", server_id=server_id, read_only=read_only)
    cfg, connection = config(q)
    cfg.allow_plaintext_management = True
    if allowed:
        assert load_cabinet(cfg, 202, 101, password=True, require_tls=False).password is not None
    else:
        with pytest.raises(CabinetUnavailable):
            load_cabinet(cfg, 202, 101, password=True, require_tls=False)
        assert not any("SELECT login,password" in sql for sql, _ in q.sql)
    assert not any(sql.startswith("SHOW") for sql, _ in q.sql)
    assert connection.closed and connection.rolled_back


@pytest.mark.parametrize("host,port,approved", [
    ("172.17.7.1", 3307, True), ("172.17.7.1", 3306, False), ("172.17.7.70", 3307, False),
])
def test_management_exception_is_restricted_to_existing_endpoint(host, port, approved):
    from app.modules.billing.netup.config import NetupConfig

    values = dict(host=host, port=port, app_env="production", purpose="production",
                  user="synthetic_ro", password="synthetic-secret", allow_plaintext_management=True)
    if approved:
        cfg = NetupConfig(**values)
        assert password_enabled(SimpleNamespace(subscriber_cabinet_mode="management"), cfg)
        assert cfg.tls_ca is None
    else:
        with pytest.raises(ValueError):
            NetupConfig(**values)
    with pytest.raises(ValueError):
        NetupConfig(**(values | {"allow_plaintext_management": False}))


@pytest.mark.parametrize("mode,verified,tls,expected", [
    ("disabled", True, "ca", False), ("verified", False, "ca", False),
    ("verified", True, None, False), ("verified", True, "ca", True),
])
def test_release_requires_both_human_memo_verification_and_tls(mode, verified, tls, expected):
    settings = SimpleNamespace(subscriber_cabinet_mode=mode,
                               subscriber_cabinet_memo_verified=verified, app_env="production")
    assert password_enabled(settings, SimpleNamespace(tls_ca=tls, purpose="production")) is expected
    assert not password_enabled(settings, SimpleNamespace(tls_ca=tls, purpose="lab"))


@pytest.mark.parametrize("origin,env,allowed", [
    ("https://oss.example.test", "dev", False),
    ("https://oss.example.test:8443", "dev", False),
    ("https://oss.example.test:9443", "production", False),
    ("https://oss.example.test:9443", "dev", True),
])
def test_dev_preview_cannot_run_on_live_origin_or_production_env(origin, env, allowed):
    from pydantic import ValidationError

    from app.core.config import Settings

    values = dict(app_env=env, subscriber_cabinet_mode="dev_preview",
                  postgres_user="synthetic", postgres_password="synthetic",
                  auth_hash_key="synthetic-key-that-is-at-least-32-bytes",
                  auth_public_origin=origin)
    if allowed:
        assert Settings(**values).subscriber_cabinet_mode == "dev_preview"
    else:
        with pytest.raises(ValidationError):
            Settings(**values)
