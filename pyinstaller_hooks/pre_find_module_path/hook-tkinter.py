"""
Custom tkinter pre-find hook.

PyInstaller 6.19 excludes tkinter entirely when Tcl/Tk probing fails in the
build interpreter. Our EXE bundles Tcl/Tk resources manually, so excluding the
stdlib tkinter package is incorrect for this project.
"""


def pre_find_module_path(hook_api):
    # Keep the default search dirs so tkinter from the stdlib can be analyzed.
    return
