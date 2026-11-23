"""核心逻辑测试：python -m unittest discover -s tests -t ."""

import asyncio
import json
import tempfile
import unittest
from unittest import mock

from backend.api import install, search
from backend.config import loader
from backend.installers import linux as linux_module
from backend.installers.base import decode_output
from backend.installers.linux import LinuxInstaller

WINGET_TABLE = """\
名称                                                               ID                                             版本                   匹配                                      源
----------------------------------------------------------------------------------------------------------------------------------------------------------------
Git                                                                Git.Git                                        2.56.0.4                                      winget
OpenCherry Desktop                                                 4nrry.OpenCherryDesktop                        0.0.1                  Tag: git               winget
"""


class WingetParserTest(unittest.TestCase):
    def test_parse_table(self):
        rows = search._parse_winget_table(WINGET_TABLE)
        self.assertEqual([r["id"] for r in rows], ["Git.Git", "4nrry.OpenCherryDesktop"])
        self.assertEqual(rows[0]["version"], "2.56.0.4")
        self.assertEqual(rows[1]["version"], "0.0.1")

    def test_name_normalization_and_aggregation(self):
        self.assertTrue(search._is_version_token("11"))
        self.assertTrue(search._is_version_token("v1.0.77"))
        self.assertFalse(search._is_version_token("python"))
        self.assertEqual(search._normalize_name("Java 17"), "java")

        merged = search._aggregate_results([
            {"name": "Java 17", "id": "a", "version": "17", "source": "winget"},
            {"name": "Java 21", "id": "b", "version": "21", "source": "winget"},
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual([v["version"] for v in merged[0]["variants"]], ["17", "21"])


class CustomCommandTest(unittest.TestCase):
    def test_linux_apt_command_keeps_version(self):
        request = install.CustomInstallRequest(package_id="git", version="1:2.43")
        with mock.patch.object(install, "current_platform", return_value="linux"), \
                mock.patch.object(install, "detect_package_manager", return_value="apt"):
            command = install._build_custom_install_command(request)
        self.assertEqual(command, ["sudo", "apt-get", "install", "-y", "git=1:2.43"])

    def test_windows_winget_command_keeps_version_and_path(self):
        request = install.CustomInstallRequest(
            package_id="Git.Git", version="2.56", install_path="C:\\Tools"
        )
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(install, "current_platform", return_value="windows"), \
                mock.patch.object(install, "DOWNLOAD_DIR", tmp), \
                mock.patch.object(install, "_ensure_winget_download_dir"):
            command = install._build_custom_install_command(request)
        self.assertIn("--version", command)
        self.assertIn("2.56", command)
        self.assertIn("--location", command)
        self.assertIn("C:\\Tools", command)


class ConfigValidationTest(unittest.TestCase):
    def test_real_config_is_valid(self):
        self.assertTrue(loader.load_tools_config()["tools"])

    def test_duplicate_tool_id_rejected(self):
        config = json.loads(json.dumps(loader.load_tools_config()))
        config["tools"].append(dict(config["tools"][0]))
        with self.assertRaisesRegex(ValueError, "id 重复"):
            loader._validate(config)

    def test_unknown_category_rejected(self):
        config = json.loads(json.dumps(loader.load_tools_config()))
        config["tools"][0]["category"] = "does-not-exist"
        with self.assertRaisesRegex(ValueError, "分类不存在"):
            loader._validate(config)


class InstallManagerTest(unittest.TestCase):
    def test_prunes_old_finished_tasks(self):
        manager = install.InstallManager()
        for _ in range(install.MAX_TASKS + 5):
            task = manager.create_task()
            task["status"] = "done"
        self.assertLessEqual(len(manager.tasks), install.MAX_TASKS)

    def test_log_is_capped(self):
        manager = install.InstallManager()
        task = manager.create_task()

        async def emit_many():
            for index in range(install.MAX_LOG_LINES + 10):
                await manager.emit(task["task_id"], "info", str(index))

        asyncio.run(emit_many())
        self.assertEqual(len(task["logs"]), install.MAX_LOG_LINES)
        self.assertEqual(task["logs"][-1]["message"], str(install.MAX_LOG_LINES + 9))


class CancelTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_queued_task_finishes_it(self):
        manager = install.InstallManager()
        task = manager.create_task(tool_ids=["x"])
        handle = asyncio.create_task(asyncio.sleep(60))
        manager.handles[task["task_id"]] = handle
        with mock.patch.object(install, "manager", manager):
            result = await install.cancel_task(task["task_id"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["logs"][-1]["event"], "done")
        handle.cancel()

    async def test_cancel_running_package_install_rejected(self):
        manager = install.InstallManager()
        task = manager.create_task(package="x")
        task["status"] = "running"
        with mock.patch.object(install, "manager", manager):
            with self.assertRaises(install.HTTPException) as ctx:
                await install.cancel_task(task["task_id"])
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_cancel_unknown_task_404(self):
        with self.assertRaises(install.HTTPException) as ctx:
            await install.cancel_task("missing")
        self.assertEqual(ctx.exception.status_code, 404)


class DecodeOutputTest(unittest.TestCase):
    def test_utf8(self):
        self.assertEqual(decode_output("安装".encode("utf-8")), "安装")

    def test_gbk_fallback(self):
        self.assertEqual(decode_output("安装".encode("gbk")), "安装")


class _FakeProcess:
    def __init__(self, output: bytes, returncode: int = 0) -> None:
        self._output = output
        self.returncode = returncode

    async def communicate(self):
        return self._output, b""


class LinuxPackageCheckTest(unittest.IsolatedAsyncioTestCase):
    """dpkg-query 对已卸载但残留配置的包也返回 0，必须看状态文本。"""

    async def _check(self, output: bytes) -> bool:
        installer = LinuxInstaller()
        installer.package_manager = "apt"
        with mock.patch.object(
            linux_module.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=_FakeProcess(output)),
        ):
            return await installer.check_installed({"linux": {"apt": "foo"}})

    async def test_installed(self):
        self.assertTrue(await self._check(b"install ok installed"))

    async def test_removed_but_config_files_is_not_installed(self):
        self.assertFalse(await self._check(b"deinstall ok config-files"))


if __name__ == "__main__":
    unittest.main()
