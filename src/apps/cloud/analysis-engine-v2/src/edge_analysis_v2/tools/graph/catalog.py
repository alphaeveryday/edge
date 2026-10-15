"""Reviewed graph catalog, generated from the ontology definitions and read as-is."""
import hashlib
import json
from pathlib import Path

CATALOG = Path(__file__).with_name('graph-catalog.json')


def load_catalog(path=CATALOG):
    """Return the catalog and the SHA-256 of the exact bytes that were read.

    Args:
        path: Catalog file; the packaged copy by default.

    Returns:
        Catalog dictionary and its hexadecimal digest for run evidence.

    Raises:
        ValueError: The catalog was generated from definitions that differ from the reviewed model.
    """
    data = Path(path).read_bytes()
    catalog = json.loads(data)
    if catalog['modelChanged']:
        raise ValueError('Graph mapping must match the reviewed model')
    return catalog, hashlib.sha256(data).hexdigest()
