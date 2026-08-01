# PyInstaller spec for the standalone mnemo binary.
#
# Build:  pyinstaller tools/mnemo.spec --distpath dist/bin --workpath build/pyi
# Result: dist/bin/mnemo/  (a directory — see below)
#
# ONEDIR, DELIBERATELY NOT ONEFILE.
#
# The UserPromptSubmit hook spawns mnemo on *every* prompt. A onefile build
# re-extracts its whole archive to a temp directory on each launch, which puts
# hundreds of milliseconds on the session's hot path, every prompt, forever.
# onedir starts in tens of milliseconds. The cost is that we ship a directory
# rather than a single file, which is why the launcher unpacks a tarball into
# ${CLAUDE_PLUGIN_DATA} instead of dropping one binary on PATH.
#
# The templates have to be collected explicitly: they are package *data*, and
# scaffold.py reads them through importlib.resources at `mnemo init` time, so
# a build without them produces a binary that installs a broken vault.

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("mnemo.templates")

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # Command modules register themselves via the @command decorator at
        # import time, reached only through mnemo.cli.commands.__init__.
        # PyInstaller's static analysis follows that, but the hook modules are
        # dispatched by name through importlib in cli/commands/hook.py, which
        # it cannot see.
        "mnemo.hooks.session_start",
        "mnemo.hooks.user_prompt_submit",
        "mnemo.hooks.pre_tool_use",
        "mnemo.hooks.session_end",
    ],
    hookspath=[],
    runtime_hooks=[],
    # Nothing in mnemo imports these; excluding them keeps the bundle small.
    excludes=["tkinter", "unittest", "pydoc", "doctest", "pdb"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mnemo",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="mnemo",
)
