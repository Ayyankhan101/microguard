from setuptools import find_packages, setup

setup(
    name="microguard",
    version="2.0.0",
    description="CLI bot traffic audit tool powered by micrograd",
    author="Microguard",
    license="MIT",
    packages=find_packages(),
    # The built dashboard SPA ships inside the package, so `microguard
    # dashboard` works from a plain pip install with no Node involved.
    package_data={"microguard": ["dashboard/static/*", "dashboard/static/**/*"]},
    include_package_data=True,
    python_requires=">=3.10",
    install_requires=[
        "micrograd",
    ],
    extras_require={
        "live": ["redis>=5.0,<6"],
        "fastapi": ["fastapi>=0.110", "uvicorn>=0.29"],
        "dashboard": [
            "fastapi>=0.110",
            "uvicorn>=0.29",
            "python-multipart>=0.0.9",
            "sse-starlette>=2.1",
        ],
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
