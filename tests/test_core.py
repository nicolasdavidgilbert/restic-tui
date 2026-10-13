import asyncio
import os
import stat

import pytest

from restic_tui.core import remote, restic
from restic_tui.core.devices import parse_lsblk
from restic_tui.core.passwords import generate_password, validate_new_password
from restic_tui.core.profiles import Profile, load_last, save_last


def test_last_roundtrip_and_permissions(tmp_path):
    path = tmp_path / "cfg" / "last.json"
    assert load_last(path) is None
    p = Profile(sources=["/x"], dest_type="sftp", sftp_user="u",
                sftp_host="h", sftp_path="/srv/r")
    save_last(p, path)
    assert load_last(path) == p
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert "password" not in path.read_text().lower()


def test_parse_lsblk_marks_usb():
    data = {"blockdevices": [
        {"name": "sda", "rm": False, "hotplug": False, "tran": "sata", "children": [
            {"name": "sda1", "mountpoint": "/", "size": "100G"},
            {"name": "sda2", "mountpoint": "/boot/efi"}]},
        {"name": "sdb", "rm": True, "tran": "usb", "children": [
            {"name": "sdb1", "label": "PENDRIVE", "mountpoint": "/media/n/PENDRIVE",
             "size": "32G", "fstype": "vfat"}]},
    ]}
    devs = parse_lsblk(data)
    assert [d.mountpoint for d in devs] == ["/media/n/PENDRIVE", "/"]
    assert devs[0].removable and not devs[1].removable


@pytest.mark.parametrize("profile,expected", [
    (Profile(name="l", local_path="/mnt/repo"), "/mnt/repo"),
    (Profile(name="s", dest_type="sftp", sftp_user="nico", sftp_host="nas.local",
             sftp_port=2222, sftp_path="/srv/r"), "sftp://nico@nas.local:2222//srv/r"),
    (Profile(name="s2", dest_type="sftp", sftp_user="nico", sftp_host="10.0.0.2",
             sftp_path="backups"), "sftp://nico@10.0.0.2:22/backups"),
    (Profile(name="r", dest_type="rest", rest_url="http://h:8000/repo"),
     "rest:http://h:8000/repo"),
])
def test_repo_url(profile, expected):
    assert remote.repo_url(profile) == expected


def test_rest_password_only_in_memory_and_masked():
    p = Profile(name="r", dest_type="rest", rest_url="https://h:8000/x", rest_user="u")
    url = remote.repo_url(p, "p@ss/w")
    assert url == "rest:https://u:p%40ss%2Fw@h:8000/x"
    assert "p%40ss" not in restic.mask_url(url)
    assert "p%40ss" not in repr(restic.Repo(url, "pw"))


@pytest.mark.parametrize("host", ["-oProxyCommand=x", "a b", "", "h;rm"])
def test_ssh_validation_rejects_injection(host):
    with pytest.raises(remote.ConfigError):
        remote.validate_ssh("u", host, 22)


def test_rest_url_rejects_inline_credentials():
    with pytest.raises(remote.ConfigError):
        remote.validate_rest_url("http://u:p@h/x")


def test_passwords():
    assert len(generate_password()) >= 24
    assert validate_new_password("a", "b")
    assert validate_new_password("short", "short")
    assert validate_new_password("x" * 12, "x" * 12) is None


def test_probe_and_browse(tmp_path):
    async def go():
        src = tmp_path / "src"
        (src / "sub").mkdir(parents=True)
        (src / "sub" / "f.txt").write_text("x")
        url = str(tmp_path / "repo")
        assert await restic.probe(url) is False
        repo = restic.Repo(url, "correct-horse")
        await restic.init(repo)
        assert await restic.probe(url) is True
        with pytest.raises(restic.ResticError, match="incorrecta"):
            await restic.verify_password(restic.Repo(url, "nope"))
        [e async for e in restic.backup(repo, [str(src)])]
        nodes = await restic.ls(repo, "latest", str(src))
        assert [n["name"] for n in nodes] == ["sub"]
        [e async for e in restic.backup(repo, [str(src)], tags=["t"])]
        out = await restic.forget(repo, {"last": 1}, dry_run=True)
        assert "remove 1 snapshots" in out or "remove" in out
        assert len(await restic.snapshots(repo)) == 2  # dry-run no borra
        await restic.forget(repo, {"last": 1}, prune=True, dry_run=False)
        assert len(await restic.snapshots(repo)) == 1
        with pytest.raises(restic.ResticError):
            await restic.forget(repo, {}, dry_run=True)
    asyncio.run(go())


def test_backup_cancel_releases_lock(tmp_path):
    async def go():
        src = tmp_path / "big"
        src.mkdir()
        for i in range(200):
            (src / f"f{i}").write_bytes(os.urandom(200_000))
        repo = restic.Repo(str(tmp_path / "repo"), "pw-pw-pw-pw")
        await restic.init(repo)
        gen = restic.backup(repo, [str(src)])
        await gen.__anext__()
        await gen.aclose()
        # Si el lock quedara colgado, este backup fallaría.
        events = [e async for e in restic.backup(repo, [str(src)])]
        assert events[-1]["message_type"] == "summary"
    asyncio.run(go())


def test_sftp_uses_only_dedicated_key(monkeypatch, tmp_path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_ed25519.pub").write_text("ssh-ed25519 AAAA test")
    monkeypatch.setattr(remote.Path, "home", lambda: tmp_path)
    p = Profile(dest_type="sftp", sftp_user="u", sftp_host="h", sftp_path="/r")
    assert remote.restic_args(p) == [
        "-o", f"sftp.args=-i {tmp_path}/.ssh/id_ed25519 -o IdentitiesOnly=yes"]
    assert remote.restic_args(Profile(local_path="/x")) == []


def test_ssh_errors_are_explained():
    msg = remote._explain_ssh_error(
        "kex_exchange_identification: read: Connection reset by peer", 255)
    assert "fail2ban" in msg
    assert "configurar acceso SSH" in remote._explain_ssh_error("Permission denied", 255)
