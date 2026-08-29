# ruff: noqa: I001
import subprocess

def check(value: str) -> None:
    subprocess.run("echo " + value, shell=True, check=False)
