# Copyright The OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# type: ignore

import logging
import tempfile
from contextlib import contextmanager
from io import StringIO
from os import chdir, getcwd
from pathlib import Path
from random import sample
from unittest import TestCase
from unittest.mock import call, patch

from opentelemetry.instrumentation import bootstrap
from opentelemetry.instrumentation.bootstrap_gen import (
    default_instrumentations,
    libraries,
)


def sample_packages(packages, rate):
    return sample(
        list(packages),
        int(len(packages) * rate),
    )


@contextmanager
def change_dir(dirname):
    cwd = getcwd()
    try:
        chdir(dirname)
        yield
    finally:
        chdir(cwd)


class TestBootstrap(TestCase):
    installed_libraries = {}
    installed_instrumentations = {}

    @classmethod
    def setUpClass(cls):
        cls.installed_libraries = sample_packages(
            [lib["instrumentation"] for lib in libraries], 0.6
        )

        # treat 50% of sampled packages as pre-installed
        cls.installed_instrumentations = sample_packages(
            cls.installed_libraries, 0.5
        )

        cls.pkg_patcher = patch(
            "opentelemetry.instrumentation.bootstrap._find_installed_libraries",
            return_value=cls.installed_libraries,
        )

        cls.pip_install_patcher = patch(
            "opentelemetry.instrumentation.bootstrap._sys_pip_install",
        )
        cls.pip_check_patcher = patch(
            "opentelemetry.instrumentation.bootstrap._pip_check",
        )

    def setUp(self):
        super().setUp()
        self.mock_pip_check = self.pip_check_patcher.start()
        self.mock_pip_install = self.pip_install_patcher.start()

    def tearDown(self):
        super().tearDown()
        self.pip_check_patcher.stop()
        self.pip_install_patcher.stop()

    @patch("sys.argv", ["bootstrap", "-a", "pipenv"])
    def test_run_unknown_cmd(self):
        with self.assertRaises(SystemExit):
            bootstrap.run()

    @patch("sys.argv", ["bootstrap", "-a", "requirements"])
    def test_run_cmd_print(self):
        self.pkg_patcher.start()
        with patch("sys.stdout", new=StringIO()) as fake_out:
            bootstrap.run()
            self.assertEqual(
                fake_out.getvalue(),
                "\n".join(self.installed_libraries) + "\n",
            )
        self.pkg_patcher.stop()

    @patch("sys.argv", ["bootstrap", "-a", "install"])
    def test_run_cmd_install(self):
        self.pkg_patcher.start()
        bootstrap.run()
        self.mock_pip_install.assert_has_calls(
            [call(i) for i in self.installed_libraries],
            any_order=True,
        )
        self.mock_pip_check.assert_called_once()
        self.pkg_patcher.stop()

    @patch("sys.argv", ["bootstrap", "-a", "install"])
    def test_can_override_available_libraries(self):
        bootstrap.run(libraries=[])
        self.mock_pip_install.assert_has_calls(
            [call(i) for i in default_instrumentations],
            any_order=True,
        )
        self.mock_pip_check.assert_called_once()

    @patch("sys.argv", ["bootstrap", "-a", "install"])
    def test_can_override_available_default_instrumentations(self):
        with patch(
            "opentelemetry.instrumentation.bootstrap._is_installed",
            return_value=True,
        ):
            bootstrap.run(default_instrumentations=[])
        self.mock_pip_install.assert_has_calls(
            [call(i) for i in self.installed_libraries],
            any_order=True,
        )
        self.mock_pip_check.assert_called_once()

    @patch("sys.argv", ["bootstrap", "-a", "requirements", "-q"])
    def test_quiet(self):
        with patch(
            "opentelemetry.instrumentation.bootstrap.logger"
        ) as mock_logger:
            bootstrap.run(libraries=[])
        mock_logger.setLevel.assert_called_once_with(logging.ERROR)

    @patch("sys.argv", ["bootstrap", "-r", "requirements.txt"])
    def test_requirements_parser(self):
        requirements_file_contents = """
            # This is a comment, to show how #-prefixed lines are ignored.
            # It is possible to specify requirements as plain names.
            pytest
            pytest-cov
            beautifulsoup4

            # The syntax supported here is the same as that of requirement specifiers.
            docopt == 0.6.1
            requests [security] >= 2.8.1, == 2.8.* ; python_version < "2.7"
            urllib3 @ https://github.com/urllib3/urllib3/archive/refs/tags/1.26.8.zip

            # It is possible to refer to other requirement files or constraints files.
            -r other-requirements.txt
            --constraint constraints.txt

            # It is possible to refer to specific local distribution paths.
            ./downloads/numpy-1.9.2-cp34-none-win32.whl

            # It is possible to refer to URLs.
            http://wxpython.org/Phoenix/snapshot-builds/wxPython_Phoenix-3.0.3.dev1820+49a8884-cp34-none-win_amd64.whl
        """
        with tempfile.TemporaryDirectory() as tempdirname:
            (Path(tempdirname) / "requirements.txt").write_text(
                requirements_file_contents
            )
            with self.assertLogs(level="WARNING") as logs:
                with change_dir(tempdirname):
                    bootstrap.run()
            print(logs.output)
            self.assertEqual(len(logs.records), 6)
            self.assertEqual(
                logs.records[0].message.strip(),
                "ignoring argument on line 14 in requirements.txt",
            )
            self.assertEqual(
                logs.records[1].message.strip(),
                "ignoring argument on line 15 in requirements.txt",
            )
            self.assertEqual(
                logs.records[2].message.strip(),
                "ignoring requirement on line 18 in requirements.txt",
            )
            self.assertEqual(
                logs.records[3].message.strip(),
                "ignoring requirement on line 21 in requirements.txt",
            )
            self.assertEqual(
                logs.records[4].message.strip(),
                "instrumentation for package requests is available but a specific version was not specified in the requirements. Skipping.",
            )
            self.assertEqual(
                logs.records[5].message.strip(),
                "instrumentation for package urllib3 is available but a specific version was not specified in the requirements. Skipping.",
            )

    @patch(
        "sys.argv",
        ["bootstrap", "-a", "requirements", "-r", "requirements.txt"],
    )
    def test_requirements(self):
        with tempfile.TemporaryDirectory() as tempdirname:
            (Path(tempdirname) / "requirements.txt").write_text(
                "celery == 5.0.0"
            )
            with change_dir(tempdirname):
                with patch("sys.stdout", new=StringIO()) as fake_out:
                    bootstrap.run()
        self.assertIn(
            "celery",
            fake_out.getvalue(),
        )

    @patch("sys.argv", ["bootstrap", "-a", "requirements", "-r", "-"])
    def test_requirements_stdin(self):
        with (
            patch("sys.stdout", new=StringIO()) as fake_out,
            patch("sys.stdin", new=StringIO("celery == 5.0.0")),
        ):
            bootstrap.run()
        self.assertIn(
            "celery",
            fake_out.getvalue(),
        )
