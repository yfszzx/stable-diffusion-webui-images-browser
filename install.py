import launch

if not launch.is_installed("send2trash"):
    launch.run_pip("install Send2Trash", "requirement for images-browser")
