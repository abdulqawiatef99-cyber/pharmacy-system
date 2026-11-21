"""Trial #3 (#38): the installer stops the running program itself, and only the program.

Inno Setup's [Code] runs only on Windows (checked there by the "Windows installer" workflow, which
compiles it, and on the pharmacy PC). These checks keep its safety rules from drifting."""
import re
import unittest
from pathlib import Path

ISS = (Path(__file__).resolve().parent.parent / "installer" / "pharmacy.iss").read_text(encoding="utf-8")
CODE = ISS[ISS.index("[Code]"):]


class TestInstallerStopsServer(unittest.TestCase):
    def test_stops_before_install_and_uninstall(self):
        self.assertRegex(CODE, r"function PrepareToInstall\(var NeedsRestart: Boolean\): String;")
        # uninstall: only after the removal was confirmed (InitializeUninstall runs before the question)
        self.assertNotRegex(CODE, r"function InitializeUninstall")
        self.assertRegex(CODE, r"procedure CurUninstallStepChanged\(CurUninstallStep: TUninstallStep\);\s+begin\s+"
                               r"if CurUninstallStep = usUninstall then\s+begin\s+StopProgram\(False\);")

    def test_only_python_from_the_program_folder(self):
        self.assertIn("Lowercase(AddBackslash(ExpandConstant('{app}')) + 'python\\')", CODE)
        self.assertRegex(CODE, r"Pos\(ProgramPythonDir\(\), Lowercase\(Exe\)\) = 1")
        # every kill is by process id, after the folder check — never by image name (would hit other Python apps)
        self.assertEqual(len(re.findall(r"taskkill", CODE)), 1)
        self.assertIn("'/F /T /PID ' + IntToStr(Pid)", CODE)
        self.assertNotRegex(CODE, r"/IM\b")

    def test_asks_unless_silent_and_refuses_when_another_server_answers(self):
        self.assertIn("not WizardSilent()", CODE)
        self.assertIn("http://127.0.0.1:8000/api/health", CODE)
        self.assertRegex(CODE, r"if not StopAndWait\(\) then\s+Result := ")


class TestInstallerSupportedWindows(unittest.TestCase):
    """Owner 21/11 (part 3): the installer refuses Windows older than 10 and 32-bit Windows (the
    embedded Python is amd64), instead of installing a program that cannot start."""
    SETUP = ISS[ISS.index("[Setup]"):ISS.index("[Languages]")]

    def test_windows_10_or_newer(self):
        self.assertRegex(self.SETUP, r"(?m)^MinVersion=10\.0\s*$")
        self.assertNotRegex(self.SETUP, r"(?m)^OnlyBelowVersion=")

    def test_64_bit_only(self):
        self.assertRegex(self.SETUP, r"(?m)^ArchitecturesAllowed=x64compatible\s*$")
        self.assertRegex(self.SETUP, r"(?m)^ArchitecturesInstallIn64BitMode=x64compatible\s*$")

    def test_embedded_python_is_amd64(self):
        wf = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "windows-installer.yml")
        self.assertIn("-embed-amd64.zip", wf.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
