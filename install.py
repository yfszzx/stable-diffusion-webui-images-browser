import launch

if not launch.is_installed("Send2Trash"):
    launch.run_pip("install Send2Trash", "requirement for images-browser")
