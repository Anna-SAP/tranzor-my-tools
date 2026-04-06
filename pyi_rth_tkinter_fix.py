import os
import sys


def _set_tk_environment():
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return

    tcl_dir = os.path.join(meipass, "_tcl_data")
    tk_dir = os.path.join(meipass, "_tk_data")
    tcl_module_dir = os.path.join(meipass, "tcl8")

    if os.path.isdir(tcl_dir):
        os.environ["TCL_LIBRARY"] = tcl_dir
    if os.path.isdir(tk_dir):
        os.environ["TK_LIBRARY"] = tk_dir
    if os.path.isdir(tcl_module_dir):
        os.environ["TCLLIBPATH"] = tcl_module_dir


_set_tk_environment()
