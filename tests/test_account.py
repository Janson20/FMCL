"""账号系统单元测试"""

import json
import os
import tempfile
import uuid
from pathlib import Path

import pytest

from launcher.account import (
    Account,
    AccountType,
    GlobalAccountSystem,
    create_offline_account,
    init_account_system,
    get_account_system,
    AuthlibInjectorManager,
)


class TestAccount:
    def test_create_offline_account(self):
        acc = create_offline_account("TestPlayer")
        assert acc.name == "TestPlayer"
        assert acc.account_type == AccountType.OFFLINE
        assert acc.uuid is not None
        assert len(acc.uuid) > 0
        assert acc.id is not None
        assert acc.access_token is None
        assert acc.refresh_token is None

    def test_offline_uuid_deterministic(self):
        a1 = create_offline_account("Player1")
        a2 = create_offline_account("Player1")
        assert a1.uuid == a2.uuid
        assert a1.id != a2.id

    def test_offline_uuid_different(self):
        a1 = create_offline_account("Player1")
        a2 = create_offline_account("Player2")
        assert a1.uuid != a2.uuid

    def test_account_to_dict_and_from_dict(self):
        acc = Account(
            id="test-id",
            name="TestUser",
            account_type=AccountType.MICROSOFT,
            uuid="test-uuid-1234",
            access_token="secret-token",
            refresh_token="refresh-token",
        )
        d = acc.to_dict()
        assert d["id"] == "test-id"
        assert d["name"] == "TestUser"
        assert d["account_type"] == "microsoft"
        assert d["uuid"] == "test-uuid-1234"
        assert "access_token" in d

        restored = Account.from_dict(d)
        assert restored.id == acc.id
        assert restored.name == acc.name
        assert restored.account_type == acc.account_type
        assert restored.uuid == acc.uuid

    def test_account_from_dict_no_token(self):
        d = {
            "id": "test-id",
            "name": "OfflineUser",
            "account_type": "offline",
            "uuid": "offline-uuid",
        }
        acc = Account.from_dict(d)
        assert acc.name == "OfflineUser"
        assert acc.account_type == AccountType.OFFLINE
        assert acc.access_token is None

    def test_account_display_name(self):
        acc = Account(
            id="test", name="Steve", account_type=AccountType.MICROSOFT
        )
        assert "Steve" in acc.display_name

    def test_is_token_expired_no_token(self):
        acc = Account(
            id="test", name="Steve", account_type=AccountType.MICROSOFT
        )
        assert acc.is_token_expired()


class TestGlobalAccountSystem:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    def test_init_empty(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        assert len(system.accounts) == 0
        assert system.current_account is None

    def test_add_offline_account(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        acc = system.offline_login("TestPlayer")
        assert acc is not None
        assert acc.name == "TestPlayer"
        assert len(system.accounts) == 1
        assert system.current_account is not None
        assert system.current_account.id == acc.id

    def test_add_duplicate_offline(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        a1 = system.offline_login("TestPlayer")
        a2 = system.offline_login("TestPlayer")
        assert len(system.accounts) == 2
        assert a1.id != a2.id

    def test_set_current_account(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        a1 = system.offline_login("Player1")
        a2 = system.offline_login("Player2")
        assert system.current_account.id == a2.id
        system.set_current_account(a1.id)
        assert system.current_account.id == a1.id

    def test_set_current_invalid(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        result = system.set_current_account("nonexistent")
        assert result is False

    def test_remove_account(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        a1 = system.offline_login("Player1")
        system.offline_login("Player2")
        assert len(system.accounts) == 2
        result = system.remove_account(a1.id)
        assert result is True
        assert len(system.accounts) == 1

    def test_remove_current_account(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        a1 = system.offline_login("Player1")
        system.remove_account(a1.id)
        assert system.current_account is None

    def test_get_by_type(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        system.offline_login("Player1")
        system.offline_login("Player2")
        offline = system.get_accounts_by_type(AccountType.OFFLINE)
        assert len(offline) == 2

    def test_get_account_by_name(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        system.offline_login("Player1")
        acc = system.get_account_by_name("Player1")
        assert acc is not None
        assert acc.name == "Player1"

    def test_build_launch_options_offline(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        system.offline_login("Player1")
        opts = system.build_launch_options()
        assert opts["username"] == "Player1"
        assert "uuid" in opts
        assert "token" not in opts

    def test_persistence(self, temp_dir):
        system1 = GlobalAccountSystem(temp_dir)
        system1.offline_login("PersistentPlayer")
        acc_id = system1.current_account.id

        system2 = GlobalAccountSystem(temp_dir)
        assert len(system2.accounts) == 1
        assert system2.current_account is not None
        assert system2.current_account.id == acc_id

    def test_add_microsoft_account(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        acc = Account(
            id=str(uuid.uuid4()),
            name="MSPlayer",
            account_type=AccountType.MICROSOFT,
            uuid="ms-uuid-123",
            access_token="ms-token",
            refresh_token="ms-refresh",
        )
        system.add_account(acc)
        system.set_current_account(acc.id)
        opts = system.build_launch_options()
        assert opts["username"] == "MSPlayer"
        assert opts["token"] == "ms-token"

    def test_add_yggdrasil_account(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        acc = Account(
            id=str(uuid.uuid4()),
            name="YggPlayer",
            account_type=AccountType.YGGDRASIL,
            uuid="ygg-uuid-123",
            access_token="ygg-token",
            yggdrasil_server_url="https://example.com/api/yggdrasil",
        )
        system.add_account(acc)
        system.set_current_account(acc.id)
        opts = system.build_launch_options()
        assert opts["username"] == "YggPlayer"
        assert opts["token"] == "ygg-token"

    def test_export_import_accounts(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        system.offline_login("Player1")
        system.offline_login("Player2")

        password = "test-export-password"
        data = system.export_accounts(password)
        assert data is not None
        assert data.startswith(b"FMCL_ACCOUNTS_V1\n")

        system2 = GlobalAccountSystem(temp_dir)
        system2.offline_login("ExistingPlayer")
        assert len(system2.accounts) >= 1

        result = system2.import_accounts(password, data)
        assert result > 0
        assert len(system2.accounts) >= 2

    def test_import_wrong_password(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        system.offline_login("Player1")
        data = system.export_accounts("correct-password")

        result = system.import_accounts("wrong-password", data)
        assert result == -1

    def test_import_invalid_data(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        result = system.import_accounts("password", b"invalid-data")
        assert result == -1

    def test_export_empty_password(self, temp_dir):
        system = GlobalAccountSystem(temp_dir)
        data = system.export_accounts("")
        assert data is None


class TestAuthlibInjector:
    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    def test_init_not_installed(self, temp_dir):
        mgr = AuthlibInjectorManager(temp_dir)
        assert not mgr.is_installed
        assert mgr.jar_path.endswith(".jar")


class TestGlobalInit:
    def test_init_account_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            system = init_account_system(base)
            assert system is not None
            assert len(system.accounts) == 0

            retrieved = get_account_system()
            assert retrieved is not None
            assert retrieved is system
