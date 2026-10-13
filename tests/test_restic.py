import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

from restic_tui.core import restic

HAS_RESTIC = shutil.which("restic") is not None


@unittest.skipUnless(HAS_RESTIC, "restic no instalado")
class ResticFlow(unittest.TestCase):
    def test_init_backup_restore(self):
        async def go():
            with tempfile.TemporaryDirectory() as d:
                d = Path(d)
                src = d / "src"; src.mkdir()
                (src / "a.txt").write_text("hola")
                repo = restic.Repo(str(d / "repo"), "pw-test")
                self.assertFalse(await restic.repo_exists(repo))
                await restic.init(repo)
                self.assertTrue(await restic.repo_exists(repo))
                events = [e async for e in restic.backup(repo, [str(src)])]
                self.assertEqual(events[-1]["message_type"], "summary")
                (src / "b.txt").write_text("nuevo")
                events = [e async for e in restic.backup(repo, [str(src)])]
                self.assertEqual(events[-1]["files_new"], 1)  # incremental
                snaps = await restic.snapshots(repo)
                self.assertEqual(len(snaps), 2)
                out = d / "out"
                await restic.restore(repo, "latest", str(out))
                self.assertEqual((out / str(src).lstrip("/") / "b.txt").read_text(), "nuevo")
                with self.assertRaises(restic.ResticError):
                    await restic.repo_exists(restic.Repo(repo.url, "mala"))
        asyncio.run(go())

    def test_password_not_in_repr(self):
        self.assertNotIn("secreto", repr(restic.Repo("x", "secreto")))


if __name__ == "__main__":
    unittest.main()
