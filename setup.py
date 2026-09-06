from setuptools import find_packages, setup

setup(
    name="microguard",
    version="2.0.0",
    description="CLI bot traffic audit tool powered by micrograd",
    author="Microguard",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "micrograd",
    ],
    extras_require={
        "live": ["redis>=5.0,<6"],
        "fastapi": ["fastapi>=0.110", "uvicorn>=0.29"],
        "flask": ["flask>=3.0"],
    },
    entry_points={
        "console_scripts": [
            "microguard=microguard.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Security",
    ],
)
