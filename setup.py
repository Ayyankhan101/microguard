from setuptools import setup, find_packages

setup(
    name="microguard",
    version="0.1.0",
    description="CLI bot traffic audit tool powered by micrograd",
    author="Microguard",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "micrograd",
    ],
    entry_points={
        "console_scripts": [
            "microguard=microguard.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Security",
    ],
)
