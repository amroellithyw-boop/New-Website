"""ForgeOS core: an evidence-first finance operating system.

Layering, outermost first:

* ``connectors``  - read-only adapters that produce raw payloads
* ``vault``       - immutable storage of those payloads with checksums
* ``normalize``   - raw payloads mapped to canonical records with lineage
* ``canonical``   - the Forge financial model every other layer agrees on
* ``engine``      - deterministic accounting math (the calculator of record)
* ``rules``       - versioned controls that turn math into findings
* ``evidence``    - evidence packets that make a finding reproducible
* ``workitems``   - the state machine, risk scoring and review routing
* ``agents``      - model-backed preparers and reviewers, under contract
* ``bench``       - ForgeBench: seeded cases, metrics and regression gates
* ``report``      - the client-facing diagnostic

The dependency arrow only ever points inward. Nothing in ``engine`` or below
knows that a language model exists.
"""

__version__ = "0.1.0"
