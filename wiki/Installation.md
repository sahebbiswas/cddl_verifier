# Installation

## Prerequisites

- Python 3.7 or higher

## Install dependencies

Using pip:

```bash
pip install cbor2
```

Or using the bundled requirements file (if present):

```bash
pip install -r requirements.txt
```

## Notes

This project is a single-file tool (`cbor_cddl_analyzer.py`) that depends on `cbor2` for CBOR parsing. If you plan to edit or extend the code, create a virtual environment first:

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
```
