"""Developer / build scripts (not runtime code).

Modules here are invoked by the operator (`python3 scripts/foo.py …`) or
imported by tests (`tests/test_mpers_generator.py`). Production services
(`server.py`, coordinators) must not import from this package — anything
they need to call belongs in a proper runtime module instead.
"""
