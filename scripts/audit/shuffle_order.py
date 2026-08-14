"""Audit-only pytest plugin: shuffle collected test order with an explicit seed.

Neither pytest-randomly nor pytest-random-order is installed in this project's
venv, and the audit brief forbids installing anything into that shared venv.
This plugin provides the equivalent order randomisation locally: load it with
``-p shuffle_order`` and set ``AUDIT_SHUFFLE_SEED`` to an integer.

Shuffling happens across the whole collected list, so tests are torn out of
their file and class groupings -- a strictly stronger check than permuting the
order of the test files on the command line.
"""

import os
import random


def pytest_collection_modifyitems(session, config, items):
    seed = os.environ.get("AUDIT_SHUFFLE_SEED")
    if seed is None:
        return
    rng = random.Random(int(seed))
    rng.shuffle(items)
    print(f"\n[audit] shuffled {len(items)} tests with seed {seed}")
