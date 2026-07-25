#!/usr/bin/env python3
"""Setup script for bookie - DraftKings odds scanner."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="bookie",
    version="1.3.0",
    description="DraftKings odds scanner with team-strength models",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="selah3435-glitch",
    url="https://github.com/selah3435-glitch/bookie",
    py_modules=["bookie"],
    python_requires=">=3.8",
    install_requires=[
        "pandas>=1.3.0",
        "requests>=2.28.0",
    ],
    entry_points={
        "console_scripts": [
            "bookie=bookie:main",
        ],
    },
)
